"""Validated, atomic overrides for the operator settings form.

Store these options on a private state volume, outside the static report root.
"""
import json
import os
from pathlib import Path
import tempfile

from .digests import MAX_EVENTS, MIN_EVENTS


SCORE_FIELDS = {"min_priority_score", "urgent_priority_score"}
BUDGET_FIELDS = {"max_articles_per_run", "max_articles_per_day"}
OTHER_FIELDS = {"max_digest_events", "ignore_title_terms"}
ALLOWED = SCORE_FIELDS | BUDGET_FIELDS | OTHER_FIELDS


def validate(values, base):
    if not isinstance(values, dict) or set(values) - ALLOWED:
        raise ValueError("发现不支持的设置项")
    for key in SCORE_FIELDS | BUDGET_FIELDS | {"max_digest_events"}:
        if key not in values:
            continue
        value = values[key]
        limit = 100 if key in SCORE_FIELDS else MAX_EVENTS if key == "max_digest_events" else 10000
        minimum = MIN_EVENTS if key == "max_digest_events" else 0
        if type(value) is not int or not minimum <= value <= limit:
            raise ValueError(f"{key} 必须是 {minimum} 至 {limit} 范围内的整数")
    defaults = base.get("event_analysis", {})
    low = values.get("min_priority_score", defaults.get("min_priority_score", 70))
    high = values.get("urgent_priority_score", defaults.get("urgent_priority_score", 85))
    if not low < high:
        raise ValueError("预警分数必须高于简报分数")
    if "ignore_title_terms" in values:
        words = values["ignore_title_terms"]
        if (not isinstance(words, list) or len(words) > 100
                or any(not isinstance(word, str) or not 1 <= len(word.strip()) <= 80 for word in words)):
            raise ValueError("过滤词最多 100 条，每条 1 至 80 字")
        values = {**values, "ignore_title_terms": list(dict.fromkeys(word.strip() for word in words))}
    return values


def read_document(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def read(path, base):
    config = read_document(path)
    if isinstance(config, dict) and type(config.get("max_digest_events")) is int and 1 <= config["max_digest_events"] < MIN_EVENTS:
        # Upgrade the former 1–7 range without discarding other operator overrides.
        config["max_digest_events"] = MIN_EVENTS
    return validate(config, base)


def write_document(path, candidate):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, pending = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        Path(pending).unlink(missing_ok=True)


def write(path, values, base):
    candidate = validate(values, base)
    write_document(path, candidate)
    return candidate


def apply(base, values):
    base.update({key: values[key] for key in BUDGET_FIELDS & values.keys()})
    for key in SCORE_FIELDS & values.keys():
        base.setdefault("event_analysis", {})[key] = values[key]
    if "max_digest_events" in values:
        base.setdefault("digest", {})["max_events"] = values["max_digest_events"]
    if "ignore_title_terms" in values:
        base.setdefault("crawl_filter", {})["ignore_title_terms"] = values["ignore_title_terms"]
