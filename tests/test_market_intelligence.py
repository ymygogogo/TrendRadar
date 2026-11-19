"""Event and notification invariants with synthetic articles; no live delivery."""
import json
from datetime import datetime, timedelta
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
    assert "热点新闻简报" in sends[0]
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


def test_digest_filters_irrelevant_p2_and_retries_p1_after_restart(tmp_path):
    from datetime import timedelta

    class ScoreClient(FakeClient):
        def chat(self, messages, **kwargs):
            obj = json.loads(super().chat(messages, **kwargs))
            score = 75 if "eligible" in obj["summary_zh"] else 45
            if score < 70:
                obj["direction"] = "unclear"
                obj["sectors"] = []
                obj["categories"] = ["entertainment"]
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
    from market_intelligence.digests import stored_edition
    key = f"digest:{evening.replace(second=0, microsecond=0).isoformat()}"
    companies = stored_edition(engine.db, key)["events"][0]["companies"]
    assert any(item["source"].startswith("https://www.naura.com/") for item in companies)
    assert all(item["name"] != "中芯国际" for item in companies)
    assert "芯片出口限制" in messages[0]
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


def test_analysis_admission_filters_at_ingest_and_rechecks_existing_pending(tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from market_intelligence.engine import MarketIntelligence

    now = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
    path = tmp_path / "admission.db"
    admission = {
        "enabled": False, "always_analyze_categories": ["domestic_official"],
        "record_only_sources": ["hacker-news"], "media_terms": ["chip exports", "加息", "war"],
    }
    settings = {"database_path": str(path), "event_analysis": {"enabled": True, "admission": admission},
                "max_articles_per_run": 0}

    def item(slug, title, summary=""):
        return SimpleNamespace(title=title, summary=summary, published_at="", url=f"https://example.org/{slug}")

    def captured(**groups):
        return SimpleNamespace(items=groups)

    initial = MarketIntelligence(settings, {}, client=object(), now=lambda: now - timedelta(days=3))
    initial.ingest(captured(bbc=[item("old", "News about chip exports")]))
    initial.close()
    initial = MarketIntelligence(settings, {}, now=lambda: now)
    initial.ingest(captured(bbc=[item("football", "Football highlights")]))
    initial.close()
    admission["enabled"] = True
    engine = MarketIntelligence(settings, {}, client=object(), now=lambda: now)
    engine.record_health(
        {"pbc": {"name": "PBC", "url": "https://example.org/pbc", "success": True, "duration_ms": 5,
                 "latest_published_at": "", "error": None}},
        {"pbc": "domestic_official"},
    )
    engine.ingest(captured(**{
        "bbc": [item("award", "School awards"), item("controls", "New regulations", "Limits on chip exports")],
        "cls": [item("zh", "美联储宣布加息")],
        "hacker-news": [item("tool", "Build a tool about chip exports"), item("duplicate", "Chip exports")],
        "pbc": [item("official", "新规发布")],
    }))
    engine.ingest(captured(bbc=[item("duplicate", "Rules on chip exports")]))
    engine.analyze_pending()
    actual = {url.rsplit("/", 1)[-1]: (state, source) for url, state, source in
              engine.db.execute("SELECT url,analysis_state,feed_id FROM articles")}
    assert actual == {
        "old": ("expired", "bbc"), "football": ("filtered", "bbc"), "award": ("filtered", "bbc"),
        "controls": ("pending", "bbc"), "zh": ("pending", "cls"), "tool": ("filtered", "hacker-news"),
        "duplicate": ("pending", "bbc"), "official": ("pending", "pbc"),
    }
    assert engine.db.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0
    engine.close()

    admission["enabled"] = False
    engine = MarketIntelligence(settings, {}, client=object(), now=lambda: now)
    try:
        engine.analyze_pending()
        assert dict(engine.db.execute("SELECT analysis_state,count(*) FROM articles GROUP BY 1")) == {
            "expired": 1, "pending": 7,
        }
    finally:
        engine.close()


def test_model_usage_records_reported_tokens_even_when_analysis_json_is_invalid(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from market_intelligence.status import snapshot, publish_current
    from trendradar.ai.client import AIClient

    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="malformed JSON"))],
                               usage={"prompt_tokens": 100, "completion_tokens": 37})
    monkeypatch.setattr("trendradar.ai.client.completion", lambda **_: response)
    ai = {"MODEL": "openai/test-model", "API_KEY": "test-only-secret", "API_BASE": "https://api.example.net/v1"}
    cfg = settings(tmp_path)
    engine = MarketIntelligence(cfg, {"AI": ai, "MARKET_AI": ai}, client=AIClient(ai), now=lambda: NOW)
    try:
        from trendradar.storage.base import RSSItem

        article = RSSItem("Chip launch", "test", url="https://example.net/chip", published_at=NOW.isoformat())
        engine.ingest(SimpleNamespace(items={"test": [article]}))
        engine.analyze_pending()
        [sample] = list(engine.db.execute("SELECT model,input_tokens,output_tokens FROM model_usage"))
        assert tuple(sample) == ("openai/test-model", 100, 37)
        day = snapshot(engine.db, NOW)["model_usage"][-1]
        assert (day["attempts"], day["input_tokens"], day["output_tokens"]) == (1, 100, 37)
        assert snapshot(engine.db, NOW)["model_usage"][0]["input_tokens"] is None
        dest = tmp_path / "status.json"
        monkeypatch.setenv("MARKET_STATUS_PATH", str(dest))
        publish_current(engine)
        assert "test-only-secret" not in dest.read_text()
        assert json.loads(dest.read_text())["model"]["endpoint_host"] == "api.example.net"
    finally:
        engine.close()


def test_live_notification_schedule_loads_saved_slots_and_only_delivers_once(tmp_path, monkeypatch):
    from market_intelligence.notification_settings import visible, write
    from market_intelligence.trading_calendar import cron_entries, due_jobs
    from trendradar.core import load_config

    location = tmp_path / "settings.json"
    monkeypatch.setenv("MARKET_OPERATOR_SETTINGS", str(location))
    monkeypatch.setenv("RUN_MODE", "cron")
    monkeypatch.setenv("MARKET_ENABLED", "true")
    monkeypatch.setenv("MARKET_NOTIFICATION_ENABLED", "true")
    base = load_config()["MARKET_INTELLIGENCE"]
    custom = {**visible(base), "daily_times": ["18:41"], "trading_times": ["11:36"],
              "weekly_time": "20:31", "opening_time": "09:42", "urgent_enabled": False}
    write(location, custom)
    loaded = load_config()
    cfg = loaded["MARKET_INTELLIGENCE"]
    cfg["database_path"] = str(tmp_path / "market.db")
    assert loaded["MARKET_LIVE_SCHEDULE"]
    assert cron_entries(cfg, live_schedule=True)[0] == "* * * * * cd /app && python -m market_intelligence schedule-tick"
    assert list(due_jobs(cfg, NOW.replace(hour=18, minute=40))) == []
    assert list(due_jobs(cfg, NOW.replace(hour=18, minute=41))) == [("daily", "18:41")]
    assert list(due_jobs(cfg, NOW.replace(hour=18, minute=46))) == [("daily", "18:41")]
    assert list(due_jobs(cfg, NOW.replace(hour=11, minute=36))) == [("trading", "11:36")]
    assert list(due_jobs(cfg, (NOW + timedelta(days=3)).replace(hour=11, minute=36))) == []
    assert list(due_jobs(cfg, (NOW + timedelta(days=4)).replace(hour=20, minute=31))) == [("weekly", None)]
    assert list(due_jobs(cfg, NOW.replace(hour=9, minute=42))) == [("opening", None)]
    write(location, {**custom, "weekly_time": "", "opening_time": ""})
    disabled = load_config()["MARKET_INTELLIGENCE"]
    assert list(due_jobs(disabled, (NOW + timedelta(days=4)).replace(hour=20, minute=31))) == []
    assert list(due_jobs(disabled, NOW.replace(hour=9, minute=42))) == []
    assert disabled["market_open"]["enabled"] is True
    now = [NOW.replace(hour=18, minute=41)]
    sent = []
    engine = MarketIntelligence(cfg, loaded, client=FakeClient(), sender=lambda channel, body: sent.append(body) or True,
                                now=lambda: now[0])
    try:
        item = article("Chip export restrictions", "https://official.example/chip", "federal-reserve")
        engine.record_health(diagnostics("federal-reserve"), {"federal-reserve": "international_official"})
        engine.ingest(data(item))
        engine.analyze_pending()
        engine.dispatch_urgent()
        assert not sent
        engine.dispatch_digest(daily_slot="18:41")
        now[0] += timedelta(minutes=5)
        engine.dispatch_digest(daily_slot="18:41")
        assert len(sent) == 1 and "热点新闻简报" in sent[0]
    finally:
        engine.close()


def test_urgent_budget_excludes_scheduled_digests_and_checks_current_threshold(tmp_path):
    cfg = settings(tmp_path)
    cfg["notification"]["max_per_hour"] = 1
    sends = []
    engine = MarketIntelligence(cfg, {}, FakeClient(), lambda _, body: sends.append(body) or True, lambda: NOW)
    try:
        item = article("Chip export restrictions", "https://official.example/chip", "federal-reserve")
        engine.record_health(diagnostics("federal-reserve"), {"federal-reserve": "international_official"})
        engine.ingest(data(item))
        engine.analyze_pending()
        with engine.db:
            engine.db.execute("INSERT INTO deliveries VALUES ('digest:previous',1,'feishu','delivered',1,?,NULL)",
                              (NOW.isoformat(),))
        score = engine.db.execute("SELECT score FROM events").fetchone()[0]
        cfg["event_analysis"]["urgent_priority_score"] = score + 1
        engine.dispatch_urgent()
        assert not sends
        cfg["event_analysis"]["urgent_priority_score"] = score
        engine.dispatch_urgent()
        engine.dispatch_urgent()
        assert len(sends) == 1
    finally:
        engine.close()


def test_headline_translation_reuses_analysis_call_and_survives_restart(tmp_path):
    from market_intelligence import public_site

    class TranslationClient(FakeClient):
        calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            prompt = json.loads(messages[1]["content"])
            assert "headline_zh" in prompt["response_example"]
            result = json.loads(super().chat(messages, **kwargs))
            result["headline_zh"] = "美国新增芯片出口限制"
            return json.dumps(result, ensure_ascii=False)

    config = settings(tmp_path)
    client = TranslationClient()
    engine = MarketIntelligence(config, {}, client, now=lambda: NOW)
    item = article("US adds chip export restrictions", "https://example.net/chip", "bbc-world")
    engine.ingest(data(item))
    engine.analyze_pending()
    engine.close()
    assert client.calls == 1
    reopened = MarketIntelligence(config, {}, now=lambda: NOW)
    try:
        assert public_site.chinese_references(reopened.db, NOW) == {
            (item.url, item.title): ("中文参考", "美国新增芯片出口限制"),
        }
        assert "headline_zh" not in json.loads(reopened.db.execute("SELECT analysis_json FROM events").fetchone()[0])
    finally:
        reopened.close()
