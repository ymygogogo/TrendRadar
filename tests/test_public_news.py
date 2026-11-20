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
        assert not sent and public_site.latest_published_edition(engine.db) is None
        assert engine.send_digest_edition("digest:test", edition, ["wework"])
        assert not engine.send_digest_edition("digest:test", edition, ["wework"])
        assert len(sent) == 1 and "潜在利好" in sent[0] and "#event-1)" in sent[0]
        assert PUBLIC in sent[0] and "internal.example" not in sent[0]
        rendered = uploads[f"digests/{edition['id']}.html"].decode()
        assert 'id="event-1"' in rendered and "&lt;script&gt;" in rendered
        assert "news=12" in rendered and "not-public" not in rendered and "192.168.0.1" not in rendered
        assert "/admin/" not in rendered
        assert f"{edition['id']}.html" in uploads["digests/index.html"].decode()
        assert public_site.latest_published_edition(engine.db)["id"] == edition["id"]
        assert "本期导读" in rendered and 'href="#event-1"' in rendered
    finally:
        engine.close()


def test_hotlist_merges_filtered_ranked_headlines_into_public_home(monkeypatch):
    monkeypatch.setenv("MARKET_DIGEST_BASE_URL", "https://tr.yumy.space")
    results = {"first": {
        "芯片设备订单": {"url": "https://example.com/chips?token=private", "ranks": [3, 2]},
        "明星演唱会用芯片": {"url": "https://example.com/show", "ranks": [1]},
        "经济内部明细": {"url": "https://tr.yumy.space/admin/", "ranks": [1]},
        "国际贸易新闻": {"mobileUrl": "https://example.com/trade", "ranks": [1]},
    }, "second": {"芯片设备订单": {"url": "https://example.com/chips", "ranks": [7]}}}
    hotlist = public_site.hotlist_groups(results, {"first": "财经媒体"}, {})
    page = public_site.render_home(None, None, NOW, hotlist=hotlist).decode()
    assert "热点榜单 2" in page and 'data-view="hot" hidden' in page
    assert page.count("芯片设备订单") == 1 and "榜单第 2 名" in page
    assert "https://example.com/chips" in page and "https://example.com/trade" in page
    assert "演唱会" not in page and "token=private" not in page and "tr.yumy.space" not in page
    assert "导出 Markdown" in page and "导出图片" in page and "/digests/index.html" in page



def test_home_exposes_regions_and_latest_brief_with_stable_event_links():
    groups = [{"word": "科技", "titles": [
        {"title": "芯片设备", "url": "https://example.com/cn", "source_id": "cn", "source_name": "国内财经"},
        {"title": "Chip launch", "url": "https://example.net/en", "source_id": "en", "source_name": "BBC"},
    ]}]
    brief = {"id": "b" * 24, "title": "最新一期", "created_at": NOW.isoformat(), "events": [
        {"title": "<国内三条>", "edition_region": "domestic"},
        {"title": "国内二", "edition_region": "domestic"},
        {"title": "国内三", "edition_region": "domestic"},
        {"title": "海外一", "edition_region": "international"},
    ]}
    page = public_site.render_home(groups, [], NOW, regions={"cn": "domestic", "en": "international"},
                                   brief=brief).decode()
    assert 'data-show="all" aria-pressed="true"' in page
    assert 'data-region="domestic"' in page and 'data-region="international"' in page
    assert "搜索原文、中文参考或来源" in page and "最新一期" in page and "&lt;国内三条&gt;" in page
    assert f'/digests/{brief["id"]}.html#event-4' in page and "国内 3 条 · 海外 1 条" in page


def test_today_news_uses_shanghai_publication_date_and_preserves_previous_rounds(tmp_path):
    engine = MarketIntelligence({"database_path": str(tmp_path / "market.db")}, {})
    try:
        with engine.db:
            engine.db.executemany("""INSERT INTO articles(id,feed_id,title,url,published_at,collected_at)
                VALUES (?,'bbc-world',?,'https://example.net/'||?,?,?)""", [
                ("prior", "旧芯片消息", "prior", "2026-11-18T15:59:00Z", NOW.isoformat()),
                ("first", "芯片早间消息", "first", "2026-11-18T16:01:00Z", NOW.isoformat()),
                ("recent", "芯片最新消息", "recent", "2026-11-19T02:55:00Z", NOW.isoformat()),
                ("future", "芯片未来消息", "future", "2026-11-19T08:00:00Z", NOW.isoformat()),
                ("unknown", "芯片无时间", "unknown", None, NOW.isoformat()),
                ("gossip", "Celebrity gossip on AI", "gossip", "2026-11-19T02:40:00Z", NOW.isoformat()),
            ])
        [tech] = public_site.today_groups(engine.db, engine.settings, NOW)
        assert [item["title"] for item in tech["titles"]] == ["芯片最新消息", "芯片早间消息"]
        assert [item["time_display"] for item in tech["titles"]] == ["10:55", "00:01"]
        page = public_site.render_home(None, None, NOW, today=[tech],
                                       regions={"bbc-world": "international"}).decode()
        assert 'data-show="today" aria-pressed="true">今日新闻 2' in page
        assert 'data-view="today" class="news-grid"' in page
        assert 'data-region="international"' in page and "旧芯片消息" not in page
        empty = public_site.render_home(None, None, NOW, today=[]).decode()
        assert 'data-show="today" aria-pressed="false">今日新闻 0' in empty
    finally:
        engine.close()


def test_home_keeps_original_links_and_labels_chinese_references():
    entries = [
        {"title": "Chip exports rise 8%", "url": "https://bbc.example/chips?utm_source=rss"},
        {"title": "Fed holds rates", "url": "https://example.net/fed"},
        {"title": "Oil & gas", "url": "https://example.net/oil"},
        {"title": "苹果公布 AI 计划", "url": "https://example.net/apple"},
    ]
    references = {"Chip exports rise 8%": "芯片出口增长 8% <估算>"}
    today = [{"word": "经济", "titles": entries}]
    page = public_site.render_home(None, None, NOW, today=today, references=references).decode()
    assert 'href="https://bbc.example/chips?utm_source=rss" rel="noreferrer">Chip exports rise 8%' in page
    assert '>中文参考</span>芯片出口增长 8% &lt;估算&gt;' in page
    assert 'Oil &amp; gas' in page and '苹果公布 AI 计划' in page
    assert "translate.google.com" not in page and "关联事件" not in page
    assert page.count(" data-chinese-reference>") == 1
    assert public_site.foreign_headlines(today, today, "current", today=today, hotlist=today) == [
        "Chip exports rise 8%", "Fed holds rates", "Oil & gas",
    ]


def test_chinese_references_reads_preupgrade_database(tmp_path):
    engine = MarketIntelligence({"database_path": str(tmp_path / "market.db")}, {})
    try:
        with engine.db:
            engine.db.execute("DROP TABLE public_headline_translations")
            engine.db.execute("""INSERT INTO events(id,title_zh,summary_zh,analysis_json,score,priority,verification_status,updated_at)
                VALUES ('event','美联储维持利率','','{}',70,'P1','confirmed',?)""", (NOW.isoformat(),))
            engine.db.execute("""INSERT INTO articles(id,feed_id,title,url,collected_at,event_id)
                VALUES ('article','fed','Fed holds rates','https://example.net/fed',?,'event')""", (NOW.isoformat(),))
            engine.db.execute("INSERT INTO article_chinese_references VALUES ('article','美联储维持利率')")
        assert public_site.chinese_references(engine.db, NOW) == {"Fed holds rates": "美联储维持利率"}
    finally:
        engine.close()
