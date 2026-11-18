from datetime import date
from pathlib import Path

import pytest

from market_intelligence.trading_calendar import trading_day


@pytest.mark.parametrize(("day", "expected"), [
    ("2026-02-23", False),  # Monday during the Spring Festival closure
    ("2026-02-24", True),
    ("2026-10-07", False),
    ("2026-10-08", True),
    ("2026-10-10", False),  # Saturday makeup working day
    ("2027-01-04", None),   # Unknown years cannot authorize trading-day messages
])
def test_verified_exchange_trading_day(day, expected):
    path = Path(__file__).resolve().parents[1] / "config/cn_trading_calendar.yaml"
    assert trading_day(date.fromisoformat(day), path) == expected


def test_trading_cron_runs_lunch_slot_exactly_and_retries():
    from market_intelligence.trading_calendar import cron_entries

    entries = cron_entries({"enabled": True, "digest": {"trading_times": ["11:35"]}})
    assert [line.split(" ", 2)[:2] for line in entries] == [["35", "11"], ["40", "11"], ["45", "11"]]
    assert all(line.endswith("--slot 11:35") for line in entries)
    with pytest.raises(ValueError, match="overlap"):
        cron_entries({"enabled": True, "digest": {"times": ["11:35"], "trading_times": ["11:35"]}})
