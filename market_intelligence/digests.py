"""Selection, storage and notification text for a fixed market digest edition."""

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from itertools import islice
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .company_mapping import match_companies
from .news_topics import NewsTopics, TOPIC_LABELS


MIN_EVENTS = 8
MAX_EVENTS = 20
MESSAGE_BYTES = 3800
TIER_ORDER = {"main": 0, "watch": 1, "extended": 2}
REGION_LABELS = {"domestic": "国内", "international": "海外"}
DIRECTIONS_ZH = {
    "potential_positive": "潜在利好",
    "potential_negative": "潜在承压",
    "potentially_mixed": "影响分化",
    "unclear": "影响不明确",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_editions (
    delivery_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_entries (
    delivery_key TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(delivery_key, event_id)
);
CREATE INDEX IF NOT EXISTS idx_digest_entries_event ON digest_entries(event_id, revision);
CREATE TABLE IF NOT EXISTS published_digest_editions (delivery_key TEXT PRIMARY KEY);
"""


def event_limit(value):
    return max(MIN_EVENTS, min(MAX_EVENTS, int(value)))


def safe_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme in ("http", "https") and url.hostname and not (url.username or url.password):
            return value
    except ValueError:
        pass
    return ""


def archive_folder(status_path):
    return Path(status_path).parent / "digests" if status_path else None


def public_id(delivery_key):
    return hashlib.sha256(delivery_key.encode()).hexdigest()[:24]


def archive_edition(path, edition):
    folder = archive_folder(path)
    if folder is None:
        raise ValueError("Set MARKET_STATUS_PATH to publish digest details")
    folder.mkdir(mode=0o755, exist_ok=True)
    target = folder / f"{edition['id']}.json"
    fd, pending = tempfile.mkstemp(prefix=".digest-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(edition, handle, ensure_ascii=False)
            os.fchmod(handle.fileno(), 0o644)
        os.replace(pending, target)
    finally:
        Path(pending).unlink(missing_ok=True)


def read_edition(status_path, edition_id):
    folder = archive_folder(status_path)
    if folder is None or not re.fullmatch(r"[0-9a-f]{24}", edition_id):
        return None
    try:
        return json.loads((folder / f"{edition_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def forget_old_editions(db, cutoff, status_path):
    folder = archive_folder(status_path)
    old = db.execute("SELECT delivery_key FROM digest_editions WHERE created_at<?", (cutoff,)).fetchall()
    if folder is not None:
        for (key,) in old:
            (folder / f"{public_id(key)}.json").unlink(missing_ok=True)
    db.execute("DELETE FROM digest_entries WHERE delivery_key IN (SELECT delivery_key FROM digest_editions WHERE created_at<?)", (cutoff,))
    db.execute("DELETE FROM published_digest_editions WHERE delivery_key IN "
               "(SELECT delivery_key FROM digest_editions WHERE created_at<?)", (cutoff,))
    db.execute("DELETE FROM digest_editions WHERE created_at<?", (cutoff,))


def _event_details(row, data, tier, topic, signals, companies, sources):
    sectors = [item for item in data.get("sectors", []) if item.get("sector")]
    # Existing event JSON can predate newly verified issuer mappings.
    stocks = match_companies(companies, sectors, limit=3)
    concepts = list(dict.fromkeys([*(sector["sector"] for sector in sectors), *data.get("categories", [])]))
    return {
        "event_id": row["id"], "revision": row["revision"], "tier": tier,
        "priority": row["priority"], "score": row["score"], "topic": topic, **signals,
        "reported_at": row["reported_at"], "verification": row["verification_status"],
        "title": row["title_zh"], "summary": row["summary_zh"],
        "source": sources[0]["url"] if sources else "", "sources": sources,
        "direction": data.get("direction", "unclear"),
        "impact": data.get("impact_mechanism", ""),
        "follow_up_signals": data.get("follow_up_signals", [])[:2],
        "concepts": concepts[:6], "sectors": sectors, "companies": stocks,
    }


def _linked_sources(db, event_id, publishers, domestic_platforms):
    rows = db.execute("""SELECT a.feed_id,a.source_name,a.url,h.name,h.category,v.views,v.sampled_at
        FROM articles a LEFT JOIN feed_health h USING (feed_id)
        LEFT JOIN article_youtube_views v ON v.article_id=a.id
        WHERE a.event_id=?
        ORDER BY julianday(coalesce(a.published_at,a.collected_at)) DESC, a.id""", (event_id,))
    sources = {}
    for row in rows:
        url = safe_url(row["url"])
        if not url:
            continue
        publisher = publishers.get(row["feed_id"]) or row["name"] or row["source_name"] or urlsplit(url).hostname
        category = row["category"] or ""
        medium = {"international_video": "YouTube", "international_social": "Bluesky"}.get(category, "")
        evidence = sources.setdefault((publisher, medium), {
            "name": publisher, "url": url, "medium": medium,
            "international": category.startswith("international_"),
            "domestic": (row["category"] in ("domestic_media", "domestic_official")
                         or row["feed_id"] in domestic_platforms),
        })
        if evidence["url"] == url and row["views"] is not None:
            evidence["youtube_views"] = {"views": row["views"], "sampled_at": row["sampled_at"]}
    return list(sources.values())


def _has_international(event):
    return any(source["international"] for source in event.get("sources", ()))


def _heat_signals(db, event_id, published, now, publishers, *, rank_bonus=True):
    sources = db.execute("""SELECT feed_id,NULL FROM articles WHERE event_id=?
        UNION ALL
        SELECT h.feed_id,h.rank FROM article_hotlist h JOIN articles a ON h.article_id=a.id
        WHERE a.event_id=? AND h.seen_at BETWEEN ? AND ?""",
        (event_id, event_id, (now - timedelta(hours=24)).isoformat(), now.isoformat()))
    outlets, ranks = set(), []
    for source, rank in sources:
        outlets.add(publishers.get(source, source))
        if rank is not None:
            ranks.append(rank)
    best = min(ranks, default=None)
    return {"heat": _heat_score(len(outlets), best if rank_bonus else None, published, now), "outlet_count": len(outlets),
            "outlets": sorted(outlets), "hotlist_rank": best}


def _heat_score(outlet_count, rank, published, now):
    age_hours = (now.timestamp() - published) / 3600
    coverage = 10 * min(4, outlet_count)
    freshness = 30 * max(0, 1 - age_hours / 24)
    rank_score = max(0, 31 - rank) if rank is not None else 0
    return round(coverage + freshness + rank_score)


def _speech_key(title):
    # Pair the original news report with the transcript of the same named speech.
    if not title or "主旨演讲" not in title:
        return None, False
    transcript = bool(re.search(r"全文|文字实录", title))
    compact = re.sub(r"[^\w]", "", title).removeprefix("全文")
    key = re.sub(r"全文|文字实录|出席|开幕式|并发表|时发表|发表|上的|在", "", compact)
    return (key if len(key) >= 14 else None), transcript


def _combine_speech_reports(buckets, now, *, rank_bonus=True):
    index = {}
    combined = {}
    for tier, candidates in buckets.items():
        events = []
        for rank, event in sorted(candidates):
            key, transcript = _speech_key(event["title"])
            if key is None:
                events.append((rank[1], event))
                continue
            published = -rank[1]
            existing = index.setdefault(key, [])
            original = next((item for item in existing if (transcript or item[2])
                             and abs(item[0] - published) <= 12 * 3600), None)
            if original is None:
                existing.append((published, event, transcript))
                events.append((rank[1], event))
                continue
            original_stamp, primary, _ = original
            primary.setdefault("related_versions", []).append((event["event_id"], event["revision"]))
            sources = primary.setdefault("other_sources", [])
            if event["source"] and event["source"] not in (primary["source"], *sources):
                sources.append(event["source"])
            evidence = {(source["name"], source["medium"]): source for source in event["sources"]}
            evidence.update({(source["name"], source["medium"]): source for source in primary["sources"]})
            primary["sources"] = list(evidence.values())
            outlets = sorted(set(primary["outlets"]) | set(event["outlets"]))
            ranks = [value for value in (primary["hotlist_rank"], event["hotlist_rank"]) if value is not None]
            best = min(ranks, default=None)
            primary.update(outlets=outlets, outlet_count=len(outlets), hotlist_rank=best,
                           heat=_heat_score(len(outlets), best if rank_bonus else None, original_stamp, now))
        combined[tier] = events
    return {
        tier: [((-event["heat"], stamp, event["event_id"]), event) for stamp, event in items]
        for tier, items in combined.items()
    }


def _cover_topics(ranked, limit, reserved):
    representatives = {}
    preselected_topics = {event["topic"] for _, event in reserved if event["tier"] == "main"}
    reserved_ids = {event["event_id"] for _, event in reserved}
    remaining = []
    for rank, event in sorted(ranked):
        if event["event_id"] in reserved_ids:
            continue
        topic = event["topic"]
        if topic in representatives or topic in preselected_topics:
            remaining.append((rank, event))
        else:
            representatives[topic] = (rank, event)
    chosen = list(reserved)
    chosen.extend(list(representatives.values())[:limit - len(chosen)])
    chosen.extend(remaining[:limit - len(chosen)])
    return chosen


def _balanced_choices(buckets, maximum):
    limit = min(maximum, max(MIN_EVENTS, len(buckets["main"])))
    seats = {"domestic": (limit + 1) // 2, "international": limit // 2}
    ordered = [pair for tier in TIER_ORDER for pair in sorted(buckets[tier])]
    regions = {
        event["event_id"]: tuple(region for region in seats if any(
            source.get(region) for source in event["sources"]))
        for _, event in ordered
    }
    exclusive = Counter(options[0] for options in regions.values() if len(options) == 1)
    chosen = {}

    def reserve(pair):
        _, event = pair
        event_id = event["event_id"]
        options = regions[event_id]
        eligible = [region for region in options if seats[region] > 0]
        if not eligible or event_id in chosen or len(chosen) == limit:
            return False
        # Assign a jointly reported event to the side with fewer single-region
        # candidates available for its remaining seats.
        region = max(eligible, key=lambda value: (seats[value] - exclusive[value], seats[value]))
        seats[region] -= 1
        if len(options) == 1:
            exclusive[region] -= 1
        event["edition_region"] = region
        chosen[event_id] = pair
        return True

    covered = set()
    for pair in sorted(buckets["main"]):
        topic = pair[1]["topic"]
        if topic not in covered and reserve(pair):
            covered.add(topic)
    for pair in ordered:
        reserve(pair)

    for pair in ordered:
        if len(chosen) == limit:
            break
        event = pair[1]
        if event["event_id"] not in chosen:
            options = regions[event["event_id"]]
            if options:
                event["edition_region"] = options[0]
            chosen[event["event_id"]] = pair

    return [event for _, event in sorted(chosen.values(), key=lambda pair: (TIER_ORDER[pair[1]["tier"]], pair[0]))]


def select_events(db, settings, companies, now, start):
    configured = settings.get("digest", {})
    threshold = int(settings.get("event_analysis", {}).get("min_priority_score", 70))
    max_events = event_limit(configured.get("max_events", MIN_EVENTS))
    balanced = bool(configured.get("balance_regions", False))
    domestic_platforms = set(settings.get("domestic_platforms", []))
    international_min = max(0, min(int(configured.get("min_international_events", 0)), max_events - len(TOPIC_LABELS)))
    yesterday = int((now - timedelta(hours=24)).timestamp())
    recent_start = int(start.timestamp())
    topics = NewsTopics(settings)
    publishers = {feed: label for label, feeds in configured.get("publisher_groups", {}).items() for feed in feeds}
    rows = db.execute("""
        SELECT e.*, coalesce(e.latest_reported_at,e.updated_at) AS reported_at,
            CAST(strftime('%s',coalesce(e.latest_reported_at,e.updated_at)) AS INTEGER) AS reported_epoch,
            EXISTS (SELECT 1 FROM digest_entries de JOIN deliveries d ON d.event_id=de.delivery_key
                WHERE de.event_id=e.id AND de.revision=e.revision AND d.state='delivered') AS sent_in_digest,
            EXISTS (SELECT 1 FROM deliveries d WHERE d.event_id=e.id AND d.revision=e.revision
                AND d.state='delivered') AS sent_as_alert
        FROM events e
        WHERE CAST(strftime('%s',coalesce(e.latest_reported_at,e.updated_at)) AS INTEGER) BETWEEN ? AND ?
            AND CAST(strftime('%s',e.updated_at) AS INTEGER)<=?
        """, (int((now - timedelta(hours=48)).timestamp()), int(now.timestamp()), int(now.timestamp())))
    buckets = {"main": [], "watch": [], "extended": []}
    for row in rows:
        if row["priority"] == "P0" and row["verification_status"] == "confirmed" and row["sent_as_alert"]:
            continue
        data = json.loads(row["analysis_json"])
        topic = topics.classify(row["title_zh"], data.get("categories", []), row["summary_zh"])
        if not topic:
            continue
        published = row["reported_epoch"]
        signals = _heat_signals(db, row["id"], published, now, publishers, rank_bonus=not balanced)
        if published >= recent_start:
            tier = "main"
        elif row["sent_in_digest"]:
            continue
        elif published >= yesterday:
            tier = "watch"
        elif row["score"] >= threshold or signals["outlet_count"] >= 2:
            tier = "extended"
        else:
            continue
        rank = (-signals["heat"], -published, row["id"])
        sources = _linked_sources(db, row["id"], publishers, domestic_platforms)
        buckets[tier].append((rank, _event_details(row, data, tier, topic, signals, companies, sources)))

    buckets = _combine_speech_reports(buckets, now, rank_bonus=not balanced)
    if balanced:
        return _balanced_choices(buckets, max_events)
    international = [pair for tier in ("main", "watch", "extended") for pair in sorted(buckets[tier])
                     if _has_international(pair[1])]
    chosen = _cover_topics(buckets["main"], max_events, international[:international_min])
    chosen_ids = {event["event_id"] for _, event in chosen}
    for tier in ("watch", "extended"):
        eligible = (pair for pair in sorted(buckets[tier]) if pair[1]["event_id"] not in chosen_ids)
        count = max(0, MIN_EVENTS - len(chosen))
        chosen.extend(islice(eligible, count))
    return [event for _, event in sorted(chosen, key=lambda pair: (TIER_ORDER[pair[1]["tier"]], pair[0]))]


def make_edition(engine, key, now, start, heading, closing=False, shown_at=None):
    from .market_open import market_context

    return {
        "id": public_id(key),
        "title": heading,
        "created_at": (shown_at or now).isoformat(),
        "target": MIN_EVENTS,
        "events": select_events(engine.db, engine.settings, engine.companies, now, start),
        "closing": engine.closing_lines(now.date()) if closing else [],
        "market_context": market_context(engine.db, now) if engine.settings.get("market_open", {}).get("enabled") else [],
    }


def freeze_edition(db, key, edition):
    with db:
        inserted = db.execute("INSERT OR IGNORE INTO digest_editions VALUES (?,?,?)",
                             (key, edition["created_at"], json.dumps(edition, ensure_ascii=False))).rowcount
        if inserted:
            db.executemany("INSERT INTO digest_entries VALUES (?,?,?)",
                           [(key, event_id, revision) for event in edition["events"]
                            for event_id, revision in [(event["event_id"], event["revision"]),
                                                       *event.get("related_versions", [])]])
    return stored_edition(db, key)


def stored_edition(db, key):
    row = db.execute("SELECT payload FROM digest_editions WHERE delivery_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _short_title(value, budget):
    text = " ".join(re.sub(r"[\[\]<>`*_\\]", "", value).split()) or "新闻详情"
    raw = (text or "新闻详情见链接").encode("utf-8")
    if len(raw) <= budget:
        return text
    return raw[:budget - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def digest_url(origin, edition_id, *, public=False):
    path = f"/digests/{edition_id}.html" if public else f"/admin/digests/{edition_id}"
    return origin.rstrip("/") + path


def region_summary(events):
    counts = Counter(event.get("edition_region") for event in events)
    if not any(counts[region] for region in REGION_LABELS):
        return ""
    return " · ".join(f"{label} {counts[region]} 条" for region, label in REGION_LABELS.items())


def digest_message(edition, detail_origin="", *, public=False, max_bytes=MESSAGE_BYTES):
    events = edition["events"]
    stamp = datetime.fromisoformat(edition["created_at"])
    lines = [f"# {edition['title']} · {stamp:%m-%d %H:%M}", f"本期 {len(events)} 条"]
    if regions := region_summary(events):
        lines[1] += f" · {regions}"
    if len(events) < edition["target"]:
        lines[1] += f" · 距目标还差 {edition['target'] - len(events)} 条符合条件的事件"
    url = digest_url(detail_origin, edition["id"], public=public) if detail_origin else ""
    context = edition.get("market_context", [])
    if context and url:
        lines.append(f"[市场核实与传闻跟踪 {len(context)} 条]({url}#market-context)")
    footer = f"\n\n[整期简报]({url})" if url else ""
    entries = []
    for index, event in enumerate(events, 1):
        topic = TOPIC_LABELS.get(event.get("topic"), event["priority"])
        label = f"{topic} · 热度 {event['heat']}" if "heat" in event else topic
        region_label = REGION_LABELS.get(event.get("edition_region"))
        if region_label:
            label += f" · {region_label}"
        elif _has_international(event):
            label += " · 海外"
        if event["tier"] == "watch":
            label += " · 观察"
        if event["tier"] == "extended":
            date = datetime.fromisoformat(event["reported_at"]).astimezone(stamp.tzinfo)
            label += f" · 补录 {date:%m-%d}"
        link = f"{url}#event-{index}" if url else ""
        source = event.get("source", "")
        if not url and len(events) <= 3 and source:
            source = f" [原文]({source.replace('(', '%28').replace(')', '%29')})"
            if len(source.encode()) > 200:
                source = ""
        else:
            source = ""
        direction = DIRECTIONS_ZH.get(event.get("direction"), "影响不明确")
        impact = event.get("impact", "").strip()
        if direction == "影响不明确" or not impact:
            impact_line, impact = "　A股：影响暂不明确", ""
        else:
            impact_line = f"　A股：{direction}"
            sector = next((item["sector"] for item in event.get("sectors", []) if item.get("sector")), "")
            impact = " · ".join(filter(None, (sector, impact)))
        entries.append((f"{index}. {label}｜", link, source, impact_line, impact, event["title"]))

    def render(title_bytes, impact_bytes):
        out = list(lines)
        for prefix, link, source, impact_label, impact, title in entries:
            short = _short_title(title, title_bytes)
            out.append(prefix + (f"[{short}]({link})" if link else short + source))
            out.append(impact_label + (" · " + _short_title(impact, impact_bytes) if impact and impact_bytes else ""))
        return "\n".join(out) + footer

    max_bytes = min(max_bytes, MESSAGE_BYTES)
    for title_bytes, impact_bytes in ((120, 95), (81, 60), (60, 36), (42, 27), (30, 18), (18, 12), (9, 9), (9, 0)):
        message = render(title_bytes, impact_bytes)
        if len(message.encode()) <= max_bytes:
            return message
    raise ValueError("channel budget cannot fit complete digest")
