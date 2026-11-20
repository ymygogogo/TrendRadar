import base64
import json
from pathlib import Path
import threading
from urllib.parse import urlencode

from market_intelligence.admin import AdminServer, password_hash
from market_intelligence.digests import archive_edition


def test_admin_requires_login_and_origin_and_persists_valid_form(tmp_path):
    import http.client
    import re

    password = "test-only-password"
    salt = bytes(24)
    credential = tmp_path / "password.json"
    credential.write_text(json.dumps({"salt": salt.hex(), "hash": password_hash(password, salt)}))
    state = tmp_path / "settings.json"
    snapshot = tmp_path / "status.json"
    snapshot.write_text(json.dumps({"updated_timestamp": 1, "updated_at": "2026-11-18T16:30:00+08:00",
                                    "articles": {"pending": 42}, "events": {}, "deliveries": {},
                                    "analysis_attempts": 50, "sources": []}))
    edition_id = "a" * 24
    archive_edition(snapshot, {
        "id": edition_id, "title": "A 股即时简报", "target": 8, "created_at": "2026-11-18T16:30:00+08:00",
        "events": [{"tier": "watch", "title": "<script>事件标题</script>", "summary": "事件摘要", "priority": "P2",
                    "score": 68, "verification": "unverified", "reported_at": "2026-11-18T16:00:00+08:00",
                    "source": "javascript:alert(1)", "direction": "unclear", "impact": "", "sectors": [],
                    "companies": [], "concepts": ["油气"]}],
    })
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "current.html").write_text("<h1>当前新闻报告</h1>")
    server = AdminServer(("127.0.0.1", 0), {}, state, credential, "https://tr.yumy.space", snapshot, reports)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    auth = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    try:
        def request(method, body=None, headers=None, route="/admin/"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            connection.request(method, route, body, headers or {})
            answer = connection.getresponse()
            result = answer.status, answer.read().decode()
            connection.close()
            return result

        assert request("GET")[0] == 401
        for entry in ("/", "/news/"):
            assert request("GET", route=entry) == (200, "<h1>当前新闻报告</h1>")
        assert request("GET", route="/news/current.html")[0] == 404
        assert request("GET", headers={"Authorization": "Basic !!!"})[0] == 401
        status, document = request("GET", headers={"Authorization": auth})
        assert status == 200
        assert "待分析 42 篇" in document
        assert 'href="/news/"' in document
        detail = "/admin/digests/" + edition_id
        assert request("GET", route=detail)[0] == 401
        code, article = request("GET", headers={"Authorization": auth}, route=detail)
        assert code == 200 and "相关概念与板块" in article and "暂无已核实的相关股票" in article
        assert "&lt;script&gt;事件标题&lt;/script&gt;" in article and "javascript:alert(1)" not in article
        assert request("GET", headers={"Authorization": auth}, route="/admin/digests/" + "b" * 24)[0] == 404
        assert request("GET", route="/admin/metrics")[0] == 401
        assert 'trendradar_articles{state="pending"} 42' in request(
            "GET", headers={"Authorization": auth}, route="/admin/metrics")[1]
        token = re.search(r'name="token" type="hidden" value="([^"]+)"', document)[1]
        data = {
            "token": token, "min_priority_score": 75, "urgent_priority_score": 90,
            "max_articles_per_run": 24, "max_articles_per_day": 250,
            "max_digest_events": 8, "ignore_title_terms": "圈粉\n饭圈",
        }
        assert request("POST", urlencode(data), {"Authorization": auth, "Origin": "https://example.com"})[0] == 403
        assert not state.exists()
        headers = {"Authorization": auth, "Origin": "https://tr.yumy.space"}
        assert request("POST", urlencode({**data, "token": "wrong"}), headers)[0] == 403
        assert request("POST", urlencode({**data, "min_priority_score": 99}), headers)[0] == 400
        assert not state.exists()
        assert request("POST", urlencode(data), headers)[0] == 200
        assert json.loads(state.read_text())["ignore_title_terms"] == ["圈粉", "饭圈"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_password_provision_writes_hash_only(monkeypatch, tmp_path):
    import getpass

    from market_intelligence.admin import set_password

    destination = Path(tmp_path) / "private" / "password.json"
    monkeypatch.setattr(getpass, "getpass", lambda _: "local-test-credential")
    set_password(destination)
    stored = json.loads(destination.read_text())
    assert stored["hash"] == password_hash("local-test-credential", bytes.fromhex(stored["salt"]))
    assert "local-test-credential" not in destination.read_text()
    assert destination.stat().st_mode & 0o777 == 0o600


def test_network_access_uses_peer_and_origin_for_password_free_admin(tmp_path):
    from http.client import HTTPConnection

    origin = "https://tr.yumy.space"
    public = "https://news.yumy.space/"
    state = tmp_path / "settings.json"
    snapshot = tmp_path / "status.json"
    snapshot.write_text(json.dumps({
        "updated_at": "2026-11-19T15:00:00+08:00", "articles": {"pending": 4},
        "analysis_queue": {"oldest_at": "2026-11-19T12:00:00+08:00", "last_hour": 3},
        "sources": [["good-feed", 5, 5, 0, 25], ["failed-feed", 5, 2, 3, 400]],
        "model": {"deployed_model": "openai/original", "model": "openai/original",
                  "endpoint_host": "api.example.net", "max_tokens": 5000, "timeout": 120,
                  "analysis_enabled": True, "credential_configured": True},
        "notification_channels": {"wework": {"enabled": True, "configured": True, "counts": {}, "latest": None}},
        "priority_events": [
            {"title": f"重点事件第 {i} 条", "summary": "正文摘要", "priority": "P1",
             "score": 72, "verification": "confirmed", "source": "https://example.net/news",
             "reported_at": "2026-11-18T14:00:00+08:00", "updated_at": "2026-11-18T16:00:00+08:00"}
            for i in range(1, 22)
        ],
        "model_usage": [{"date": "2026-11-19", "attempts": 2, "input_tokens": 500, "output_tokens": 57,
                         "input_samples": 1, "output_samples": 1, "responses": 1}],
    }))
    server = AdminServer(("127.0.0.1", 0), {}, state, tmp_path / "missing-password.json", origin,
                         snapshot, news_url=public, trusted_networks=["127.0.0.1/32"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, route, *, client="127.0.0.1", headers=None, data=None):
        with_connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5,
                                         source_address=(client, 0))
        try:
            with_connection.request(method, route, data, {"Host": "tr.yumy.space", **(headers or {})})
            response = with_connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            with_connection.close()

    try:
        for route, expected in (("/", "/admin/"), ("/admin", "/admin/"), ("/news/", public)):
            code, headers, _ = request("GET", route)
            assert code == 302 and headers["Location"] == expected
        code, headers, body = request("GET", "/admin/")
        assert code == 200 and "WWW-Authenticate" not in headers
        assert f'href="{public}"' in body
        assert "1 个来源异常" in body and "查看 1 个正常来源" in body
        assert body.index("failed-feed") < body.index("good-feed")
        assert "近 1 小时采集且待分析 3 篇" in body and "今日 2 / 250 次" in body
        assert "模型与用量" in body and 'value="openai/original"' in body
        assert 'aria-label="最近 7 天输入与输出 Token 曲线"' in body and "api.example.net" in body
        assert '<strong>今日 500</strong>' in body and "查看每日用量" in body
        assert "立即推送" in body and "投递至 企业微信" in body
        assert request("GET", "/admin/", headers={"Host": "foreign.example"})[0] == 403
        spoofed = {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"}
        for route in ("/admin/", "/admin/metrics", "/admin/digests/" + "a" * 24):
            assert request("GET", route, client="127.0.0.2", headers=spoofed)[0] == 403
        assert request("GET", "/admin/events", client="127.0.0.2", headers=spoofed)[0] == 403
        assert "查看 21 件 P0 / P1 事件" in body and "查看更早的事件" in body
        code, _, events = request("GET", "/admin/events?priority=P1")
        assert code == 200 and "重点事件第 1 条" in events and "重点事件第 21 条" not in events
        assert "/admin/events?priority=P1&amp;page=2" in events
        code, _, events = request("GET", "/admin/events?priority=P1&page=2")
        assert code == 200 and "重点事件第 21 条" in events and "重点事件第 1 条" not in events
        assert "保留期内暂无符合条件的事件" in request("GET", "/admin/events?priority=P0")[2]
        assert request("GET", "/admin/events?priority=P2")[0] == 400
        data = urlencode({"token": server.token, "min_priority_score": 75, "urgent_priority_score": 90,
                          "max_articles_per_run": 24, "max_articles_per_day": 250,
                          "max_digest_events": 8, "ignore_title_terms": "饭圈"})
        assert request("POST", "/admin/", client="127.0.0.2",
                       headers={"Origin": origin, **spoofed}, data=data)[0] == 403
        assert request("POST", "/admin/", headers={"Origin": "https://news.yumy.space"}, data=data)[0] == 403
        assert not state.exists()
        assert request("POST", "/admin/", headers={"Origin": origin}, data=data)[0] == 200
        assert json.loads(state.read_text())["ignore_title_terms"] == ["饭圈"]
        headers = {"Origin": origin}
        model = {"token": server.token, "model": "openai/replacement", "max_tokens": 6000, "timeout": 90}
        assert request("POST", "/admin/model", headers=headers, data=urlencode({**model, "model": "other/replacement"}))[0] == 400
        assert request("POST", "/admin/model", headers={"Origin": public.rstrip("/")}, data=urlencode(model))[0] == 403
        assert not state.with_name("model.json").exists()
        code, _, result = request("POST", "/admin/model", headers=headers, data=urlencode(model))
        assert code == 200 and 'value="openai/replacement"' in result
        assert json.loads(state.read_text())["ignore_title_terms"] == ["饭圈"]
        from market_intelligence.model_settings import resolve
        base = {"MODEL": "openai/original", "API_KEY": "private-secret", "API_BASE": "https://api.example.net/v1"}
        assert resolve(base, state) == {**base, "MODEL": "openai/replacement", "MAX_TOKENS": 6000, "TIMEOUT": 90}
        assert "private-secret" not in state.with_name("model.json").read_text()
        assert "推送设置" in body and 'name="urgent_enabled" value="1" checked' in body
        manual = "/admin/notifications/send-now"
        manual_data = urlencode({"token": server.token, "request_id": "a" * 32})
        assert request("GET", manual)[0] == 404
        assert request("POST", manual, client="127.0.0.2", headers={"Origin": origin, **spoofed}, data=manual_data)[0] == 403
        assert request("POST", manual, headers={"Origin": public.rstrip("/")}, data=manual_data)[0] == 403
        assert not state.with_name("manual-digest.json").exists()
        code, response_headers, _ = request("POST", manual, headers=headers, data=manual_data)
        assert code == 302 and response_headers["Location"] == "/admin/#push-settings"
        queued = state.with_name("manual-digest.json").read_bytes()
        assert request("POST", manual, headers=headers, data=manual_data)[0] == 302
        assert state.with_name("manual-digest.json").read_bytes() == queued
        assert "下一轮调度处理" in request("GET", "/admin/")[2]
        policy = {"token": server.token, "daily_times": "18:41, 21:30", "trading_times": "08:30，15:30",
                  "weekly_time": "20:30", "opening_time": "09:40", "max_per_hour": 4,
                  "cooldown_minutes": 30, "urgent_max_age_hours": 12}
        route = "/admin/notifications"
        assert request("POST", route, headers={"Origin": public.rstrip("/")}, data=urlencode(policy))[0] == 403
        assert request("POST", route, headers=headers, data=urlencode({**policy, "token": "invalid"}))[0] == 403
        code, _, response = request("POST", route, headers=headers, data=urlencode({**policy, "daily_times": "08:30"}))
        assert code == 400 and "时间不能重叠" in response
        assert not state.with_name("notifications.json").exists()
        code, _, response = request("POST", route, headers=headers, data=urlencode(policy))
        assert code == 200 and 'value="18:41, 21:30"' in response
        parsed = json.loads(state.with_name("notifications.json").read_text())
        assert parsed["trading_times"] == ["08:30", "15:30"] and parsed["urgent_enabled"] is False
        assert state.exists() and state.with_name("model.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
