"""Publish a dashboard snapshot and expose its operational metrics."""
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import tempfile


def snapshot(db, now, channels=None, model=None):
    status = {"updated_at": now.isoformat(), "updated_timestamp": int(now.timestamp())}
    for key, query in (
        ("articles", "SELECT analysis_state,count(*) FROM articles GROUP BY 1"),
        ("events", "SELECT priority,count(*) FROM events GROUP BY 1"),
        ("deliveries", "SELECT state,count(*) FROM deliveries GROUP BY 1"),
    ):
        status[key] = {row[0]: row[1] for row in db.execute(query)}
    status["analysis_attempts"] = db.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0]
    status["model"] = model or {}
    days = [(now.date() - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    attempts = dict(db.execute("""SELECT substr(attempted_at,1,10),count(*) FROM analysis_attempts
        WHERE attempted_at>=? GROUP BY 1""", (days[0],)))
    usage = {row[0]: list(row[1:]) for row in db.execute(
        """SELECT substr(recorded_at,1,10),sum(input_tokens),sum(output_tokens),count(*),count(input_tokens),
                  count(output_tokens) FROM model_usage WHERE recorded_at>=? GROUP BY 1""", (days[0],))}
    status["model_usage"] = [
        {"date": day, "attempts": attempts.get(day, 0), "input_tokens": u[0], "output_tokens": u[1],
         "responses": u[2], "input_samples": u[3], "output_samples": u[4]}
        for day in days for u in [usage.get(day, [None, None, 0, 0, 0])]
    ]
    status["sources"] = [list(row) for row in db.execute(
        "SELECT feed_id,attempts,successes,consecutive_failures,duration_ms FROM feed_health ORDER BY feed_id")]
    status["source_details"] = [list(row) for row in db.execute(
        """SELECT feed_id,name,category,last_attempt,last_success,latest_published_at
        FROM feed_health ORDER BY feed_id""")]
    status["recent_events"] = [list(row) for row in db.execute(
        """SELECT title_zh,substr(summary_zh,1,240),priority,score,verification_status,latest_reported_at
        FROM events WHERE priority IN ('P0','P1')
            AND julianday(latest_reported_at) BETWEEN julianday(?) AND julianday(?)
        ORDER BY priority, score DESC, julianday(latest_reported_at) DESC, id LIMIT 8""",
        ((now - timedelta(hours=24)).isoformat(), now.isoformat()))]
    deliveries = {}
    for channel, state, count in db.execute("SELECT channel,state,count(*) FROM deliveries GROUP BY 1,2"):
        deliveries.setdefault(channel, {})[state] = count
    last_deliveries = {
        channel: [state, stamp] for channel, state, stamp in db.execute(
            """SELECT channel,state,updated_at FROM (
                SELECT channel,state,updated_at,row_number() OVER (
                    PARTITION BY channel ORDER BY updated_at DESC, rowid DESC) AS position
                FROM deliveries
            ) WHERE position = 1""")
    }
    status["notification_channels"] = {
        channel: {"enabled": info["enabled"], "configured": info["configured"],
                  "counts": deliveries.get(channel, {}), "latest": last_deliveries.get(channel)}
        for channel, info in (channels or {}).items()
    }
    return status


def publish(path, db, now, channels=None, model=None):
    path = Path(path)
    fd, pending = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot(db, now, channels, model), handle, ensure_ascii=False)
            os.fchmod(handle.fileno(), 0o644)
        os.replace(pending, path)
    finally:
        Path(pending).unlink(missing_ok=True)


def publish_current(engine):
    path = os.environ.get("MARKET_STATUS_PATH")
    if not path:
        return
    from .model_settings import public_settings

    config = engine.upstream_config
    ai = config.get("AI", {})
    model = public_settings(ai, config.get("MARKET_AI", ai))
    model["analysis_enabled"] = bool(engine.settings.get("event_analysis", {}).get("enabled"))
    try:
        publish(path, engine.db, engine.now(), engine.notification_status(), model)
    except OSError as error:
        logging.getLogger(__name__).warning("状态快照写入失败: %s", type(error).__name__)


def read(path):
    if path is not None:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return None


def metrics(status):
    lines = []

    def one(name, value, labels=""):
        if isinstance(value, int):
            lines.append(f"trendradar_{name}{labels} {value}")

    one("status_snapshot_timestamp_seconds", status["updated_timestamp"])
    one("analysis_attempts_retained", status["analysis_attempts"])
    for key, name, label in (("articles", "articles", "state"),
                              ("events", "events", "priority"),
                              ("deliveries", "deliveries", "state")):
        for state, count in status[key].items():
            one(name, count, "{" + label + "=" + json.dumps(state) + "}")
    for feed, attempts, successes, failures, duration_ms in status["sources"]:
        label = "{feed=" + json.dumps(feed, ensure_ascii=False) + "}"
        one("feed_attempts_total", attempts, label)
        one("feed_successes_total", successes, label)
        one("feed_consecutive_failures", failures, label)
        one("feed_last_fetch_duration_milliseconds", duration_ms, label)
    return ("\n".join(lines) + "\n").encode()
