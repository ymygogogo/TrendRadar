#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/trendradar
exec 9>deploy.lock
flock -n 9 || exit 0
stage=$(mktemp -d "$PWD/.runtime-update.XXXXXX")
trap 'rm -rf "$stage"' EXIT

url=https://github.com/ymygogogo/TrendRadar/releases/download/pve-runtime/runtime.env.enc
status=$(curl -sSL --retry 2 --max-time 30 --max-filesize 16384     -o "$stage/envelope" -w '%{http_code}' "$url")
case "$status" in
    200) ;;
    404) exit 0 ;;
    *) printf 'Runtime release unavailable (HTTP %s)\n' "$status" >&2; exit 1 ;;
esac
digest=$(sha256sum "$stage/envelope" | cut -d' ' -f1)
if [[ -f runtime.applied ]] && grep -Fxq "$digest" runtime.applied; then exit 0; fi

python3 runtime_config.py stage "$PWD" "$stage/runtime.env" < "$stage/envelope"
install -d -m 700 runtime-backups
backup=$(mktemp "$PWD/runtime-backups/runtime.XXXXXX.env")
cp -p runtime.env "$backup"
cmp "$backup" runtime.env
mv "$stage/runtime.env" runtime.env
compose=(docker compose --env-file image.env)
if "${compose[@]}" config --quiet > "$stage/compose.log" 2>&1 &&
   "${compose[@]}" up -d --force-recreate --wait --wait-timeout 100 >> "$stage/compose.log" 2>&1 &&
   "${compose[@]}" exec -T trendradar python -c 'import os; assert os.environ.get("AI_API_KEY") and os.environ.get("MARKET_WEWORK_WEBHOOK_URL"); assert all(os.environ.get(k) == "true" for k in ("MARKET_ANALYSIS_ENABLED", "MARKET_NOTIFICATION_ENABLED"))' >> "$stage/compose.log" 2>&1; then
    printf '%s\n' "$digest" > "$stage/runtime.applied"
    mv "$stage/runtime.applied" runtime.applied
    find runtime-backups -maxdepth 1 -type f -name 'runtime.*.env' -mtime +30 -delete
    echo "Runtime configuration applied and container healthy"
else
    install -m 600 "$backup" "$stage/prior.env"
    mv "$stage/prior.env" runtime.env
    "${compose[@]}" up -d --force-recreate --wait --wait-timeout 100 >> "$stage/compose.log" 2>&1
    echo "Runtime update failed and previous configuration restored" >&2
    exit 1
fi
