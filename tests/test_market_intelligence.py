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
    return {feed: {"name": feed, "url": f"https://{'sec.example' if feed == 'sec-press' else 'official.example'}/{feed}",
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


def test_material_update_respects_cooldown_then_dispatches(tmp_path):
    from datetime import timedelta

    class UpdatedClient(FakeClient):
        def chat(self, messages, **kwargs):
            obj = json.loads(super().chat(messages, **kwargs))
            obj["material_update"] = "changed" in obj["summary_zh"]
            return json.dumps(obj)

    clock = [NOW]
    sends = []
    e = MarketIntelligence(settings(tmp_path), {}, UpdatedClient(),
                           lambda channel, body: sends.append(body) or True, lambda: clock[0])
    official = {"sec-press": "international_official"}
    e.run(data(article("Chip restrictions", "https://sec.example/a", "sec-press")),
          diagnostics("sec-press"), official)
    e.run(data(article("Chip restrictions changed", "https://sec.example/b", "sec-press")),
          diagnostics("sec-press"), official)
    assert len(sends) == 1
    assert e.db.execute("SELECT count(*) FROM event_versions").fetchone()[0] == 2
    clock[0] += timedelta(minutes=61)
    e.dispatch_urgent()
    assert len(sends) == 2
    e.close()


def test_official_index_only_accepts_matching_same_host_article_links():
    from trendradar.crawler.rss.links import parse_article_index

    html = """
      <a href="/news/t20261118_100.html"><span>重点政策</span>发布</a>
      <a href="/news/t20261118_100.html">重复标题</a>
      <a href="https://different.test/news/t20261118_100.html">站外新闻标题</a>
      <a href="/news/index.html">普通索引页面</a>
    """
    items = parse_article_index(html, "https://official.test/news/", r"/news/t[0-9]{8}_[0-9]+\.html$")
    assert [(item.title, item.url, item.published_at) for item in items] == [
        ("重点政策发布", "https://official.test/news/t20261118_100.html", None)
    ]


def test_digest_filters_p2_and_retries_p1_after_restart(tmp_path):
    from datetime import timedelta

    class ScoreClient(FakeClient):
        def chat(self, messages, **kwargs):
            obj = json.loads(super().chat(messages, **kwargs))
            score = 75 if "eligible" in obj["summary_zh"] else 45
            obj["score_dimensions"] = dict.fromkeys(obj["score_dimensions"], score)
            return json.dumps(obj)

    evening = NOW.replace(hour=21, minute=30)
    clock = [evening - timedelta(minutes=30)]
    sends = []

    def sender(channel, body):
        sends.append(body)
        return len(sends) != 1

    cfg = settings(tmp_path)
    feed = "bbc-world"
    e = MarketIntelligence(cfg, {}, ScoreClient(), sender, lambda: clock[0])
    e.run(data(article("eligible", "https://bbc.example/p1", feed),
               article("excluded", "https://bbc.example/p2", feed)), diagnostics(feed))
    assert not sends
    clock[0] = evening
    e.dispatch_digest()
    assert e.db.execute("SELECT state FROM deliveries").fetchone()[0] == "failed"
    e.close()
    clock[0] += timedelta(minutes=30)
    e = MarketIntelligence(cfg, {}, ScoreClient(), sender, lambda: clock[0])
    e.dispatch_digest()
    e.dispatch_digest()
    assert len(sends) == 2
    assert all("https://bbc.example/p1" in body and "https://bbc.example/p2" not in body for body in sends)
    assert e.db.execute("SELECT state FROM deliveries").fetchone()[0] == "delivered"
    e.close()


def test_new_articles_and_backlog_share_a_bounded_batch(tmp_path):
    from datetime import timedelta

    class RecordingClient(FakeClient):
        def __init__(self):
            self.articles = []

        def chat(self, messages, **kwargs):
            url = json.loads(messages[1]["content"])["article"]["url"]
            self.articles.append(url)
            return super().chat(messages, **kwargs)

    cfg = settings(tmp_path)
    cfg["max_articles_per_run"] = 4
    clock = [NOW - timedelta(hours=2)]
    client = RecordingClient()
    engine = MarketIntelligence(cfg, {}, client, lambda *_: True, lambda: clock[0])
    backlog = [article("old", f"https://old.example/{i}", "sec-press") for i in range(4)]
    engine.ingest(data(*backlog))
    engine.record_health(diagnostics("sec-press"), {"sec-press": "international_official"})
    clock[0] = NOW
    fresh = [article("recent", f"https://fresh.example/{i}", "bbc-world") for i in range(3)]
    engine.ingest(data(*fresh))
    engine.analyze_pending()
    assert len(client.articles) == 4
    assert sum(url.startswith("https://fresh.example/") for url in client.articles) == 3
    engine.analyze_pending()
    assert len(client.articles) == 7
    assert engine.db.execute("SELECT count(*) FROM articles WHERE analysis_state='pending'").fetchone()[0] == 0
    engine.close()


def test_market_crawl_filters_noisy_sources_and_entertainment_at_the_boundary(monkeypatch):
    from market_intelligence.crawl_filter import blocked_titles
    from trendradar.core import load_config

    monkeypatch.setenv("MARKET_ENABLED", "true")
    monkeypatch.setenv("MARKET_FOCUSED_CRAWL", "true")
    config = load_config()
    platform_ids = {platform["id"] for platform in config["PLATFORMS"]}
    rss_ids = {feed["id"] for feed in config["RSS"]["FEEDS"]}
    assert {"weibo", "douyin", "baidu"}.isdisjoint(platform_ids)
    assert {"wallstreetcn-hot", "cls-hot"} <= platform_ids
    assert {"bbc-world", "federal-reserve", "nbs-releases", "yahoo-finance"} <= rss_ids
    assert "hacker-news" not in rss_ids
    should_ignore = blocked_titles(config["MARKET_INTELLIGENCE"])
    assert should_ignore("方浩明在APEC用国产手机圈粉同行")
    assert not should_ignore("APEC公布数字贸易合作政策")


def test_trading_digest_respects_exchange_closures_and_sends_once(tmp_path):
    from datetime import timedelta

    cfg = settings(tmp_path)
    cfg["digest"] = {"trading_times": ["08:30", "11:35", "15:30"]}
    clock = [NOW.replace(hour=11, minute=34)]
    delivered = []
    engine = MarketIntelligence(cfg, {}, FakeClient(), lambda _, body: delivered.append(body) or True,
                                lambda: clock[0])
    engine.run(data(article("chip controls", "https://bbc.example/notice", "bbc-world")),
               diagnostics("bbc-world"))
    assert not delivered
    clock[0] += timedelta(minutes=1)
    engine.dispatch_digest(trading_slot="11:35")
    clock[0] += timedelta(minutes=5)
    engine.dispatch_digest(trading_slot="11:35")
    assert len(delivered) == 1
    assert "11:35" in delivered[0]
    clock[0] = NOW.replace(month=10, day=7, hour=11, minute=34)
    engine.run(data(article("chip policy", "https://bbc.example/holiday", "bbc-world")),
               diagnostics("bbc-world"))
    clock[0] += timedelta(minutes=1)
    engine.dispatch_digest(trading_slot="11:35")
    assert len(delivered) == 1
    engine.close()


def test_company_associations_are_derived_from_evidence_not_model(tmp_path):
    class IndustryClient(FakeClient):
        def chat(self, messages, **kwargs):
            result = json.loads(super().chat(messages, **kwargs))
            result["sectors"][0]["sector"] = "刻蚀设备"
            return json.dumps(result, ensure_ascii=False)

    cfg = settings(tmp_path)
    cfg["market_mapping"] = {"company_analysis": True}
    evening = NOW.replace(hour=21, minute=30)
    messages = []
    engine = MarketIntelligence(cfg, {}, IndustryClient(), lambda _, body: messages.append(body) or True,
                                lambda: evening)
    engine.run(data(article("Chip export controls", "https://bbc.example/controls", "bbc-world")),
               diagnostics("bbc-world"))
    result = json.loads(engine.db.execute("SELECT analysis_json FROM events").fetchone()[0])
    assert {item["code"] for item in result["related_companies"]} == {"002371.SZ", "688012.SH"}
    assert "https://www.naura.com/" in messages[0]
    assert "中芯国际" not in messages[0]
    engine.close()


def test_weekly_calendar_normalizes_publisher_timezone_and_deduplicates(tmp_path):
    from datetime import timedelta

    cfg = settings(tmp_path)
    cfg["digest"] = {"weekly_time": "20:30"}
    clock = [NOW.replace(day=22, hour=20, minute=30)]
    sent = []
    engine = MarketIntelligence(cfg, {}, sender=lambda _, body: sent.append(body) or True, now=lambda: clock[0])
    engine.dispatch_weekly_calendar()
    assert len(sent) == 1
    assert "11-26 03:00 美联储褐皮书" in sent[0]
    assert "11-27 09:30 国家统计局工业经济效益月度报告" in sent[0]
    engine.close()
    clock[0] += timedelta(minutes=5)
    engine = MarketIntelligence(cfg, {}, sender=lambda _, body: sent.append(body) or True, now=lambda: clock[0])
    engine.dispatch_weekly_calendar()
    assert len(sent) == 1
    assert engine.db.execute("SELECT event_id,state FROM deliveries").fetchone()[:] == (
        "calendar:2026-11-23", "delivered")
    engine.close()
