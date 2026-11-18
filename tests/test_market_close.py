from datetime import date
from decimal import Decimal

import pytest

from market_intelligence.market_close import parse_snapshot


def test_verified_index_close_snapshot():
    body = ('trendradar({"result":[{"TRADE_DATE":"20261118","INDEX_CODE":"000001",'
            '"INDEX_ABBR":"上证指数","CLOSE_POINT":"100.00","TRADE_AMT":"5.50"}]});')
    rows = parse_snapshot(body, {"000001"})
    assert rows[(date(2026, 11, 18), "000001")] == ("上证指数", Decimal("100.00"), Decimal("5.50"))
    with pytest.raises(ValueError, match="wrapper"):
        parse_snapshot("other({})", {"000001"})
    with pytest.raises(ValueError, match="Invalid"):
        parse_snapshot(body.replace("100.00", "NaN"), {"000001"})


def test_same_day_close_is_separate_and_previous_trading_day_is_required(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from market_intelligence.engine import MarketIntelligence

    zone = ZoneInfo("Asia/Shanghai")
    today = date(2026, 11, 18)
    settings = {
        "database_path": str(tmp_path / "market.db"),
        "market_close": {"enabled": True, "index_codes": ["000001"]},
    }
    monkeypatch.setattr("market_intelligence.market_close.fetch_snapshot", lambda _codes: {
        (date(2026, 11, 16), "000001"): ("上证指数", Decimal("99"), Decimal("3")),
        (today, "000001"): ("上证指数", Decimal("102"), Decimal("5")),
    })
    engine = MarketIntelligence(settings, {}, now=lambda: datetime(2026, 11, 18, 15, 30, tzinfo=zone))
    try:
        engine.collect_market_close()
        report = "\n".join(engine.closing_lines(today))
        assert "收盘 102" in report and "较上一交易日" not in report
        assert "尚未发布" in engine.closing_lines(date(2026, 11, 19))[0]
        assert engine.closing_lines(date(2026, 11, 21)) == []
        monkeypatch.setattr("market_intelligence.market_close.fetch_snapshot", lambda _codes: {
            (date(2026, 11, 17), "000001"): ("上证指数", Decimal("100"), Decimal("4")),
        })
        engine.collect_market_close()
        assert "较上一交易日 +2.00%" in engine.closing_lines(today)[1]
    finally:
        engine.close()
