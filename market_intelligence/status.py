"""Publish operational counters without exposing the underlying SQLite file."""
import json
import os
from pathlib import Path
import tempfile


def snapshot(db, now):
    status = {"updated_at": now.isoformat(), "updated_timestamp": int(now.timestamp())}
    for key, query in (
        ("articles", "SELECT analysis_state,count(*) FROM articles GROUP BY 1"),
        ("events", "SELECT priority,count(*) FROM events GROUP BY 1"),
        ("deliveries", "SELECT state,count(*) FROM deliveries GROUP BY 1"),
    ):
        status[key] = {row[0]: row[1] for row in db.execute(query)}
    status["analysis_attempts"] = db.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0]
    status["sources"] = [list(row) for row in db.execute(
        "SELECT feed_id,attempts,successes,consecutive_failures,duration_ms FROM feed_health ORDER BY feed_id")]
    return status


def publish(path, db, now):
    path = Path(path)
    fd, pending = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot(db, now), handle, ensure_ascii=False)
            os.fchmod(handle.fileno(), 0o644)
        os.replace(pending, path)
    finally:
        Path(pending).unlink(missing_ok=True)


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
