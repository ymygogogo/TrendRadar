"""Validated non-secret notification settings stored with operator overrides."""

import re
from pathlib import Path

from .operator_settings import read_document, write_document


TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
LIST_FIELDS = (("daily_times", "每日简报"), ("trading_times", "交易日简报"))
TIME_FIELDS = (("weekly_time", "周日财经日历"), ("opening_time", "交易日开盘观察"))
NUMBER_FIELDS = (
    ("max_per_hour", "每小时最多投递（按渠道计）", 1, 50),
    ("cooldown_minutes", "同事件更新冷却（分钟）", 0, 1440),
    ("urgent_max_age_hours", "最近更新时限（小时）", 1, 72),
)


class ValidationError(ValueError):
    pass


def visible(settings):
    digest = settings.get("digest", {})
    opening = settings.get("market_open", {})
    policy = settings.get("notification", {})
    return {
        "daily_times": digest.get("times", ["21:30"]),
        "trading_times": digest.get("trading_times", []),
        "weekly_time": digest.get("weekly_time") or "",
        "opening_time": opening.get("alert_time", "09:40") if opening.get("notification_enabled", opening.get("enabled")) else "",
        "urgent_enabled": policy.get("urgent_enabled", True),
        "max_per_hour": policy.get("max_per_hour", 3),
        "cooldown_minutes": policy.get("cooldown_minutes", 60),
        "urgent_max_age_hours": policy.get("urgent_max_age_hours", 6),
    }


def validate(values):
    if not isinstance(values, dict) or set(values) != {
            "urgent_enabled", *(key for key, _ in LIST_FIELDS + TIME_FIELDS),
            *(key for key, *_ in NUMBER_FIELDS)}:
        raise ValidationError("推送设置项不完整")
    for key, label in LIST_FIELDS:
        times = values[key]
        if (not isinstance(times, list) or len(times) > 8
                or any(not isinstance(time, str) or not TIME.fullmatch(time) for time in times)
                or len(set(times)) != len(times)):
            raise ValidationError(f"{label}最多 8 个不同时间，格式为 HH:MM")
    if set(values["daily_times"]) & set(values["trading_times"]):
        raise ValidationError("每日简报与交易日简报的时间不能重叠")
    for key, label in TIME_FIELDS:
        time = values[key]
        if not isinstance(time, str) or (time and not TIME.fullmatch(time)):
            raise ValidationError(f"{label}请填写 HH:MM 或留空")
    if values["opening_time"] and values["opening_time"] < "09:31":
        raise ValidationError("开盘观察时间应在 09:31 及以后")
    if type(values["urgent_enabled"]) is not bool:
        raise ValidationError("即时预警开关无效")
    for key, label, minimum, maximum in NUMBER_FIELDS:
        if type(values[key]) is not int or not minimum <= values[key] <= maximum:
            raise ValidationError(f"{label}须在 {minimum} 至 {maximum} 之间")
    return {**values, **{key: sorted(values[key]) for key, _ in LIST_FIELDS}}


def from_form(form):
    values = {key: re.split(r"[,，\s]+", form[key][0].strip()) if form[key][0].strip() else []
              for key, _ in LIST_FIELDS}
    values.update({key: form[key][0].strip() for key, _ in TIME_FIELDS})
    try:
        values.update({key: int(form[key][0]) for key, *_ in NUMBER_FIELDS})
    except ValueError as error:
        raise ValidationError("即时预警数值请填写整数") from error
    values["urgent_enabled"] = form.get("urgent_enabled") == ["1"]
    return validate(values)


def selected(defaults, settings_path):
    try:
        values = read_document(Path(settings_path).with_name("notifications.json"))
        return validate(values) if values else defaults
    except (ValueError, OSError):
        print("[市场情报] 推送覆盖配置不可用，使用部署配置")
        return defaults


def apply(settings, settings_path):
    values = selected(None, settings_path)
    if values is None:
        return
    digest = settings.setdefault("digest", {})
    digest.update(times=values["daily_times"], trading_times=values["trading_times"], weekly_time=values["weekly_time"] or None)
    opening = settings.setdefault("market_open", {})
    opening["notification_enabled"] = bool(values["opening_time"])
    if values["opening_time"]:
        opening["alert_time"] = values["opening_time"]
    settings.setdefault("notification", {}).update(
        {key: values[key] for key in ("urgent_enabled", *(key for key, *_ in NUMBER_FIELDS))})


def write(settings_path, values):
    write_document(Path(settings_path).with_name("notifications.json"), validate(values))
