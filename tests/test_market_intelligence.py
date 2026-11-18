"""Event and notification invariants with synthetic articles; no live delivery."""
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.engine import MarketIntelligence, canonical_url, validate_analysis


NOW = datetime(2026, 11, 18, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def article(title, url, feed):
    return SimpleNamespace(title=title, url=url, feed_id=feed,
                           published_at="2026-11-18T01:00:00Z", summary=title)


def data(*items):
    groups = {}
    for item in items:
        groups.setdefault(item.feed_id, []).append(item)
    return SimpleNamespace(items=groups)


def diagnostics(*feeds):
    return {feed: {"name": feed, "url": f"https://example.com/{feed}",
            "success": True, "duration_ms": 25, "latest_published_at":
            "2026-11-18T01:00:00Z", "error": ""} for feed in feeds}


class FakeClient:
    def chat(self, messages, **kwargs):
        prompt = json.loads(messages[1]["content"])
        item = prompt["article"]
        same = "chip" in item["title"].lower() or "芯片" in item["title"]
        candidate = prompt["candidate_events"][0]["id"] if same and prompt["candidate_events"] else None
        return json.dumps({
            "candidate_event_id": candidate, "title_zh": "芯片出口限制" if same else "独立事件",
            "summary_zh": item["title"], "impact_mechanism": "可能影响供应链",
            "verification_status": "confirmed", "confidence": .95, "categories": ["semiconductors"], "direction": "potentially_mixed",
            "score_dimensions": {"scope": 95, "strength": 95, "credibility": 95, "urgency": 95},
            "sectors": [{"sector": "半导体", "direction": "potentially_mixed", "reason": "供应链变化"}],
            "uncertainties": ["政策执行细节未知"], "follow_up_signals": ["官方细则"],
            "related_companies": [], "source_urls": [item["url"]],
            "material_update": False,
        }, ensure_ascii=False)


def settings(tmp_path):
    return {"database_path": str(tmp_path / "market.db"), "timezone": "Asia/Shanghai",
            "event_analysis": {"enabled": True, "min_priority_score": 70,
                               "urgent_priority_score": 85},
            "notification": {"enabled": True, "channels": ["feishu"], "max_per_hour": 3}}


def test_cross_language_cluster_and_delivery_idempotency(tmp_path):
    sends = []
    engine = MarketIntelligence(settings(tmp_path), {}, FakeClient(),
                                lambda channel, body: sends.append(body) or True, lambda: NOW)
    first = article("Chip export restrictions", "https://official.example/chip?utm_source=x", "federal-reserve")
    second = article("芯片出口限制", "https://bbc.example/chip", "bbc-world")
    engine.run(data(first), diagnostics("federal-reserve"), {"federal-reserve": "international_official"})
    engine.run(data(second), diagnostics("bbc-world"), {"bbc-world": "international_media"})
    engine.run(data(first, second), diagnostics("federal-reserve", "bbc-world"),
               {"federal-reserve": "international_official", "bbc-world": "international_media"})
    assert engine.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert engine.db.execute("SELECT count(*) FROM articles").fetchone()[0] == 2
    assert len(sends) == 1
    assert "https://official.example/chip" in sends[0]
    engine.close()


def test_failure_is_retryable_after_restart(tmp_path):
    attempts = []
    def sender(channel, body):
        attempts.append(body)
        return len(attempts) > 1
    item = article("Chip export restrictions", "https://official.example/chip", "federal-reserve")
    cfg = settings(tmp_path)
    e = MarketIntelligence(cfg, {}, FakeClient(), sender, lambda: NOW)
    e.run(data(item), diagnostics("federal-reserve"), {"federal-reserve": "international_official"})
    assert e.db.execute("SELECT state FROM deliveries").fetchone()[0] == "failed"
    e.close()
    e = MarketIntelligence(cfg, {}, FakeClient(), sender, lambda: NOW)
    e.dispatch_urgent()
    assert e.db.execute("SELECT state FROM deliveries").fetchone()[0] == "delivered"
    assert len(attempts) == 2
    e.close()


def test_validation_rejects_hallucinated_source_and_candidate():
    value = json.loads(FakeClient().chat([{}, {"content": json.dumps({"article": {
        "title": "Chip", "url": "https://official.example/chip"}, "candidate_events": []})}]))
    value["source_urls"] = ["https://invented.example/"]
    with pytest.raises(ValueError, match="unprovided"):
        validate_analysis(value, "https://official.example/chip", set())
    value["source_urls"] = ["https://official.example/chip"]
    value["candidate_event_id"] = "invented"
    with pytest.raises(ValueError, match="candidate"):
        validate_analysis(value, "https://official.example/chip", set())


def test_url_normalization():
    assert canonical_url("HTTPS://EXAMPLE.COM/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"


def test_unverified_p0_goes_to_digest_once(tmp_path):
    sends = []
    evening = NOW.replace(hour=21, minute=30)
    e = MarketIntelligence(settings(tmp_path), {}, FakeClient(),
                           lambda channel, body: sends.append(body) or True, lambda: evening)
    item = article("Chip export restrictions", "https://bbc.example/chip", "bbc-world")
    e.run(data(item), diagnostics("bbc-world"), {"bbc-world": "international_media"})
    e.dispatch_digest()
    assert len(sends) == 1
    assert "事件简报" in sends[0]
    assert e.db.execute("SELECT verification_status FROM events").fetchone()[0] == "unverified"
    e.close()


def test_daily_ai_budget_and_hotlist(tmp_path):
    cfg = settings(tmp_path)
    cfg.update(max_articles_per_day=1, domestic_platforms=["cls-hot"])
    e = MarketIntelligence(cfg, {}, FakeClient(), lambda *_: True, lambda: NOW)
    e.ingest_hotlist({"cls-hot": {"芯片消息": {"url": "https://example.net/1"},
                                 "另一消息": {"url": "https://example.net/2"}}}, {"cls-hot": "财联社"})
    e.analyze_pending()
    e.analyze_pending()
    assert e.db.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 1
    assert e.db.execute("SELECT count(*) FROM articles").fetchone()[0] == 2
    e.close()
