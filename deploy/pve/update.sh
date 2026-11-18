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
if [[ -f image.env ]]; then
    current_ref=$(grep -E '^TREND_IMAGE=trendradar-pve:[a-f0-9]{40}$' image.env || true)
    if [[ $current_ref =~ ([a-f0-9]{40})$ ]]; then
        current_commit=${BASH_REMATCH[1]}
        if [[ "$current_commit" == "$commit" ]]; then exit 0; fi
        relation=$(curl --connect-timeout 8 --max-time 20 -fsS \
            "https://api.github.com/repos/$repo/compare/$current_commit...$commit" | jq -er '.status')
        if [[ "$relation" != ahead ]]; then
            echo "Release $commit skipped: GitHub comparison is $relation relative to $current_commit"
            exit 0
        fi
    fi
fi
if [[ -f failed-release ]] && grep -Fxq "$tag" failed-release; then exit 0; fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
for asset in compose.yaml SHA256SUMS IMAGE_ID; do
    curl --retry 2 --max-time 900 -fsSL "https://github.com/$repo/releases/download/$tag/$asset" -o "$stage/$asset"
done
[[ $(awk '{print $2}' "$stage/SHA256SUMS") == trendradar-image.tar.gz ]]
archive=$stage/trendradar-image.tar.gz
cached=$PWD/release-cache/$tag.tar.gz
if [[ -f "$cached" ]]; then
    ln -s "$cached" "$archive"
    if ! (cd "$stage" && sha256sum --strict -c SHA256SUMS); then
        rm -f "$archive"
    fi
fi
if [[ ! -e "$archive" ]]; then
    curl --retry 2 --max-time 900 -fsSL "https://github.com/$repo/releases/download/$tag/trendradar-image.tar.gz" -o "$archive"
fi
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
    rm -f failed-release "$cached"
    find backups -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf -- {} +
    mapfile -t retained < <(find backups -mindepth 1 -maxdepth 1 -type d -name '20*' | sort -r)
    for old in "${retained[@]:5}"; do rm -rf -- "$old"; done
    previous=""
    if [[ -f "$backup/image.env" ]]; then previous=$(cut -d= -f2- "$backup/image.env"); fi
    while read -r old; do
        if [[ $old =~ ^trendradar-pve:[a-f0-9]{40}$ && $old != "$ref" && $old != "$previous" ]]; then
            docker image rm "$old" >/dev/null || true
        fi
    done < <(docker image ls trendradar-pve --format '{{.Repository}}:{{.Tag}}')
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
