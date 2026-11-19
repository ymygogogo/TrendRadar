from datetime import datetime
from types import SimpleNamespace

from market_intelligence import public_site
from market_intelligence.engine import MarketIntelligence
from trendradar.report.html import render_html_content
from trendradar.report.rss_views import rss_groups


NOW = datetime.fromisoformat("2026-11-19T11:30:00+08:00")
PUBLIC = "https://news.yumy.space"


def test_rss_is_one_section_and_deduplicates_with_empty_and_incremental_views():
    item = {"title": "芯片消息", "url": "https://example.net/chips", "source_name": "来源"}
    later = {"title": "经济新闻", "url": "https://example.net/economy", "source_name": "媒体"}
    current = [{"word": "科技", "titles": [item]}, {"word": "经济", "titles": [item, later]}]
    new = [{"word": "科技", "titles": [item]}]
    recent, all_groups = rss_groups(current, new, "current")
    assert sum(g["count"] for g in recent) == 1
    assert sum(g["count"] for g in all_groups) == 2
    assert all_groups[0]["titles"][0]["is_new"]
    assert rss_groups(current, [], "daily")[0] == []
    assert rss_groups(new, [], "incremental")[1] == []
    screen = render_html_content({"stats": [], "failed_ids": [], "new_titles": {}}, 2, "current", rss_items=current, rss_new_items=new)
    assert screen.count('<section class="rss-news">') == 1
    assert 'data-rss-panel="all" hidden' in screen
    assert "RSS 新增更新" not in screen and "RSS 订阅更新" not in screen
    assert "当前全部 2" in screen
    assert "今日全部 2" in public_site.render_home(current, [], NOW, "daily").decode()


def test_public_readback_gates_wecom_and_publishes_only_public_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_PUBLIC_NEWS_URL", PUBLIC)
    monkeypatch.setenv("MARKET_PUBLIC_NEWS_BUCKET", "test-news-bucket")
    monkeypatch.setenv("MARKET_STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("MARKET_DIGEST_BASE_URL", "https://internal.example")
    uploads, sent = {}, []
    replies = [False, True]

    def get(url, **_):
        body = uploads[url.removeprefix(PUBLIC + "/")] if replies.pop(0) else b"wrong"
        return SimpleNamespace(status_code=200, content=body)

    monkeypatch.setattr(public_site, "_upload", lambda key, body, cache: uploads.update({key: body}))
    monkeypatch.setattr(public_site.requests, "get", get)
    engine = MarketIntelligence({"database_path": str(tmp_path / "market.db")}, {},
                                sender=lambda _, message: sent.append(message) or True, now=lambda: NOW)
    edition = {"created_at": NOW.isoformat(), "title": "新闻简报", "id": "a" * 24, "target": 8,
               "events": [{"title": "<script>股票观察</script>", "summary": "消息", "source":
                           "https://example.net/a?token=not-public&news=12", "other_sources":["http://192.168.0.1/a"],
                           "tier": "main", "priority": "P1", "score": 72, "verification": "confirmed",
                           "reported_at": NOW.isoformat(), "impact": "需求可能上升",
                           "direction": "potential_positive", "event_id": "x", "revision": 1}]}
    try:
        assert not engine.send_digest_edition("digest:test", edition, ["wework"])
        assert not sent
        assert engine.send_digest_edition("digest:test", edition, ["wework"])
        assert not engine.send_digest_edition("digest:test", edition, ["wework"])
        assert len(sent) == 1 and "潜在利好" in sent[0] and "#event-1)" in sent[0]
        assert PUBLIC in sent[0] and "internal.example" not in sent[0]
        rendered = uploads[f"digests/{edition['id']}.html"].decode()
        assert 'id="event-1"' in rendered and "&lt;script&gt;" in rendered
        assert "news=12" in rendered and "not-public" not in rendered and "192.168.0.1" not in rendered
        assert "/admin/" not in rendered
        assert f"{edition['id']}.html" in uploads["digests/index.html"].decode()
    finally:
        engine.close()
