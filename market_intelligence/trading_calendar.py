"""Trading-day checks for mainland A-share digests."""
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml


def trading_day(day: date, calendar_path: str | Path) -> bool | None:
    """Return None when there is no verified calendar for the requested year."""
    with Path(calendar_path).open(encoding="utf-8") as source:
        years = yaml.safe_load(source)["years"]
    year = years.get(day.year)
    if year is None:
        return None
    closures = [(date.fromisoformat(start), date.fromisoformat(end))
                for start, end in year["closures"]]
    if any(start.year != day.year or end.year != day.year or start > end for start, end in closures):
        raise ValueError("Invalid trading-calendar closure range")
    return day.weekday() < 5 and not any(start <= day <= end for start, end in closures)

def trading_slots(settings):
    """Validate trading-day times and prevent duplicate daily digest slots."""
    digest = settings.get("digest", {})
    values = digest.get("trading_times", [])
    if len(set(values)) != len(values) or set(values).intersection(digest.get("times", ["21:30"])):
        raise ValueError("Trading digest times overlap another slot")
    for value in values:
        if datetime.strptime(value, "%H:%M").strftime("%H:%M") != value:
            raise ValueError("Expected a trading digest time in HH:MM format")
    return sorted(values)


def cron_entries(settings):
    if not settings.get("enabled"):
        return []
    entries = []
    for value in trading_slots(settings):
        slot = datetime.strptime(value, "%H:%M")
        for delay in (0, 5, 10):
            execute = slot + timedelta(minutes=delay)
            if execute.date() == slot.date():
                entries.append(f"{execute.minute} {execute.hour} * * * cd /app && python -m market_intelligence trading-digest --slot {value}")
    return entries
