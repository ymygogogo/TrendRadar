"""Verified macro calendar entries for the following Shanghai calendar week."""
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def weekly_time(settings):
    value = settings.get("digest", {}).get("weekly_time")
    if value is not None and datetime.strptime(value, "%H:%M").strftime("%H:%M") != value:
        raise ValueError("Weekly calendar time must use HH:MM")
    return value


def upcoming_events(path, now):
    start = now.date() + timedelta(days=7 - now.weekday())
    end = start + timedelta(days=7)
    with Path(path).open(encoding="utf-8") as stream:
        calendar = yaml.safe_load(stream)
    if date.fromisoformat(calendar["coverage_until"]) < end - timedelta(days=1):
        return start, None
    records = {}
    for event in calendar["events"]:
        local = datetime.fromisoformat(event["starts_at"])
        source = urlsplit(event["source"])
        title = event["title"].strip()
        if local.tzinfo is None or not title or source.scheme != "https" or not source.hostname:
            raise ValueError("Invalid entry in the market events calendar")
        local = local.astimezone(now.tzinfo)
        if start <= local.date() < end:
            records[local, title] = event["source"]
    return start, [(local, title, source) for (local, title), source in sorted(records.items())]
