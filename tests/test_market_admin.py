import base64
import json
from pathlib import Path
import threading
from urllib.parse import urlencode

from market_intelligence.admin import AdminServer, password_hash


def test_admin_requires_login_and_origin_and_persists_valid_form(tmp_path):
    import http.client
    import re

    password = "test-only-password"
    salt = bytes(24)
    credential = tmp_path / "password.json"
    credential.write_text(json.dumps({"salt": salt.hex(), "hash": password_hash(password, salt)}))
    state = tmp_path / "settings.json"
    server = AdminServer(("127.0.0.1", 0), {}, state, credential, "https://tr.yumy.space")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    auth = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    try:
        def request(method, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            connection.request(method, "/admin/", body, headers or {})
            answer = connection.getresponse()
            result = answer.status, answer.read().decode()
            connection.close()
            return result

        assert request("GET")[0] == 401
        assert request("GET", headers={"Authorization": "Basic !!!"})[0] == 401
        status, document = request("GET", headers={"Authorization": auth})
        assert status == 200
        token = re.search(r'name="token" type="hidden" value="([^"]+)"', document)[1]
        data = {
            "token": token, "min_priority_score": 75, "urgent_priority_score": 90,
            "max_articles_per_run": 24, "max_articles_per_day": 250,
            "max_digest_events": 6, "ignore_title_terms": "圈粉\n饭圈",
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
