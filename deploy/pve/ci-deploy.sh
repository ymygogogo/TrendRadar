#!/usr/bin/env bash
set -euo pipefail
umask 077

kind=${1:-}
digest=${2:-}
if ! { [[ $kind == image && $digest =~ ^[a-f0-9]{40}$ ]] ||
       [[ $kind == runtime && $digest =~ ^[a-f0-9]{64}$ ]]; }; then
    exit 2
fi
: "${PVE_DEPLOY_KEY:?PVE deploy SSH key is required}"
: "${PVE_DEPLOY_KNOWN_HOSTS:?Pinned PVE SSH host key is required}"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
printf '%s\n' "$PVE_DEPLOY_KEY" > "$stage/key"
printf '%s\n' "$PVE_DEPLOY_KNOWN_HOSTS" > "$stage/known_hosts"
unset PVE_DEPLOY_KEY PVE_DEPLOY_KNOWN_HOSTS
ssh -F /dev/null -T -i "$stage/key" -o BatchMode=yes -o IdentitiesOnly=yes \
    -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stage/known_hosts" \
    trendradar-deploy@192.168.0.103 "$kind $digest"
