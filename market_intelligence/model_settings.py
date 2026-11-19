"""Operator overrides for the market-analysis model on its deployed provider."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from .operator_settings import read_document, write_document


MODEL_ID = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*")
FIELDS = (("max_tokens", "最大输出 Token", 256, 65536), ("timeout", "超时（秒）", 5, 600))


def validate(values, deployed_model):
    if not isinstance(values, dict) or set(values) != {"model", *(key for key, *_ in FIELDS)}:
        raise ValueError("模型设置项不完整")
    model = values["model"]
    if (not isinstance(model, str) or len(model) > 128 or not MODEL_ID.fullmatch(model)
            or model.partition("/")[0] != deployed_model.partition("/")[0]):
        raise ValueError("请输入同一服务商的模型标识")
    for key, label, minimum, maximum in FIELDS:
        value = values[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{label}须在 {minimum} 至 {maximum} 之间")
    return values


def resolve(ai, settings_path):
    effective = dict(ai)
    if settings_path:
        try:
            override = read_document(Path(settings_path).with_name("model.json"))
            if override:
                values = validate(override, ai["MODEL"])
                effective.update({key.upper(): value for key, value in values.items()})
        except (ValueError, OSError):
            print("[市场情报] 模型覆盖配置不可用，使用部署配置")
    return effective


def public_settings(ai, active):
    try:
        host = urlsplit(ai.get("API_BASE") or "").hostname or ""
    except ValueError:
        host = ""
    return {
        "deployed_model": ai.get("MODEL", ""),
        "model": active.get("MODEL", ""),
        "endpoint_host": host,
        "credential_configured": bool(ai.get("API_KEY")),
        "max_tokens": active.get("MAX_TOKENS", 5000),
        "timeout": active.get("TIMEOUT", 120),
    }


def write(path, values, deployed_model):
    write_document(Path(path).with_name("model.json"), validate(values, deployed_model))
