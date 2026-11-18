#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/trendradar
[[ ! -r deploy-egress.env ]] || source ./deploy-egress.env
# Keep retries from holding the lock shared with the image updater.
exec 8>runtime-download.lock
requested=${1:-}
if [[ -n $requested ]]; then
    [[ $requested =~ ^[a-f0-9]{64}$ ]] || exit 2
    flock -w 120 8
else
    flock -n 8 || exit 0
fi
stage=$(mktemp -d "$PWD/.runtime-update.XXXXXX")
trap 'rm -rf "$stage"' EXIT

url=https://github.com/ymygogogo/TrendRadar/releases/download/pve-runtime/runtime.env.enc
status=$(curl -sSL --connect-timeout 8 --retry 1 --retry-max-time 25 --max-time 20 --max-filesize 16384     -o "$stage/envelope" -w '%{http_code}' "$url")
case "$status" in
    200) ;;
    404) [[ -z $requested ]]; exit 0 ;;
    *) printf 'Runtime release unavailable (HTTP %s)\n' "$status" >&2; exit 1 ;;
esac
digest=$(sha256sum "$stage/envelope" | cut -d' ' -f1)
if [[ -n $requested && $digest != "$requested" ]]; then
    echo "Published runtime configuration does not match this CI run" >&2
    exit 1
fi
exec 9>deploy.lock
if [[ -n $requested ]]; then
    flock -w 120 9
else
    flock -n 9 || exit 0
fi
compose=(docker compose --env-file image.env)
if [[ -f runtime.applied ]] && grep -Fxq "$digest" runtime.applied; then
    if [[ -n $requested ]]; then
        container=$("${compose[@]}" ps -q trendradar)
        [[ -n $container && $(docker inspect --format '{{.State.Health.Status}}' "$container") == healthy ]]
        printf 'Runtime configuration already applied: %s (healthy)\n' "$digest"
    fi
    exit 0
fi

python3 runtime_config.py stage "$PWD" "$stage/runtime.env" < "$stage/envelope"
install -d -m 700 runtime-backups
backup=$(mktemp "$PWD/runtime-backups/runtime.XXXXXX.env")
cp -p runtime.env "$backup"
cmp "$backup" runtime.env
mv "$stage/runtime.env" runtime.env
if "${compose[@]}" config --quiet > "$stage/compose.log" 2>&1 &&
   "${compose[@]}" up -d --force-recreate --wait --wait-timeout 100 >> "$stage/compose.log" 2>&1 &&
   "${compose[@]}" exec -T trendradar python -c 'import os; assert os.environ.get("AI_API_KEY") and os.environ.get("MARKET_WEWORK_WEBHOOK_URL"); assert all(os.environ.get(k) == "true" for k in ("MARKET_ANALYSIS_ENABLED", "MARKET_NOTIFICATION_ENABLED"))' >> "$stage/compose.log" 2>&1; then
    printf '%s\n' "$digest" > "$stage/runtime.applied"
    mv "$stage/runtime.applied" runtime.applied
    find runtime-backups -maxdepth 1 -type f -name 'runtime.*.env' -mtime +30 -delete
    printf 'Runtime configuration applied: %s (healthy)\n' "$digest"
else
    install -m 600 "$backup" "$stage/prior.env"
    mv "$stage/prior.env" runtime.env
    "${compose[@]}" up -d --force-recreate --wait --wait-timeout 100 >> "$stage/compose.log" 2>&1
    echo "Runtime update failed and previous configuration restored" >&2
    exit 1
fi
