"""Operator commands for TrendRadar market intelligence."""

import argparse
from contextlib import redirect_stdout
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
    counts = {"domestic": 0, "international": 0}
    for source in feeds:
        items = result.items.get(source["id"], [])
        info = fetcher.health[source["id"]]
        if items and info["success"]:
            counts[source["category"].split("_", 1)[0]] += 1
            if source["id"] == "bbc-business" and "bbc-world" in result.items and result.items["bbc-world"]:
                counts["international"] -= 1
        print(json.dumps({"id": source["id"], "count": len(items),
                          "success": info["success"], "duration_ms": info["duration_ms"],
                          "latest_published_at": info["latest_published_at"]}, ensure_ascii=False))
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
    with redirect_stdout(sys.stderr):
        from trendradar.core import load_config

        config = load_config()
    settings = config["MARKET_INTELLIGENCE"]
    if command == "digest-cron":
        from .trading_calendar import cron_entries

        for entry in cron_entries(settings):
            print(entry)
    elif settings.get("enabled"):
        from .engine import MarketIntelligence

        engine = MarketIntelligence(settings, config)
        try:
            if command == "weekly-calendar":
                engine.dispatch_weekly_calendar()
            elif command == "market-close":
                engine.collect_market_close()
            else:
                if slot in settings.get("market_close", {}).get("display_slots", []):
                    engine.collect_market_close()
                engine.dispatch_digest(trading_slot=slot)
        finally:
            engine.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-sources", "health", "digest-cron", "trading-digest", "weekly-calendar", "market-close"])
    parser.add_argument("--database", default="output/market_intelligence.db")
    parser.add_argument("--slot", help="Configured trading digest time in HH:MM")
    args = parser.parse_args()
    if args.command == "trading-digest" and args.slot is None:
        parser.error("trading-digest requires --slot")
    if args.command in {"digest-cron", "trading-digest", "weekly-calendar", "market-close"}:
        return trading_command(args.command, args.slot)
    return check_sources() if args.command == "check-sources" else health(args.database)


if __name__ == "__main__":
    raise SystemExit(main())
