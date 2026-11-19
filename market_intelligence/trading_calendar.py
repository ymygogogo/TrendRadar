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


def due_jobs(settings, now):
    from .weekly_calendar import weekly_time

    def due(value, offsets):
        if not value:
            return False
        hour, minute = map(int, value.split(":"))
        return now.hour * 60 + now.minute - (hour * 60 + minute) in offsets

    digest = settings.get("digest", {})
    for slot in digest.get("times", ["21:30"]):
        if due(slot, (0, 5, 10, 30, 60)):
            yield "daily", slot
    if now.weekday() < 5:
        for slot in trading_slots(settings):
            if due(slot, (0, 5, 10)):
                yield "trading", slot
        opening = settings.get("market_open", {})
        if opening.get("notification_enabled", opening.get("enabled")) and due(opening.get("alert_time", "09:40"), (0, 5, 10)):
            yield "opening", None
    if now.weekday() == 6 and due(weekly_time(settings), (0, 5, 10)):
        yield "weekly", None


def cron_entries(settings, *, live_schedule=False):
    if not settings.get("enabled"):
        return []
    entries = []
    if live_schedule:
        entries.append("* * * * * cd /app && python -m market_intelligence schedule-tick")
    else:
        for value in trading_slots(settings):
            slot = datetime.strptime(value, "%H:%M")
            for delay in (0, 5, 10):
                execute = slot + timedelta(minutes=delay)
                if execute.date() == slot.date():
                    entries.append(f"{execute.minute} {execute.hour} * * * cd /app && python -m market_intelligence trading-digest --slot {value}")
        from .weekly_calendar import weekly_time

        weekly = weekly_time(settings)
        if weekly:
            slot = datetime.strptime(weekly, "%H:%M")
            for delay in (0, 5, 10):
                execute = slot + timedelta(minutes=delay)
                if execute.date() == slot.date():
                    entries.append(f"{execute.minute} {execute.hour} * * 0 cd /app && python -m market_intelligence weekly-calendar")
        opening = settings.get("market_open", {})
        if opening.get("notification_enabled", opening.get("enabled")):
            start = datetime.strptime(opening.get("alert_time", "09:40"), "%H:%M")
            if start.strftime("%H:%M") < "09:31":
                raise ValueError("Opening observation must be scheduled after market open")
            for delay in (0, 5, 10):
                stamp = start + timedelta(minutes=delay)
                entries.append(f"{stamp.minute} {stamp.hour} * * * cd /app && python -m market_intelligence market-open")
    sampling = settings.get("market_close", {})
    if sampling.get("enabled"):
        for value in sampling.get("sample_times", []):
            slot = datetime.strptime(value, "%H:%M")
            entries.append(f"{slot.minute} {slot.hour} * * * cd /app && python -m market_intelligence market-close")
    return entries
