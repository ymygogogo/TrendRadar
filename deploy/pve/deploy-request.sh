#!/usr/bin/env bash
# OpenSSH forced command. sudo permits only the root-owned updater scripts.
set -euo pipefail

request=${SSH_ORIGINAL_COMMAND:-}
if [[ $request =~ ^image\ ([a-f0-9]{40})$ ]]; then
    exec sudo -n /opt/trendradar/update.sh "${BASH_REMATCH[1]}"
elif [[ $request =~ ^runtime\ ([a-f0-9]{64})$ ]]; then
    exec sudo -n /opt/trendradar/runtime-update.sh "${BASH_REMATCH[1]}"
fi
printf 'Expected a TrendRadar deployment request with a fixed digest\n' >&2
exit 2
