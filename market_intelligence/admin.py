"""HTTPS operator form for a small set of non-secret market settings."""

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from ipaddress import ip_address, ip_network
import os
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

from . import digests, manual_digest, model_settings, notification_settings, operator_settings, status
from .admin_view import render_dashboard, render_digest


FIELDS = (
    ("min_priority_score", "简报最低分"),
    ("urgent_priority_score", "即时预警最低分"),
    ("max_articles_per_run", "每轮分析上限"),
    ("max_articles_per_day", "每日分析上限"),
    ("max_digest_events", "每份简报上限（8～20）"),
)
ITERATIONS = 300_000
MAX_FORM_BYTES = 12_288
ADMIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
REPORT_CSP = (
    "default-src 'none'; base-uri 'none'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline' https://cdnjs.cloudflare.com; img-src data: blob:; "
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads; frame-ancestors 'none'"
)
REPORT_NAMES = ("current.html", "daily.html", "incremental.html")


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
        "max_digest_events": digests.event_limit(settings.get("digest", {}).get("max_events", 8)),
        "ignore_title_terms": settings.get("crawl_filter", {}).get("ignore_title_terms", []),
    }


def render(settings, token, message="", snapshot=None, *, news_url="", model=None, model_message="",
           notification=None, notification_message="", manual_push=None):
    return render_dashboard(settings, FIELDS, token, message, snapshot, news_url=news_url,
                            model=model, model_message=model_message, notification=notification,
                            notification_message=notification_message, manual_push=manual_push)


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, base, settings_path, password_path, origin, status_path=None, report_dir=None, *,
                 news_url="", trusted_networks=(), notification_defaults=None):
        self.base = base
        self.notification_defaults = notification_defaults or notification_settings.visible(base)
        self.settings_path = settings_path
        self.password_path = password_path
        self.origin = origin
        self.news_url = news_url
        self.trusted_networks = tuple(ip_network(cidr.strip()) for cidr in trusted_networks)
        self.status_path = status_path
        self.report_dir = Path(report_dir) if report_dir else None
        self.token = secrets.token_urlsafe(32)
        self.save_lock = threading.Lock()
        super().__init__(address, AdminRequest)

    def settings(self):
        return {**visible_values(self.base), **operator_settings.read(self.settings_path, self.base)}

    def notification(self):
        return notification_settings.selected(self.notification_defaults, self.settings_path)

    def model(self, snapshot):
        active = (snapshot or {}).get("model", {})
        if not active.get("deployed_model"):
            return {}
        configured = model_settings.resolve({"MODEL": active["deployed_model"]}, self.settings_path)
        return {**active, "selected_model": configured.get("MODEL"),
                "selected_max_tokens": configured.get("MAX_TOKENS", active["max_tokens"]),
                "selected_timeout": configured.get("TIMEOUT", active["timeout"])}


class RedirectResponse:
    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()


class AdminRedirect(RedirectResponse, BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        location = self.server.news_url if path in ("/news", "/news/") else self.server.origin + "/admin/"
        self.redirect(location)

    do_HEAD = do_GET


class AdminRequest(RedirectResponse, BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def reply(self, code, body=b"", authenticate=False, content_type="text/html; charset=utf-8", csp=ADMIN_CSP):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", csp)
        if authenticate:
            self.send_header("WWW-Authenticate", 'Basic realm="TrendRadar", charset="UTF-8"')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if self.server.trusted_networks:
            # Use the socket peer. A client can supply arbitrary forwarding headers.
            peer = ip_address(self.client_address[0])
            host = self.headers.get("Host", "").lower()
            if (host == urlsplit(self.server.origin).netloc
                    and any(peer in network for network in self.server.trusted_networks)):
                return True
            self.reply(403)
            return False
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

    def show_news(self):
        folder = self.server.report_dir
        if folder is None:
            self.reply(404)
            return
        reports = [folder / name for name in REPORT_NAMES if (folder / name).is_file()]
        try:
            newest = max(reports, key=lambda path: path.stat().st_mtime_ns)
            content = newest.read_bytes()
        except (OSError, ValueError):
            self.reply(503, "等待采集生成新闻报告。".encode(), content_type="text/plain; charset=utf-8")
        else:
            self.reply(200, content, csp=REPORT_CSP)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/admin" or (path == "/" and self.server.news_url):
            self.redirect("/admin/")
            return
        if path in ("/news", "/news/") and self.server.news_url:
            self.redirect(self.server.news_url)
            return
        if self.path in ("/", "/news/"):
            self.show_news()
            return
        digest_prefix = "/admin/digests/"
        if self.path.startswith(digest_prefix):
            if self.authorized():
                edition = digests.read_edition(self.server.status_path, self.path.removeprefix(digest_prefix))
                if edition is None:
                    self.reply(404)
                else:
                    self.reply(200, render_digest(edition))
        elif path not in ("/admin/", "/admin/metrics"):
            self.reply(404)
        elif self.authorized():
            snapshot = status.read(self.server.status_path)
            if path == "/admin/metrics":
                if snapshot is None:
                    self.reply(503)
                else:
                    self.reply(200, status.metrics(snapshot), content_type="text/plain; version=0.0.4; charset=utf-8")
            else:
                self.dashboard(snapshot)

    def dashboard(self, snapshot, code=200, *, message="", model_message="", notification_message=""):
        manual_push = None
        if self.server.status_path:
            manual_push = {
                **manual_digest.current(self.server.settings_path, self.server.status_path),
                "ticket": secrets.token_hex(16),
                "channels_ready": [name for name, info in (snapshot or {}).get("notification_channels", {}).items()
                                   if info.get("configured") and info.get("enabled")],
            }
        self.reply(code, render(self.server.settings(), self.server.token, message, snapshot,
                                news_url=self.server.news_url, model=self.server.model(snapshot),
                                notification=self.server.notification(), model_message=model_message,
                                notification_message=notification_message, manual_push=manual_push))

    def form(self, max_fields):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_FORM_BYTES:
            raise ValueError("表单长度不符")
        values = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True, max_num_fields=max_fields)
        if not hmac.compare_digest(values.get("token", [""])[0].encode(), self.server.token.encode()):
            self.reply(403)
            return None
        return values

    def do_POST(self):
        if self.path not in ("/admin/", "/admin/model", "/admin/notifications", "/admin/notifications/send-now"):
            self.reply(404)
            return
        if not self.authorized():
            return
        if self.headers.get("Origin") != self.server.origin:
            self.reply(403)
            return
        snapshot = status.read(self.server.status_path)
        if self.path == "/admin/model":
            self.save_model(snapshot)
            return
        if self.path == "/admin/notifications/send-now":
            self.queue_manual_digest(snapshot)
            return
        if self.path == "/admin/notifications":
            self.save_notifications(snapshot)
            return
        try:
            form = self.form(8)
            if form is None:
                return
            values = {key: int(form[key][0]) for key, _ in FIELDS}
            values["ignore_title_terms"] = [word for word in form["ignore_title_terms"][0].splitlines() if word.strip()]
            with self.server.save_lock:
                operator_settings.write(self.server.settings_path, values, self.server.base)
        except (ValueError, KeyError):
            self.dashboard(snapshot, 400, message="设置无效，请检查数值范围和阈值顺序。")
        else:
            self.dashboard(snapshot, message="已保存，下轮任务生效。")

    def queue_manual_digest(self, snapshot):
        try:
            form = self.form(2)
            if form is None:
                return
            if not self.server.status_path or not any(
                info.get("configured") and info.get("enabled")
                for info in (snapshot or {}).get("notification_channels", {}).values()
            ):
                self.dashboard(snapshot, 409, notification_message="还没有可用的推送渠道。")
                return
            with self.server.save_lock:
                manual_digest.enqueue(self.server.settings_path, self.server.status_path, form["request_id"][0])
        except (ValueError, KeyError):
            self.dashboard(snapshot, 400, notification_message="推送请求无效，请刷新页面。")
        except OSError:
            self.dashboard(snapshot, 500, notification_message="暂时无法提交推送请求。")
        else:
            self.redirect("/admin/#push-settings")

    def save_model(self, snapshot):
        result, message = 200, "模型设置已保存，下轮采集生效。"
        try:
            form = self.form(4)
            if form is None:
                return
            model = self.server.model(snapshot)
            if not model:
                raise ValueError("等待采集容器同步模型配置")
            values = {key: int(form[key][0]) for key, *_ in model_settings.FIELDS}
            values["model"] = form["model"][0].strip()
            with self.server.save_lock:
                model_settings.write(self.server.settings_path, values, model["deployed_model"])
        except (ValueError, KeyError, OSError):
            result, message = 400, "无法保存。请检查模型服务商、Token 上限和超时范围。"
        self.dashboard(snapshot, result, model_message=message)

    def save_notifications(self, snapshot):
        try:
            form = self.form(9)
            if form is None:
                return
            values = notification_settings.from_form(form)
            with self.server.save_lock:
                notification_settings.write(self.server.settings_path, values)
        except notification_settings.ValidationError as error:
            self.dashboard(snapshot, 400, notification_message=str(error))
        except (ValueError, KeyError):
            self.dashboard(snapshot, 400, notification_message="请检查推送时间和数值范围。")
        except OSError:
            self.dashboard(snapshot, 500, notification_message="写入失败，请检查管理配置目录。")
        else:
            self.dashboard(snapshot, notification_message="设置已保存，下一轮调度读取。")


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

    config = load_config()
    base = config["MARKET_INTELLIGENCE"]
    status_path = os.environ.get("MARKET_STATUS_PATH")
    trusted_networks = os.environ.get("MARKET_ADMIN_TRUSTED_NETWORKS")
    server = AdminServer(("0.0.0.0", 8443), base, state / "settings.json", password_path,
                         "https://tr.yumy.space", status_path, os.environ.get("MARKET_REPORT_DIR"),
                         news_url=os.environ.get("MARKET_ADMIN_NEWS_URL", ""),
                         trusted_networks=trusted_networks.split(",") if trusted_networks else (),
                         notification_defaults=config.get("MARKET_NOTIFICATION_DEFAULTS"))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(private / "fullchain.pem", private / "privkey.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    redirect_port = os.environ.get("MARKET_ADMIN_HTTP_REDIRECT_PORT")
    if redirect_port:
        redirect = ThreadingHTTPServer(("0.0.0.0", int(redirect_port)), AdminRedirect)
        redirect.origin = server.origin
        redirect.news_url = server.news_url or server.origin + "/news/"
        threading.Thread(target=redirect.serve_forever, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
