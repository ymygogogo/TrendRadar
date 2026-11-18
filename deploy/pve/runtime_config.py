"""Encrypt CI secrets or prepare a scoped Compose environment update."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request


def settings(model_key, chart_bot):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{16,512}", model_key or ""):
        raise ValueError("MODEL_KEY is missing or malformed")
    if re.fullmatch(r"[A-Fa-f0-9-]{32,64}", chart_bot or ""):
        chart_bot = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={chart_bot}"
    if not re.fullmatch(
        r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]{16,128}",
        chart_bot or "",
    ):
        raise ValueError("CHART_BOT must be a WeCom webhook or webhook key")
    return {
        "AI_API_KEY": model_key,
        "MARKET_WEWORK_WEBHOOK_URL": chart_bot,
        "MARKET_ANALYSIS_ENABLED": "true",
        "MARKET_NOTIFICATION_ENABLED": "true",
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_model(old_values, key):
    base = old_values.get("AI_API_BASE", "").rstrip("/")
    if base != "https://token.alayanew.com/v1":
        raise ValueError("expected configured AlayaNew endpoint")
    model = old_values.get("AI_MODEL", "").removeprefix("openai/")
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=20) as response:
            models = json.loads(response.read(2_000_000))
        if model not in {entry["id"] for entry in models["data"]}:
            raise ValueError("configured model was not advertised by AlayaNew")
    except urllib.error.HTTPError as error:
        raise ValueError(f"AlayaNew model validation returned HTTP {error.code}") from None
    except (OSError, KeyError, TypeError):
        raise ValueError("AlayaNew model validation failed") from None


def stage(directory, candidate):
    identity = directory / "runtime-identity"
    decrypted = subprocess.run(
        ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER",
         "-recip", str(identity / "recipient.pem"), "-inkey", str(identity / "private.pem")],
        input=sys.stdin.buffer.read(16384), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=True,
    ).stdout
    config = json.loads(decrypted)
    values = settings(config["AI_API_KEY"], config["MARKET_WEWORK_WEBHOOK_URL"])
    lines = (directory / "runtime.env").read_text().splitlines()
    old = dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    verify_model(old, values["AI_API_KEY"])
    lines = [line for line in lines if line.split("=", 1)[0] not in values]
    lines += [f"{key}={value}" for key, value in values.items()]
    candidate.write_text("\n".join(lines) + "\n")
    candidate.chmod(0o600)


def main():
    match sys.argv[1:]:
        case ["seal", output]:
            values = settings(os.environ.get("MODEL_KEY"), os.environ.get("CHART_BOT"))
            cert = Path(__file__).with_name("runtime-recipient.pem")
            subprocess.run(
                ["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm", "-outform", "DER",
                 "-out", output, "-recip", str(cert), "-keyopt", "rsa_padding_mode:oaep"],
                input=json.dumps(values).encode(), check=True,
            )
        case ["stage", directory, candidate]:
            stage(Path(directory), Path(candidate))
        case _:
            raise ValueError("expected seal or stage")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        # Validation errors above contain fixed strings and HTTP status only.
        print(str(error) if type(error) is ValueError else "runtime configuration operation failed", file=sys.stderr)
        raise SystemExit(1)
