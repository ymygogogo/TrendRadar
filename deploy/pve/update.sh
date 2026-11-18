#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/trendradar
exec 9>deploy.lock
flock -n 9 || exit 0
repo=ymygogogo/TrendRadar
metadata=$(curl --retry 2 --max-time 40 -fsS "https://api.github.com/repos/$repo/releases/latest")
tag=$(jq -er '.tag_name' <<<"$metadata")
[[ $tag =~ ^pve-([a-f0-9]{40})$ ]] || exit 0
commit=${BASH_REMATCH[1]}
if [[ -f image.env ]] && grep -Fxq "TREND_IMAGE=trendradar-pve:$commit" image.env; then exit 0; fi
if [[ -f failed-release ]] && grep -Fxq "$tag" failed-release; then exit 0; fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
for asset in compose.yaml SHA256SUMS IMAGE_ID trendradar-image.tar.gz; do
    curl --retry 2 --max-time 900 -fsSL "https://github.com/$repo/releases/download/$tag/$asset" -o "$stage/$asset"
done
[[ $(awk '{print $2}' "$stage/SHA256SUMS") == trendradar-image.tar.gz ]]
(cd "$stage" && sha256sum --strict -c SHA256SUMS)
docker load -i "$stage/trendradar-image.tar.gz"
ref=trendradar-pve:$commit
[[ $(docker image inspect --format '{{.Id}}' "$ref") == "$(cat "$stage/IMAGE_ID")" ]]
printf 'TREND_IMAGE=%s\n' "$ref" > "$stage/image.env"
docker compose --project-name trendradar --project-directory "$PWD" --env-file "$stage/image.env" -f "$stage/compose.yaml" config --quiet
install -d -m 700 output backups
backup=$(mktemp -d "$PWD/backups/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")
if [[ -f image.env ]]; then
    cp -p image.env compose.yaml "$backup/"
    docker compose --env-file image.env stop
fi
cp -p runtime.env "$backup/"
# Stop the only writer before copying SQLite and any WAL files together.
if ! tar -C "$PWD" -czf "$backup/output.tgz" output || ! tar -tzf "$backup/output.tgz" >/dev/null; then
    if [[ -f image.env ]]; then docker compose --env-file image.env up -d --wait --wait-timeout 90; fi
    exit 1
fi
install -m 600 "$stage/compose.yaml" compose.yaml
install -m 600 "$stage/image.env" image.env
if docker compose --env-file image.env up -d --wait --wait-timeout 100; then
    rm -f failed-release
    find backups -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf -- {} +
    echo "TrendRadar deployed $commit and passed the container health check"
else
    docker compose --env-file image.env stop
    mv output "$backup/output-after-failure"
    tar -C "$PWD" -xzf "$backup/output.tgz"
    if [[ -f "$backup/image.env" ]]; then
        cp -p "$backup/image.env" "$backup/compose.yaml" .
        docker compose --env-file image.env up -d --wait --wait-timeout 100
    else
        rm -f image.env compose.yaml
    fi
    echo "$tag" > failed-release
    exit 1
fi
