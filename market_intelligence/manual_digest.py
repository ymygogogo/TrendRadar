"""Relay a manual briefing request between the admin and the privileged collector."""

from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path
import re

from . import digests, operator_settings


REQUEST_ID = re.compile(r"[a-f0-9]{32}")
MAX_AGE = timedelta(minutes=10)
MAX_ATTEMPTS = 5
TERMINAL = {"delivered", "partial", "failed", "no_channels", "empty", "expired"}


def _request_path(settings_path):
    return Path(settings_path).with_name("manual-digest.json")


def _result_path(status_path):
    return Path(status_path).with_name("manual-digest-result.json")


def current(settings_path, status_path, now=None):
    if not settings_path or not status_path:
        return {}
    now = now or datetime.now(timezone.utc)
    try:
        request = operator_settings.read_document(_request_path(settings_path))
        if not REQUEST_ID.fullmatch(request.get("id", "")):
            return {}
        stamp = datetime.fromisoformat(request["requested_at"])
        if stamp.tzinfo is None or stamp > now:
            return {}
        result = operator_settings.read_document(_result_path(status_path))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}
    if not isinstance(result, dict) or result.get("id") != request["id"]:
        result = {}
    state = result.get("state", "queued")
    if state not in TERMINAL and now - stamp >= MAX_AGE:
        state = "expired"
    return {**request, **result, "state": state}


def enqueue(settings_path, status_path, request_id, now=None):
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("推送请求无效，请刷新页面。")
    now = now or datetime.now(timezone.utc)
    previous = current(settings_path, status_path, now)
    if previous and (previous["id"] == request_id or previous["state"] not in TERMINAL):
        return False
    operator_settings.write_document(_request_path(settings_path), {
        "id": request_id, "requested_at": now.isoformat(),
    })
    return True


def pending(settings_path, status_path, now=None):
    request = current(settings_path, status_path, now)
    return bool(request and request["state"] not in TERMINAL)


def edition_for(engine, key):
    edition = digests.stored_edition(engine.db, key)
    if edition is not None:
        return edition
    now = engine.now()
    return digests.make_edition(engine, key, now, now - timedelta(hours=24),
                                "即时热点新闻简报", closing=True)


def process(engine, settings_path, status_path):
    if not settings_path or not status_path:
        return
    lock_path = _result_path(status_path).with_suffix(".lock")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        request = current(settings_path, status_path, engine.now())
        if not request or request["state"] in TERMINAL:
            return
        key = "digest-manual:" + request["id"]
        channels = [name for name, info in engine.notification_status().items()
                    if info["enabled"] and info["configured"]]
        attempts = request.get("attempts", 0) + 1
        delivered = 0
        if not channels:
            state = "no_channels"
        else:
            edition = edition_for(engine, key)
            if not edition["events"]:
                state = "empty"
            else:
                engine.send_digest_edition(key, edition, channels)
                sent = {row[0] for row in engine.db.execute(
                    "SELECT channel FROM deliveries WHERE event_id=? AND state='delivered'", (key,))}
                delivered = len(sent.intersection(channels))
                if delivered == len(channels):
                    state = "delivered"
                elif attempts >= MAX_ATTEMPTS:
                    state = "partial" if delivered else "failed"
                else:
                    state = "retrying"
        operator_settings.write_document(_result_path(status_path), {
            "id": request["id"], "state": state, "attempts": attempts,
            "delivered": delivered, "channels": len(channels), "updated_at": engine.now().isoformat(),
        }, mode=0o644)
