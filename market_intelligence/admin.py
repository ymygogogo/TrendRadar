"""HTTPS operator form for a small set of non-secret market settings."""

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import parse_qs

from . import operator_settings, status


FIELDS = (
    ("min_priority_score", "简报最低分"),
    ("urgent_priority_score", "即时预警最低分"),
    ("max_articles_per_run", "每轮分析上限"),
    ("max_articles_per_day", "每日分析上限"),
    ("max_digest_events", "每份简报条数"),
)
ITERATIONS = 300_000
MAX_FORM_BYTES = 12_288


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def set_password(path):
    password = getpass.getpass("新的 TrendRadar 管理密码: ")
    if len(password) < 12 or password != getpass.getpass("再次输入: "):
        raise ValueError("密码至少 12 字符且两次输入必须一致")
    salt = secrets.token_bytes(24)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}-{secrets.token_hex(8)}")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            json.dump({"salt": salt.hex(), "hash": password_hash(password, salt)}, handle)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def visible_values(settings):
    analysis = settings.get("event_analysis", {})
    return {
        "min_priority_score": analysis.get("min_priority_score", 70),
        "urgent_priority_score": analysis.get("urgent_priority_score", 85),
        "max_articles_per_run": settings.get("max_articles_per_run", 12),
        "max_articles_per_day": settings.get("max_articles_per_day", 250),
        "max_digest_events": settings.get("digest", {}).get("max_events", 8),
        "ignore_title_terms": settings.get("crawl_filter", {}).get("ignore_title_terms", []),
    }


def render(settings, token, message="", snapshot=None):
    numbers = "".join(
        f'<label>{label}<input name="{key}" type="number" required value="{settings[key]}"></label>'
        for key, label in FIELDS
    )
    terms = escape("\n".join(settings["ignore_title_terms"]))
    state = "<p>等待采集状态快照。</p>"
    if snapshot:
        counts = snapshot["articles"]
        state = (f"<p>上次采集 {escape(snapshot['updated_at'])}<br>"
                 f"待分析 {int(counts.get('pending',0))} 篇 · 已分析 {int(counts.get('done',0))} 篇"
                 ' · <a href="/admin/metrics">采集指标</a></p>')
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>TrendRadar 管理</title>
<style>
body{{font:16px system-ui,sans-serif;background:#111827;color:#f3f4f6;max-width:44rem;margin:3rem auto;padding:0 1rem}}
h1{{font-size:1.65rem}}a{{color:#7dd3fc}}form{{background:#1f2937;padding:1.5rem;border-radius:.75rem}}
label{{display:block;margin-bottom:1rem}}input,textarea{{display:block;color:inherit;background:#111827;border:1px solid #64748b;
padding:.5rem .75rem;margin-top:.4rem;border-radius:.3rem;font:inherit}}textarea{{box-sizing:border-box;width:100%}}
button{{background:#7dd3fc;color:#082f49;padding:.65rem 1rem;border:0;border-radius:.3rem;font-weight:600}}
</style>
<a href="http://tr.yumy.space/">新闻报告</a><h1>TrendRadar 管理</h1>
<p>调整采集过滤词、分析预算和简报阈值。保存后从下一轮任务生效。</p>
{state}<p role="status">{escape(message)}</p>
<form action="/admin/" method="post"><input name="token" type="hidden" value="{token}">
{numbers}<label>媒体标题过滤词，每行一个<textarea name="ignore_title_terms" rows="6">{terms}</textarea></label>
<button>保存设置</button></form></html>""".encode()


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, base, settings_path, password_path, origin, status_path=None):
        self.base = base
        self.settings_path = settings_path
        self.password_path = password_path
        self.origin = origin
        self.status_path = status_path
        self.token = secrets.token_urlsafe(32)
        self.save_lock = threading.Lock()
        super().__init__(address, AdminRequest)

    def settings(self):
        return {**visible_values(self.base), **operator_settings.read(self.settings_path, self.base)}


class AdminRequest(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def reply(self, code, body=b"", authenticate=False, content_type="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        if authenticate:
            self.send_header("WWW-Authenticate", 'Basic realm="TrendRadar", charset="UTF-8"')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic ") and len(header) <= 1024:
            try:
                user, _, password = base64.b64decode(header[6:], validate=True).decode().partition(":")
                info = json.loads(self.server.password_path.read_text(encoding="ascii"))
                if (user == "admin" and password and hmac.compare_digest(
                        password_hash(password, bytes.fromhex(info["salt"])), info["hash"])):
                    return True
            except (OSError, KeyError, ValueError, binascii.Error):
                pass
        self.reply(401, authenticate=True)
        return False

    def do_GET(self):
        if self.path not in ("/admin/", "/admin/metrics"):
            self.reply(404)
        elif self.authorized():
            snapshot = status.read(self.server.status_path)
            if self.path == "/admin/metrics":
                if snapshot is None:
                    self.reply(503)
                else:
                    self.reply(200, status.metrics(snapshot), content_type="text/plain; version=0.0.4; charset=utf-8")
            else:
                self.reply(200, render(self.server.settings(), self.server.token, snapshot=snapshot))

    def do_POST(self):
        if self.path != "/admin/":
            self.reply(404)
            return
        if not self.authorized():
            return
        if self.headers.get("Origin") != self.server.origin:
            self.reply(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_FORM_BYTES:
                raise ValueError("表单长度不符")
            form = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True, max_num_fields=8)
            if not hmac.compare_digest(form.get("token", [""])[0], self.server.token):
                self.reply(403)
                return
            values = {key: int(form[key][0]) for key, _ in FIELDS}
            values["ignore_title_terms"] = [word for word in form["ignore_title_terms"][0].splitlines() if word.strip()]
            with self.server.save_lock:
                operator_settings.write(self.server.settings_path, values, self.server.base)
        except (ValueError, KeyError):
            self.reply(400, render(self.server.settings(), self.server.token, "设置无效，请检查数值范围和阈值顺序。"))
        else:
            self.reply(200, render(self.server.settings(), self.server.token, "已保存，下轮任务生效。"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "set-password"])
    args = parser.parse_args()
    state = Path(os.environ.get("MARKET_ADMIN_STATE", "/app/admin-state"))
    private = Path(os.environ.get("MARKET_ADMIN_PRIVATE", "/app/admin-private"))
    password_path = private / "password.json"
    if args.command == "set-password":
        set_password(password_path)
        return
    from trendradar.core import load_config

    base = load_config()["MARKET_INTELLIGENCE"]
    status_path = os.environ.get("MARKET_STATUS_PATH")
    server = AdminServer(("0.0.0.0", 8443), base, state / "settings.json", password_path,
                         "https://tr.yumy.space", status_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(private / "fullchain.pem", private / "privkey.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    server.serve_forever()


if __name__ == "__main__":
    main()
