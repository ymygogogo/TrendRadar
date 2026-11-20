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


def test_admin_snapshot_displays_recent_events_and_channel_health_without_credentials(tmp_path, monkeypatch):
    from market_intelligence.admin import render, visible_values
    from market_intelligence.admin_view import render_event_index

    monkeypatch.setenv("MARKET_WEWORK_WEBHOOK_URL", "https://example.invalid/?key=wework-private")
    engine = MarketIntelligence({
        "database_path": str(tmp_path / "market.db"),
        "notification": {"enabled": True, "channels": ["wework", "telegram"]},
    }, {"TELEGRAM_BOT_TOKEN": "telegram-private"})
    try:
        now = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
        engine.record_health({"sse": {
            "name": "上交所", "url": "https://example.invalid/?key=feed-private", "success": False,
            "duration_ms": 250, "latest_published_at": "", "error": "feed-error-private",
        }}, {"sse": "domestic_official"})
        engine.db.executemany(
            """INSERT INTO events(id,title_zh,summary_zh,analysis_json,score,priority,verification_status,
                                  latest_reported_at,updated_at) VALUES (?,?,?,'{}',?,?,?,?,?)""",
            [
                ("a", "<script>重点事件</script>", "摘要", 90, "P0", "unverified",
                 "2026-11-18T15:50:00+08:00", now.isoformat()),
                ("b", "旧报道", "旧摘要", 87, "P0", "confirmed", "2026-11-16T19:03:00-05:00", now.isoformat()),
                ("c", "普通资讯", "摘要", 59, "P2", "unverified", now.isoformat(), now.isoformat()),
            ],
        )
        engine.db.executemany(
            """INSERT INTO deliveries(event_id,revision,channel,state,attempts,updated_at,error)
               VALUES ('a',?,'wework',?,1,?,'dispatch-error-private')""",
            [(1, "delivered", "2026-11-18T15:00:00+08:00"), (2, "failed", "2026-11-18T16:00:00+08:00")],
        )
        engine.db.execute("""INSERT INTO articles (id,feed_id,title,url,collected_at,event_id)
            VALUES ('source','sse','原文','https://example.net/news?token=source-private',?,'b')""", (now.isoformat(),))
        path = tmp_path / "status.json"
        publish(path, engine.db, now, engine.notification_status())
        current = read(path)
        channels = current["notification_channels"]
        assert channels["wework"]["latest"] == ["failed", "2026-11-18T16:00:00+08:00"]
        assert channels["wework"]["counts"] == {"delivered": 1, "failed": 1}
        assert channels["telegram"]["enabled"] and not channels["telegram"]["configured"]
        assert "private" not in path.read_text()
        assert "重点事件" not in metrics(current).decode()
        html = render(visible_values({}), "test-token", snapshot=current).decode()
        for label in ("当前汇总", "采集渠道", "推送渠道", "企业微信", "待配置", "连续失败 1 次",
                      "待核实", "16:00", "&lt;script&gt;重点事件&lt;/script&gt;"):
            assert label in html
        assert 'href="/admin/events" aria-label="查看 2 件 P0 / P1 事件"' in html
        archive = render_event_index(current, "P0", 1).decode()
        assert "旧报道" in archive and "&lt;script&gt;重点事件&lt;/script&gt;" in archive
        assert "https://example.net/news" in archive and "普通资讯" not in archive and "private" not in archive
        assert "保留期内暂无符合条件的事件" in render_event_index(current, "P1", 1).decode()
        for omitted in ("<script>", "旧报道", "普通资讯", "private"):
            assert omitted not in html
    finally:
        engine.close()
