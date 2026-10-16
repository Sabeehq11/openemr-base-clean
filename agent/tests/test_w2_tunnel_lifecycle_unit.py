"""scripts/copilot/tunnel.sh process lifecycle, against a stub cloudflared in a throwaway root.

No network and no real tunnel. The stub is a bash script (so `ps` shows "cloudflared" in its command line, like the real
one) that prints a trycloudflare URL and idles. Guards: the tunnel survives the launching shell, a stale or reused PID is
never trusted or killed, a failing gate starts nothing, and a dead URL is never published.
"""
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "copilot" / "tunnel.sh"

CLOUDFLARED = r"""#!/usr/bin/env bash
echo "start $$" >> "$CALLS"
case "${CF_MODE:-ok}" in
  die) echo "ERR cannot reach the edge" >&2; exit 1 ;;
  url_then_die) echo "INF https://flash-in-the-pan.trycloudflare.com" >&2; exit 0 ;;
esac
echo "INF https://quiet-demo-tunnel.trycloudflare.com" >&2
# A foreground sleep is the process that receives SIGTERM, and it does not run this trap. Read on a closed fd
# wakes immediately when the process is signaled, which is how a real cloudflared leaves on SIGTERM.
trap 'exit 0' TERM INT
exec 3<&0
while :; do read -r -t 30 -u 3 || true; done
"""
GATE = '#!/usr/bin/env bash\necho "gate" >> "$CALLS"\nexit "${GATE_RC:-0}"\n'


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie has exited. `kill -0` still succeeds, and PID 1 may not have reaped it yet, so it is not a live tunnel.
    stat = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    return not stat.startswith("Z")


@pytest.fixture
def tunnel(tmp_path):
    root, bin_dir, calls = tmp_path / "root", tmp_path / "bin", tmp_path / "calls.log"
    _exe(root / "scripts" / "copilot" / "tunnel.sh", SCRIPT.read_text())
    _exe(root / "scripts" / "copilot" / "security_gate.sh", GATE)
    _exe(bin_dir / "cloudflared", CLOUDFLARED)
    (root / "deploy" / "tunnel").mkdir(parents=True)
    (root / "deploy" / "tunnel" / ".env").write_text("DB_ROOT_PASS=dbsecretvalue\nOE_ADMIN_PASS=adminsecretvalue\n")
    calls.write_text("")
    pidf, urlf = root / "deploy/tunnel/cloudflared.pid", root / "deploy/tunnel/url.txt"
    extra_pids: list[int] = []

    def env(**extra):
        return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls),
                "TUNNEL_WAIT_SECONDS": "6", **{k: str(v) for k, v in extra.items()}}

    def run(*args, **extra):
        # capture_output also proves the tunnel does not keep the caller's stdout/stderr pipes open
        return subprocess.run(["bash", str(root / "scripts/copilot/tunnel.sh"), *args], cwd=root, env=env(**extra),
                              capture_output=True, text=True, timeout=30)

    run.root, run.pidf, run.urlf, run.calls, run.env, run.extra_pids = root, pidf, urlf, calls, env, extra_pids
    yield run
    pids = list(extra_pids)
    if pidf.exists() and pidf.read_text().strip().isdigit():
        pids.append(int(pidf.read_text()))
    for p in pids:
        if _alive(p):
            os.kill(p, signal.SIGKILL)


def _starts(t) -> int:
    return t.calls.read_text().count("start ")


def test_up_starts_the_tunnel_records_pid_and_url_and_runs_the_gate_first(tunnel):
    p = tunnel("up")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "https://quiet-demo-tunnel.trycloudflare.com" in p.stdout
    assert tunnel.urlf.read_text().strip() == "https://quiet-demo-tunnel.trycloudflare.com"
    assert _alive(int(tunnel.pidf.read_text()))
    assert tunnel.calls.read_text().splitlines()[0] == "gate"


def test_the_tunnel_survives_the_launching_shell_being_killed(tunnel):
    """The reported failure: cloudflared died when the launching command's process group ended."""
    launcher = subprocess.Popen(["bash", str(tunnel.root / "scripts/copilot/tunnel.sh"), "up"], cwd=tunnel.root,
                                env=tunnel.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    launcher.communicate(timeout=30)
    pid = int(tunnel.pidf.read_text())
    assert os.getsid(pid) not in (os.getsid(0), launcher.pid), "cloudflared must run in its own session"
    for sig in (signal.SIGHUP, signal.SIGKILL):  # what ending a terminal or tool session does to the launcher's group
        try:
            os.killpg(launcher.pid, sig)
        except ProcessLookupError:
            pass
    time.sleep(1)
    assert _alive(pid), "the tunnel must outlive the launching shell and its process group"
    assert tunnel("status").returncode == 0


def test_a_failing_gate_starts_nothing_and_publishes_nothing(tunnel):
    p = tunnel("up", GATE_RC=1)
    assert p.returncode == 1 and "Not starting the tunnel" in p.stderr
    assert _starts(tunnel) == 0 and not tunnel.pidf.exists() and not tunnel.urlf.exists()


def test_a_second_up_reuses_the_running_tunnel(tunnel):
    tunnel("up")
    pid = tunnel.pidf.read_text()
    p = tunnel("up")
    assert p.returncode == 0 and "already running" in p.stdout
    assert _starts(tunnel) == 1 and tunnel.pidf.read_text() == pid


def test_status_reports_the_url_only_while_the_tunnel_is_alive(tunnel):
    assert tunnel("status").returncode == 1
    tunnel("up")
    p = tunnel("status")
    assert p.returncode == 0 and p.stdout.strip() == "https://quiet-demo-tunnel.trycloudflare.com"
    tunnel("down")
    assert tunnel("status").returncode == 1


@pytest.mark.parametrize("pid_text", ["999999", "not-a-pid", "", "-1", "12 34"])
def test_stale_or_garbage_state_is_cleared_and_never_reported_as_running(tunnel, pid_text):
    tunnel.pidf.write_text(pid_text)
    tunnel.urlf.write_text("https://dead-tunnel.trycloudflare.com\n")
    p = tunnel("status")
    assert p.returncode == 1 and "dead-tunnel" not in p.stdout
    assert not tunnel.pidf.exists() and not tunnel.urlf.exists()


def test_up_replaces_stale_state_with_a_fresh_tunnel(tunnel):
    tunnel.pidf.write_text("999999")
    tunnel.urlf.write_text("https://dead-tunnel.trycloudflare.com\n")
    p = tunnel("up")
    assert p.returncode == 0 and "quiet-demo-tunnel" in p.stdout and "dead-tunnel" not in p.stdout
    assert _alive(int(tunnel.pidf.read_text())) and _starts(tunnel) == 1


def test_a_reused_pid_belonging_to_another_program_is_neither_trusted_nor_killed(tunnel):
    bystander = subprocess.Popen(["sleep", "60"])
    tunnel.extra_pids.append(bystander.pid)
    tunnel.pidf.write_text(str(bystander.pid))
    tunnel.urlf.write_text("https://dead-tunnel.trycloudflare.com\n")
    assert tunnel("status").returncode == 1, "a live pid that is not cloudflared is not a tunnel"
    tunnel.pidf.write_text(str(bystander.pid))
    tunnel("down")
    assert bystander.poll() is None, "down must never kill a process that is not cloudflared"
    assert not tunnel.pidf.exists() and not tunnel.urlf.exists()
    bystander.kill()
    bystander.wait()


def test_down_stops_the_tunnel_and_clears_state(tunnel):
    tunnel("up")
    pid = int(tunnel.pidf.read_text())
    p = tunnel("down")
    assert p.returncode == 0 and "Tunnel stopped" in p.stdout
    assert not _alive(pid) and not tunnel.pidf.exists() and not tunnel.urlf.exists()


def test_down_with_nothing_running_is_harmless(tunnel):
    assert tunnel("down").returncode == 0


def test_cloudflared_exiting_before_a_url_fails_fast_and_leaves_no_state(tunnel):
    start = time.time()
    p = tunnel("up", CF_MODE="die", TUNNEL_WAIT_SECONDS=30)
    assert p.returncode == 1 and "did not start or did not stay up" in p.stderr
    assert time.time() - start < 10, "must stop waiting when the process is already dead"
    assert not tunnel.pidf.exists() and not tunnel.urlf.exists()


def test_a_url_from_a_process_that_already_exited_is_not_published(tunnel):
    p = tunnel("up", CF_MODE="url_then_die")
    assert p.returncode == 1 and "flash-in-the-pan" not in p.stdout
    assert not tunnel.urlf.exists() and not tunnel.pidf.exists()


def test_the_log_and_state_files_are_git_ignored_and_secrets_never_printed(tunnel):
    p = tunnel("up")
    for secret in ("dbsecretvalue", "adminsecretvalue"):
        assert secret not in p.stdout + p.stderr
    for f in ("deploy/tunnel/cloudflared.pid", "deploy/tunnel/url.txt", "deploy/tunnel/cloudflared.log"):
        assert subprocess.run(["git", "check-ignore", "-q", f], cwd=REPO).returncode == 0, f
