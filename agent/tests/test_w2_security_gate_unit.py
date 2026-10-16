"""scripts/copilot/security_gate.sh: the weak-account audit blocks the tunnel. All tools are stubbed; nothing live is touched.

Only the weak-password line and the overall exit code are asserted, so the other gate checks (stubbed to succeed or fail
quietly) do not matter here.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "copilot" / "security_gate.sh"
TUNNEL = REPO / "scripts" / "copilot" / "tunnel.sh"

DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
if [[ "$*" == *fixture_accounts_audit.php* ]]; then
  [ -n "${AUDIT_OUT:-}" ] && printf '%b\n' "$AUDIT_OUT"
  exit "${AUDIT_RC:-0}"
fi
exit 1
"""
QUIET = "#!/usr/bin/env bash\nexit 0\n"


@pytest.fixture
def gate(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER), ("curl", QUIET), ("git", QUIET), ("python3", QUIET)):
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.log"
    calls.write_text("")

    def run(**extra):
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls),
               **{k: str(v) for k, v in extra.items()}}
        p = subprocess.run(["bash", str(GATE)], capture_output=True, text=True, env=env, timeout=60)
        line = next((l for l in p.stdout.splitlines() if "weak-password audit" in l or "default or guessable" in l), "")
        return p, line, calls.read_text()

    return run


def test_weak_accounts_fail_the_gate_and_are_named(gate):
    p, line, _ = gate(AUDIT_RC=1, AUDIT_OUT="accountant  WEAK (default or guessable)\nadmin  ok\nphysician  WEAK (default or guessable)")
    assert line.startswith("FAIL") and "accountant,physician" in line, p.stdout
    assert p.returncode == 1


def test_clean_accounts_pass_that_check(gate):
    _, line, _ = gate(AUDIT_RC=0, AUDIT_OUT="admin  ok")
    assert line.startswith("PASS"), line


@pytest.mark.parametrize("rc", [2, 125, 126, 127])
def test_an_audit_that_cannot_run_fails_closed(gate, rc):
    p, line, _ = gate(AUDIT_RC=rc)
    assert line.startswith("FAIL") and "could not run" in line, p.stdout
    assert p.returncode == 1


def test_the_gate_never_prints_a_password_or_a_hash(gate):
    p, _, _ = gate(AUDIT_RC=1, AUDIT_OUT="physician  WEAK (default or guessable)")
    assert "$2y$" not in p.stdout and "password_verify" not in p.stdout


def test_the_audit_is_read_only_and_runs_as_the_web_user(gate):
    _, _, calls = gate(AUDIT_RC=0)
    audit = next(l for l in calls.splitlines() if "fixture_accounts_audit.php" in l)
    assert audit.startswith("docker exec -u apache ") and " php " in audit
    assert not any(w in audit.lower() for w in ("update", "delete", "insert"))


def test_tunnel_up_runs_the_gate_before_cloudflared_so_a_failing_gate_stops_it():
    up = TUNNEL.read_text().split("cmd_up() {", 1)[1].split("\n}\n", 1)[0]
    assert up.index("security_gate.sh") < up.index("cloudflared tunnel"), "the gate must run before the tunnel starts"
    assert "Not starting the tunnel" in up and "exit 1" in up.split("security_gate.sh", 1)[1].split("\n", 1)[0]
