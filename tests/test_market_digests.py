from datetime import datetime, timedelta
import json
import re

from market_intelligence import digests
from market_intelligence.engine import MarketIntelligence


NOW = datetime.fromisoformat("2026-11-18T21:30:00+08:00")
KEY = "digest:" + NOW.isoformat()


def add_event(db, event_id, score, age, *, actionable=True, priority=None, verification="unverified",
              title="中国市场供需观察" * 25, categories=("能源",)):
    stamp = (NOW - timedelta(hours=age)).isoformat()
    sector = {"sector": "油气开采", "direction": "potential_positive", "reason": "原油供应可能收紧"}
    analysis = {
        "sectors": [sector] if actionable else [],
        "direction": "potential_positive" if actionable else "unclear",
        "impact_mechanism": "供给变化可能影响原油价格",
        "categories": list(categories),
    }
    db.execute("""INSERT INTO events(id,title_zh,summary_zh,analysis_json,score,priority,verification_status,
        first_reported_at,latest_reported_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_id, title, "一条公开消息", json.dumps(analysis), score,
         priority or ("P1" if score >= 70 else "P2"), verification, stamp, stamp, stamp))
    for i in (1, 2):
        db.execute("INSERT INTO articles (id,feed_id,title,url,collected_at,event_id) VALUES (?,?,?,?,?,?)",
                   (f"{event_id}-{i}", "feed", "原文", f"https://news.example/{event_id}-{i}", stamp, event_id))
    db.commit()


def test_digest_target_selection_evidence_and_delivery_retry(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    origin = "https://tr.yumy.space"
    monkeypatch.setenv("MARKET_DIGEST_BASE_URL", origin)
    monkeypatch.setenv("MARKET_STATUS_PATH", str(status))
    clock = [NOW]
    config = {
        "database_path": str(tmp_path / "market.db"),
        "notification": {"enabled": True, "channels": ["wework"]},
        "market_mapping": {"company_analysis": True},
    }
    messages = []

    def sender(channel, message):
        assert channel == "wework"
        messages.append(message)
        return len(messages) > 1

    engine = MarketIntelligence(config, {}, sender=sender, now=lambda: clock[0])
    add_event(engine.db, "main", 75, 1)
    for i in range(6):
        add_event(engine.db, f"watch{i}", 68 - i, 2)
    add_event(engine.db, "not-relevant", 69, 1, actionable=False, categories=("娱乐",), title="明星红毯")
    add_event(engine.db, "older-sent", 84, 26)
    add_event(engine.db, "older", 80, 30)
    add_event(engine.db, "too-old", 81, 50)
    add_event(engine.db, "alert", 93, 2, priority="P0", verification="confirmed")
    with engine.db:
        engine.db.execute("INSERT INTO digest_entries VALUES ('digest:previous','older-sent',1)")
        engine.db.executemany("INSERT INTO deliveries VALUES (?,1,'wework','delivered',1,?,NULL)",
                              [("digest:previous", NOW.isoformat()), ("alert", NOW.isoformat())])
    engine.dispatch_digest()
    edition = digests.read_edition(status, digests.public_id(KEY))
    assert [event["event_id"] for event in edition["events"]] == [
        "main", *(f"watch{i}" for i in range(6)), "older",
    ]
    assert edition["events"][1]["tier"] == "main"
    assert edition["events"][-1]["tier"] == "extended"
    assert edition["events"][0]["concepts"] == ["油气开采", "能源"]
    assert edition["events"][0]["companies"][0]["code"] == "601857.SH"
    assert digests.read_edition(status, "../status") is None
    assert len(messages) == 1 and "本期 8 条" in messages[0]
    assert origin + "/admin/digests/" + edition["id"] in messages[0]
    add_event(engine.db, "new", 82, 0)
    engine.close()

    clock[0] += timedelta(minutes=30)
    engine = MarketIntelligence(config, {}, sender=sender, now=lambda: clock[0])
    try:
        engine.dispatch_digest()
        engine.dispatch_digest()
        assert messages == [messages[0], messages[0]]
        assert tuple(engine.db.execute("SELECT state,attempts FROM deliveries WHERE event_id=?", (KEY,)).fetchone()) == (
            "delivered", 2,
        )
        assert digests.read_edition(status, edition["id"]) == edition
        engine.db.execute("DELETE FROM events WHERE id='new'")
        engine.db.execute("UPDATE events SET revision=2 WHERE id='older-sent'")
        new_selection = digests.select_events(engine.db, config, engine.companies, NOW, NOW - timedelta(hours=24))
        assert any(item["event_id"] == "older-sent" for item in new_selection)
    finally:
        engine.close()


def test_digest_message_fits_all_long_titles_and_shows_shortfall():
    edition = {
        "id": "a" * 24, "title": "A 股即时简报", "target": 8, "created_at": NOW.isoformat(),
        "events": [{"title": "很长的中文和表情📈" * 60, "tier": "main", "priority": "P1"} for _ in range(20)],
    }
    message = digests.digest_message(edition, "https://tr.yumy.space")
    assert len(message.encode("utf-8")) <= digests.MESSAGE_BYTES
    assert len(re.findall(r"^\d+\. P1｜", message, re.M)) == 20
    assert message.endswith("/admin/digests/" + edition["id"] + ")")
    assert message.count("A股：影响暂不明确") == 20
    assert all(f"#event-{number})" in message for number in range(1, 21))
    for event in edition["events"]:
        event.update(topic="economy", heat=99, tier="extended", reported_at=NOW.isoformat(),
                     direction="potentially_mixed", impact="可能通过供应链成本传导到制造业" * 60)
    public = digests.digest_message(edition, "https://news.yumy.space", public=True)
    assert len(public.encode()) <= digests.MESSAGE_BYTES and public.count("A股：影响分化") == 20
    assert "/digests/" + edition["id"] + ".html#event-20)" in public
    edition["events"] = edition["events"][:2]
    assert "距目标还差 6 条" in digests.digest_message(edition, "https://tr.yumy.space")


def test_hotlist_evidence_ranks_non_market_technology_and_deduplicates_publishers(tmp_path):
    from market_intelligence.engine import digest

    config = {"database_path": str(tmp_path / "heat.db"), "domestic_platforms": ["cls-hot"],
              "digest": {"publisher_groups": {"BBC": ["bbc-world", "bbc-business"]}}}
    engine = MarketIntelligence(config, {}, now=lambda: NOW)
    try:
        add_event(engine.db, "hot-tech", 35, 2, actionable=False, title="开源人工智能取得技术突破", categories=("tech",))
        add_event(engine.db, "investment", 83, 1, title="固定资产投资形势", categories=("economy",))
        add_event(engine.db, "show", 85, 0, title="全球明星红毯", categories=("entertainment",))
        engine.ingest_hotlist({"cls-hot": {
            "科技突破": {"url": "https://news.example/tech-ranking", "ranks": [2, 8, 0]},
        }}, {"cls-hot": "财联社"})
        with engine.db:
            engine.db.execute("UPDATE articles SET event_id='hot-tech' WHERE id=?",
                              (digest("https://news.example/tech-ranking"),))
            engine.db.execute("UPDATE articles SET feed_id='bbc-world' WHERE id='hot-tech-1'")
            engine.db.execute("UPDATE articles SET feed_id='bbc-business' WHERE id='hot-tech-2'")
        events = digests.select_events(engine.db, config, [], NOW, NOW - timedelta(hours=1))
        assert [event["event_id"] for event in events] == ["investment", "hot-tech"]
        assert events[1]["tier"] == "watch"
        assert events[1]["hotlist_rank"] == 2 and events[1]["outlet_count"] == 2
        assert not events[1]["companies"]
        assert events[1]["heat"] > events[0]["heat"]
        all_recent = digests.select_events(engine.db, config, [], NOW, NOW - timedelta(hours=24))
        assert [event["event_id"] for event in all_recent] == ["hot-tech", "investment"]
    finally:
        engine.close()


def test_digest_reserves_top_item_per_available_topic(tmp_path):
    engine = MarketIntelligence({"database_path": str(tmp_path / "coverage.db"),
                                 "market_mapping": {"company_analysis": True}}, {}, now=lambda: NOW)
    try:
        for index in range(8):
            add_event(engine.db, f"economy{index}", 70, 1 + index / 10, title="金融通胀新闻")
        add_event(engine.db, "tech-new", 25, 5, actionable=False, title="芯片技术突破")
        add_event(engine.db, "tech-old", 25, 8, actionable=False, title="航天科技")
        add_event(engine.db, "world", 50, 6, title="国际地震救援")
        events = digests.select_events(engine.db, engine.settings, engine.companies, NOW, NOW - timedelta(hours=24))
        assert len(events) == 8
        assert {event["topic"] for event in events} == {"tech", "economy", "world"}
        assert [event["event_id"] for event in events[-2:]] == ["tech-new", "world"]
        assert [item["code"] for item in digests.match_companies(engine.companies, [{"sector": "银行"}])] == [
            "601398.SH"
        ]
    finally:
        engine.close()


def test_same_named_speech_keeps_one_slot_and_both_sources(tmp_path):
    engine = MarketIntelligence({"database_path": str(tmp_path / "speeches.db")}, {}, now=lambda: NOW)
    speech = "习近平出席亚太经合组织工商领导人峰会开幕式并发表主旨演讲"
    transcript = "习近平在亚太经合组织工商领导人峰会上的主旨演讲（全文）"
    distinct = "习近平在亚太经合组织领导人非正式会议上的主旨演讲（全文）"
    try:
        add_event(engine.db, "speech", 70, 1, title=speech, categories=("全球",))
        add_event(engine.db, "transcript", 72, 3, title=transcript, categories=("全球",))
        add_event(engine.db, "different", 72, 3, title=distinct, categories=("全球",))
        with engine.db:
            engine.db.execute("UPDATE articles SET feed_id='another-outlet' WHERE event_id='transcript'")
        edition = digests.make_edition(engine, KEY, NOW, NOW - timedelta(hours=24), "热点新闻简报")
        edition = digests.freeze_edition(engine.db, KEY, edition)
        assert [event["event_id"] for event in edition["events"]] == ["speech", "different"]
        combined = edition["events"][0]
        assert combined["outlet_count"] == 2
        assert combined["other_sources"] == ["https://news.example/transcript-1"]
        assert combined["related_versions"] == [["transcript", 1]]
        assert {row[0] for row in engine.db.execute("SELECT event_id FROM digest_entries")} == {
            "speech", "transcript", "different",
        }
    finally:
        engine.close()
