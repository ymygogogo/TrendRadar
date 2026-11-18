#!/usr/bin/env bash
set -euo pipefail
umask 077

name=${TRENDRADAR_RUNNER_NAME:-trendradar-runtime-pve}
state=${TRENDRADAR_RUNNER_STATE:-/var/lib/trendradar-actions-runner}
repo=${TRENDRADAR_RUNNER_REPO:-ymygogogo/TrendRadar}
image=ghcr.io/actions/actions-runner:2.338.2
credential_files=(.runner .credentials .credentials_rsaparams)
install -d -m 700 "$state"
exec 9>"$state/lock"

cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
}

start_container() {
    if docker container inspect "$name" >/dev/null 2>&1; then
        test "$(docker inspect -f '{{index .Config.Labels "io.trendradar.runner"}}' "$name")" = managed
        cleanup
    fi
    docker run --pull=never --rm -d --name "$name" --label io.trendradar.runner=managed \
        --user runner --cap-drop=ALL --security-opt no-new-privileges \
        --pids-limit 160 --memory 2g --cpus 1.5 --sysctl net.ipv6.conf.all.disable_ipv6=1 \
        --log-driver local -e HTTPS_PROXY=http://192.168.0.2:7890 \
        -e HTTP_PROXY=http://192.168.0.2:7890 \
        -e NO_PROXY=localhost,127.0.0.1,github.com,api.github.com \
        --entrypoint /bin/sleep "$image" infinity >/dev/null
    trap cleanup EXIT

    pid=$(docker inspect -f '{{.State.Pid}}' "$name")
    test "$(readlink /proc/1/ns/net)" != "$(readlink "/proc/$pid/ns/net")"
    rules=(nsenter -t "$pid" -n iptables)
    "${rules[@]}" -N TREND_OUT
    "${rules[@]}" -A TREND_OUT -d 192.168.0.2/32 -p tcp --dport 7890 -j RETURN
    "${rules[@]}" -A TREND_OUT -d 192.168.0.103/32 -p tcp --dport 22 -j RETURN
    for range in 10.0.0.0/8 100.64.0.0/10 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16; do
        "${rules[@]}" -A TREND_OUT -d "$range" -j REJECT
    done
    "${rules[@]}" -I OUTPUT 1 -j TREND_OUT

    docker exec "$name" sh -c 'test "$(id -u)" != 0; test ! -S /var/run/docker.sock; mkdir -p /tmp/trendradar-tools'
    if ! docker exec "$name" sh -c 'command -v gh' >/dev/null; then
        docker cp /usr/bin/gh "$name:/tmp/trendradar-tools/gh"
        docker exec "$name" /tmp/trendradar-tools/gh --version >/dev/null
    fi
}

case "${1:-}" in
    stop)
        # Killing the container leaves a stale broker session. Signal the listener
        # and give it time to delete the session before the run process cleans up.
        if ! docker container inspect "$name" >/dev/null 2>&1; then exit 0; fi
        test "$(docker inspect -f '{{index .Config.Labels "io.trendradar.runner"}}' "$name")" = managed
        docker exec "$name" python3 -c '
import os
from pathlib import Path
import signal
for entry in Path("/proc").glob("[0-9]*/comm"):
    try:
        if entry.read_text().strip() == "Runner.Listener":
            os.kill(int(entry.parent.name), signal.SIGINT)
    except (FileNotFoundError, ProcessLookupError):
        pass
' || true
        for ((elapsed=0; elapsed<28; elapsed++)); do
            if ! docker container inspect "$name" >/dev/null 2>&1; then exit 0; fi
            sleep 1
        done
        echo "TrendRadar runner did not stop gracefully before the deadline" >&2
        cleanup
        ;;
    register)
        flock -n 9
        test ! -e "$state/.runner"
        start_container
        docker exec -i -w /home/runner "$name" /bin/bash -c '
            IFS= read -r registration_token
            test -n "$registration_token"
            exec ./config.sh --unattended --url "https://github.com/$1" \
                --token "$registration_token" --name "$2" \
                --labels "$2" --no-default-labels --work _work
        ' _ "$repo" "$name"
        docker exec -w /home/runner "$name" tar -cf - "${credential_files[@]}" |
            tar -xf - -C "$state" --no-same-owner --no-same-permissions
        chmod 600 "${credential_files[@]/#/$state/}"
        ;;
    run)
        flock -n 9
        for file in "${credential_files[@]}"; do test -s "$state/$file"; done
        start_container
        tar -cf - -C "$state" "${credential_files[@]}" |
            docker exec -i -w /home/runner "$name" tar -xf - --no-same-owner
        docker exec -e PATH=/tmp/trendradar-tools:/usr/local/bin:/usr/bin:/bin \
            -w /home/runner "$name" ./run.sh --once
        ;;
    *) printf 'Specify register, run, or stop\n' >&2; exit 2 ;;
esac
