"""The unattended heartbeat must never mistake a broken check for a quiet market.

From 2026-09-04 to 2026-10-16 every launchd run of `pre_run.sh --check` died with rc 127
(`uv: command not found` — launchd's PATH has no ~/.local/bin), and `heartbeat.sh`, which only
spoke on rc 10, printed "quiet — nothing needs a review" eleven times in a row. These tests pin
the two defences: scripts resolve `uv` themselves, and the heartbeat treats any rc other than
0/10 as an alert.

Run: uv run pytest tests/unit/test_heartbeat_scripts.py -q
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
_PIPELINE = ["pre_run.sh", "score_run.sh", "post_run.sh", "heartbeat.sh"]


def _invocations(path: Path):
    """Lines that EXECUTE `uv run` — comments and echoed text are not invocations."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s.startswith("#") or s.startswith("echo"):
            continue
        # drop a quoted label such as  echo "\$ uv run $*"  that shares the line with real code
        code = re.sub(r'echo\s+"[^"]*"', "", line)
        if re.search(r"\buv run\b", code):
            yield n, line


@pytest.mark.parametrize("name", _PIPELINE)
def test_no_bare_uv_in_pipeline_scripts(name):
    """A bare `uv run` resolves through PATH, which under launchd lacks ~/.local/bin."""
    bare = [(n, l) for n, l in _invocations(SCRIPTS / name)
            if not re.search(r'"\$UV"\s+run\b', l)]
    assert bare == [], f"{name} invokes uv without the resolved path: {bare}"


@pytest.mark.parametrize("name", ["pre_run.sh", "score_run.sh", "post_run.sh"])
def test_scripts_resolve_uv_and_exit_3_when_absent(name):
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    assert 'UV="$(command -v uv || echo "$HOME/.local/bin/uv")"' in text
    assert re.search(r'if \[ ! -x "\$UV" \]; then.*?exit 3', text, flags=re.S)
    # resolved BEFORE the first real use
    assert text.index('UV="$(command -v uv') < text.index('"$UV" run')


def test_plist_puts_local_bin_on_launchd_path():
    plist = (SCRIPTS / "launchd" / "com.catalyx.heartbeat.plist").read_text(encoding="utf-8")
    assert "__HOME__/.local/bin" in plist
    install = (SCRIPTS / "heartbeat.sh").read_text(encoding="utf-8")
    assert "__HOME__" in install, "--install must substitute __HOME__ or the PATH is literal"


def _heartbeat(tmp_path, check_cmd):
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "CATALYX_HEARTBEAT_SKIP_DATA": "1",
        "CATALYX_NO_NOTIFY": "1",
        "CATALYX_HEARTBEAT_LOG": str(tmp_path / "hb.log"),
        "CATALYX_CHECK_CMD": check_cmd,
    }
    return subprocess.run(["bash", str(SCRIPTS / "heartbeat.sh")], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_heartbeat_with_a_check_that_could_not_run_alerts(tmp_path):
    r = _heartbeat(tmp_path, "exit 127")
    assert r.returncode == 10
    assert "HEARTBEAT BROKEN" in r.stdout and "127" in r.stdout
    assert "quiet" not in r.stdout


def test_heartbeat_quiet_only_on_a_clean_check(tmp_path):
    r = _heartbeat(tmp_path, "exit 0")
    assert r.returncode == 0
    assert "quiet" in r.stdout


def test_heartbeat_relays_the_checks_own_alerts(tmp_path):
    r = _heartbeat(tmp_path, "echo '  • something moved'; exit 10")
    assert r.returncode == 10
    assert "• something moved" in r.stdout
    assert "BROKEN" not in r.stdout


def test_check_mode_has_its_own_log_so_a_manual_run_cannot_erase_it():
    text = (SCRIPTS / "pre_run.sh").read_text(encoding="utf-8")
    assert "pre_run_check_" in text
