"""Exercise the CI deployment scripts without network or a Docker daemon."""
import os
from pathlib import Path
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "deploy/pve"
SHA = "a" * 40
NEXT_SHA = "b" * 40


@pytest.fixture
def updater(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def fake(name, body):
        command = bin_dir / name
        command.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n")
        command.chmod(0o700)

    def run(script, *args, **overrides):
        source = (SCRIPTS / script).read_text().replace("cd /opt/trendradar\n", "")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **overrides}
        return subprocess.run(["bash", "-c", source, script, *args],
                              cwd=tmp_path, env=env, text=True, capture_output=True, timeout=10)

    return run, fake


def test_forced_command_rejects_shell_and_accepts_only_exact_digest(updater):
    run, fake = updater
    fake("sudo", 'printf "%s\\n" "$@"')
    assert run("deploy-request.sh", SSH_ORIGINAL_COMMAND=f"image {SHA}; id").returncode == 2
    accepted = run("deploy-request.sh", SSH_ORIGINAL_COMMAND=f"image {SHA}")
    assert accepted.returncode == 0
    assert accepted.stdout.splitlines() == ["-n", "/opt/trendradar/update.sh", SHA]


def test_existing_image_must_be_healthy_before_acknowledging(updater, tmp_path):
    run, fake = updater
    (tmp_path / "image.env").write_text(f"TREND_IMAGE=trendradar-pve:{SHA}\n")
    fake("docker", '''
case "$1" in
    compose) echo container ;;
    inspect) echo "sha256:test ${HEALTH:-healthy}" ;;
    image) echo sha256:test ;;
esac''')
    passed = run("update.sh", SHA)
    assert passed.returncode == 0
    assert (tmp_path / "image.applied").read_text().split()[:2] == [SHA, "sha256:test"]
    failed = run("update.sh", SHA, HEALTH="unhealthy")
    assert failed.returncode != 0
    assert "Image deployed" not in failed.stdout


def test_exact_image_rolls_back_on_failed_health_check(updater, tmp_path):
    run, fake = updater
    fake("jq", 'cat >/dev/null; echo ahead')
    fake("curl", '''
if [[ $* == *"/compare/"* ]]; then
    echo '{"status":"ahead"}'
    exit
fi
while [[ $1 != -o ]]; do shift; done
target=$2
case "${target##*/}" in
    compose.yaml) echo candidate > "$target" ;;
    IMAGE_ID) echo sha256:new > "$target" ;;
    trendradar-image.tar.gz) echo candidate-image > "$target" ;;
    SHA256SUMS) printf 'candidate-image\\n' | sha256sum | sed 's/  -$/  trendradar-image.tar.gz/' > "$target" ;;
esac
''')
    fake("docker", '''
if [[ $1 == image ]]; then echo sha256:new; exit; fi
if [[ $* == *" up "* ]] && grep -q candidate compose.yaml; then
    echo "bad new writer" > output/news.db
    exit 1
fi
''')
    (tmp_path / "image.env").write_text(f"TREND_IMAGE=trendradar-pve:{SHA}\n")
    (tmp_path / "compose.yaml").write_text("original\n")
    (tmp_path / "runtime.env").write_text("EXAMPLE=sample\n")
    (tmp_path / "output").mkdir()
    (tmp_path / "output/news.db").write_text("original data\n")
    failure = run("update.sh", NEXT_SHA)
    assert failure.returncode == 1, failure.stderr
    assert (tmp_path / "failed-release").read_text().strip() == f"pve-{NEXT_SHA}"
    assert (tmp_path / "compose.yaml").read_text() == "original\n"
    assert (tmp_path / "output/news.db").read_text() == "original data\n"
    assert not (tmp_path / "image.applied").exists()


@pytest.mark.parametrize("status", [200, 404])
def test_runtime_request_fails_on_wrong_or_missing_artifact(updater, tmp_path, status):
    run, fake = updater
    fake("curl", 'while [[ $1 != -o ]]; do shift; done; echo wrong > "$2"; echo -n "$STATUS"')
    retried = run("runtime-update.sh", "a" * 64, STATUS=str(status))
    assert retried.returncode != 0
    assert not (tmp_path / "runtime.applied").exists()
