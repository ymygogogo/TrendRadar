from datetime import datetime
from decimal import Decimal

from market_intelligence import market_open
from market_intelligence.engine import MarketIntelligence
from market_intelligence.trading_calendar import cron_entries
from trendradar.crawler.rss.links import parse_official_qa


NOW = datetime.fromisoformat("2026-11-19T09:40:00+08:00")


def quotes(day):
    return "\n".join(
        f'var hq_str_{code}="{",".join([label, value, "100", *(["0"] * 27), day, "09:40:00"])}";'
        for (code, label), value in zip(market_open.CODES.items(), ("99.87", "100.63", "99.07"))
    )


def test_opening_uses_trade_date_and_open_not_current():
    values = market_open.parse_open(quotes("2026-11-19"), NOW.date())
    assert values["沪指"] == Decimal("-0.13")
    assert "深成指高开 0.63%" in market_open.opening_facts(values)
    assert "创业板指低开 0.93%" in market_open.opening_facts(values)
    import pytest

    with pytest.raises(ValueError):
        market_open.parse_open(quotes("2026-11-18"), NOW.date())


def test_opening_alert_matches_relevant_public_sources_and_dedupes(tmp_path, monkeypatch):
    settings = {"enabled": True, "database_path": str(tmp_path / "market.db"),
                "notification": {"enabled": True, "channels": ["wework"]},
                "market_open": {"enabled": True, "alert_time": "09:40"}}
    messages = []
    now = [NOW]
    engine = MarketIntelligence(settings, {}, sender=lambda channel, body: messages.append(body) or True,
                                now=lambda: now[0])
    try:
        engine.db.executemany("""INSERT INTO articles(id,feed_id,title,url,published_at,collected_at)
             VALUES(?,?,?,?,?,?)""", [
            ("a","eastmoney-market","三星半导体代工穿透严查传闻", "https://example.com/a",
             "2026-11-19T08:00:00+08:00","2026-11-19T09:00:00+08:00"),
            ("b","csrc-news","证监会辟谣基金敞口数据上报", "https://example.com/b",
             "2026-11-18T21:00:00+08:00","2026-11-18T21:20:00+08:00"),
            ("c","eastmoney-market","芯片断供后续报道", "https://example.com/c",
             "2026-11-19T09:32:00+08:00","2026-11-19T09:35:00+08:00"),
            ("d","eastmoney-market","游戏明星网传", "https://example.com/d",
             None,"2026-11-19T09:34:00+08:00"),
        ])
        engine.db.commit()
        monkeypatch.setattr(market_open, "fetch_open", lambda day: market_open.parse_open(quotes(day.isoformat()), day))
        engine.dispatch_market_open()
        engine.dispatch_market_open()
        assert len(messages) == 1
        message = messages[0]
        for text in ("低开 0.93%", "盘前报道", "开盘后报道", "官方核实", "待核实", "交付可能承压"):
            assert text in message
        assert "明星" not in message
        assert [x.split(" *")[0] for x in cron_entries(settings) if x.endswith("market-open")] == [
            "40 9", "45 9", "50 9"]
        now[0] = datetime.fromisoformat("2026-11-22T09:40:00+08:00")
        engine.dispatch_market_open()
        assert len(messages) == 1
    finally:
        engine.close()


def test_official_qa_extracts_question_with_meaningful_title():
    source = "<p>问：基金公司是否上报全部产品AI敞口？</p><p>答：该信息不属实。</p><p>版权所有</p>"
    assert parse_official_qa(source) == ("基金公司是否上报全部产品AI敞口？", "该信息不属实。")
