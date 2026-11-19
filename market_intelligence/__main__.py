"""Operator commands for TrendRadar market intelligence."""

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def check_sources():
    import yaml
    from trendradar.crawler.rss import RSSFetcher

    config = yaml.safe_load(Path(os.environ.get("CONFIG_PATH", "config/config.yaml")).read_text())
    feeds = [dict(feed) for feed in config["rss"]["feeds"]
             if feed.get("category") and feed.get("enabled", True)]
    proxy = os.environ.get("MARKET_INTERNATIONAL_PROXY")
    for feed in feeds:
        if proxy and feed["category"].startswith("international_"):
            feed["proxy_url"] = proxy
    fetcher = RSSFetcher.from_config({"feeds": feeds, "timeout": 12, "request_interval": 200})
    start = time.monotonic()
    result = fetcher.fetch_all()
    groups = config.get("market_intelligence", {}).get("digest", {}).get("publisher_groups", {})
    publishers = {feed: name for name, feeds in groups.items() for feed in feeds}
    verified = {"domestic": set(), "international": set()}
    for source in feeds:
        items = result.items.get(source["id"], [])
        info = fetcher.health[source["id"]]
        if items and info["success"]:
            region = source["category"].split("_", 1)[0]
            verified[region].add(publishers.get(source["id"], source["id"]))
        print(json.dumps({"id": source["id"], "count": len(items),
                          "success": info["success"], "duration_ms": info["duration_ms"],
                          "latest_published_at": info["latest_published_at"]}, ensure_ascii=False))
    counts = {region: len(names) for region, names in verified.items()}
    print(json.dumps({"distinct_publishers": counts, "elapsed_seconds": round(time.monotonic() - start, 1)}))
    return 0 if min(counts.values()) >= 5 else 1


def health(database):
    path = Path(database).resolve()
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("""SELECT feed_id, category, last_attempt, last_success, attempts,
            successes, round(100.0*successes/attempts,2) AS success_percent, consecutive_failures,
            duration_ms, latest_published_at
            FROM feed_health ORDER BY feed_id"""):
            print(json.dumps(dict(row), ensure_ascii=False))
        for label, query in {
            "articles": "SELECT analysis_state, count(*) FROM articles GROUP BY analysis_state",
            "events": "SELECT priority, count(*) FROM events GROUP BY priority",
            "deliveries": "SELECT state, count(*) FROM deliveries GROUP BY state",
        }.items():
            print(json.dumps({label: dict(db.execute(query))}))
    return 0


def trading_command(command, slot):
    # Loader progress belongs in container logs, never in the generated crontab.
    output = io.StringIO() if command == "schedule-tick" else sys.stderr
    with redirect_stdout(output):
        from trendradar.core import load_config

        config = load_config()
    settings = config["MARKET_INTELLIGENCE"]
    if command == "digest-cron":
        from .trading_calendar import cron_entries

        for entry in cron_entries(settings, live_schedule=config.get("MARKET_LIVE_SCHEDULE", False)):
            print(entry)
    elif settings.get("enabled"):
        from .engine import MarketIntelligence
        from .status import publish_current

        jobs = ()
        if command == "schedule-tick":
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from .trading_calendar import due_jobs

            now = datetime.now(ZoneInfo(settings.get("timezone", "Asia/Shanghai")))
            jobs = tuple(due_jobs(settings, now))
            if not jobs:
                return 0

        engine = MarketIntelligence(settings, config)
        try:
            if command == "schedule-tick":
                for kind, due in jobs:
                    if kind == "weekly":
                        engine.dispatch_weekly_calendar()
                    elif kind == "opening":
                        engine.dispatch_market_open()
                    else:
                        if due in settings.get("market_close", {}).get("display_slots", []):
                            engine.collect_market_close()
                        if kind == "trading":
                            engine.dispatch_digest(trading_slot=due)
                        else:
                            engine.dispatch_digest(daily_slot=due)
            elif command == "publish-public-history":
                from .public_site import backfill_delivered

                print(f"Published {backfill_delivered(engine)} existing delivered digests")
            elif command == "weekly-calendar":
                engine.dispatch_weekly_calendar()
            elif command == "market-close":
                engine.collect_market_close()
            elif command == "market-open":
                engine.dispatch_market_open()
            else:
                if slot in settings.get("market_close", {}).get("display_slots", []):
                    engine.collect_market_close()
                engine.dispatch_digest(trading_slot=slot)
            publish_current(engine)
        finally:
            engine.close()
    return 0


def brief_now(send, publish_preview):
    from datetime import timedelta
    from uuid import uuid4

    from . import digests, public_site
    from .engine import MarketIntelligence

    with redirect_stdout(sys.stderr):
        from trendradar.core import load_config

        config = load_config()
    engine = MarketIntelligence(config["MARKET_INTELLIGENCE"], config)
    try:
        now = engine.now()
        key = "digest-manual:" + uuid4().hex
        edition = digests.make_edition(engine, key, now, now - timedelta(hours=24),
                                       "即时热点新闻简报", closing=True)
        origin = os.environ.get("MARKET_DIGEST_BASE_URL", "")
        if send or publish_preview:
            if not origin or not os.environ.get("MARKET_STATUS_PATH"):
                raise SystemExit("Set MARKET_DIGEST_BASE_URL and MARKET_STATUS_PATH first")
        if send:
            wework = engine.notification_status()["wework"]
            if not (wework["enabled"] and wework["configured"]):
                raise SystemExit("WeCom is disabled or missing credentials")
            with redirect_stdout(sys.stderr):
                delivered = engine.send_digest_edition(key, edition, ["wework"])
            if not delivered:
                return 1
        elif publish_preview:
            edition = digests.freeze_edition(engine.db, key, edition)
            digests.archive_edition(os.environ["MARKET_STATUS_PATH"], edition)
        public = public_site.public_origin() if send else ""
        print(digests.digest_message(edition, public or (origin if send or publish_preview else ""),
                                     public=bool(public), max_bytes=3800))
        return 0
    finally:
        engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-sources", "health", "digest-cron", "schedule-tick", "trading-digest", "weekly-calendar", "market-close", "brief-now", "publish-public-history", "market-open"])
    parser.add_argument("--database", default="output/market_intelligence.db")
    parser.add_argument("--slot", help="Configured trading digest time in HH:MM")
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument("--send", action="store_true", help="Send one immediate brief to WeCom")
    delivery.add_argument("--publish-preview", action="store_true", help="Publish a protected preview without sending")
    args = parser.parse_args()
    if args.command == "brief-now":
        return brief_now(args.send, args.publish_preview)
    if args.send or args.publish_preview:
        parser.error("--send and --publish-preview require brief-now")
    if args.command == "trading-digest" and args.slot is None:
        parser.error("trading-digest requires --slot")
    if args.command in {"digest-cron", "schedule-tick", "trading-digest", "weekly-calendar", "market-close", "publish-public-history", "market-open"}:
        return trading_command(args.command, args.slot)
    return check_sources() if args.command == "check-sources" else health(args.database)


if __name__ == "__main__":
    raise SystemExit(main())
