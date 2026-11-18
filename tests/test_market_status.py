from datetime import datetime, timezone

from market_intelligence.engine import MarketIntelligence
from market_intelligence.status import metrics, publish, read


def test_market_metrics_are_operational_counters_without_news_or_secret_values(tmp_path):
    engine = MarketIntelligence({"database_path": str(tmp_path / "market.db")}, {})
    try:
        engine.record_health({"sse": {
            "name": "上交所", "url": "https://example.invalid/private?token=redact-me", "success": True,
            "duration_ms": 35, "latest_published_at": "", "error": None,
        }})
        path = tmp_path / "status.json"
        publish(path, engine.db, datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc))
        state = read(path)
        assert state["articles"] == {}
        result = metrics(state).decode()
        assert 'trendradar_feed_successes_total{feed="sse"} 1' in result
        assert 'trendradar_feed_last_fetch_duration_milliseconds{feed="sse"} 35' in result
        assert "redact-me" not in path.read_text()
        assert "example.invalid" not in result
        assert path.stat().st_mode & 0o777 == 0o644
        assert read(tmp_path / "missing.json") is None
    finally:
        engine.close()
