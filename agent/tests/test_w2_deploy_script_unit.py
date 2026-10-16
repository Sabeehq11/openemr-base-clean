"""scripts/copilot/deploy_w2.sh against stubbed docker and curl, and stub tunnel scripts, in a throwaway root.

Nothing live is touched: the script runs with W2_DEPLOY_ROOT pointing at a temp directory whose scripts are stubs that
only append to a call log. Guards the three deployment safeguards: idempotent, non-destructive, agent stays private.
"""
import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "copilot" / "deploy_w2.sh"

DOCKER_STUB = r"""#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
case "$1" in
  ps) echo abc123 ;;
  compose)
    if [[ " $* " == *" config "* ]]; then python3 "$STUB_COMPOSE_CONFIG" "$3"; fi ;;
  exec)
    if [ "$2" = "-i" ]; then cat > /dev/null; exit 0; fi
    sql="${@: -1}"
    case "$sql" in
      *"from patient_data"*) echo "${FAKE_PATIENTS:-5}" ;;
      *"from documents"*) echo "${FAKE_DOCS:-0}" ;;
      *"from modules"*) echo "${FAKE_MODULE:-1}" ;;
      *"from oauth_clients"*) echo "${FAKE_CLIENT:-1}" ;;
      *w2_documents*) echo "${FAKE_W2DOCS:-0}" ;;
    esac ;;
esac
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
echo "curl $*" >> "$CALLS"
for a in "$@"; do last="$a"; done
case "$last" in
  */ready) echo '{"status":"ready","checks":{}}' ;;
  */openapi.json)
    if [ "${FAKE_ROUTE:-1}" = 1 ]; then echo '{"paths":{"/v1/w2/documents/{document_id}/page":{"post":{}}}}'
    else echo '{"paths":{"/v1/w2/documents":{"post":{}}}}'; fi ;;
  */health) [ "${FAKE_LEAK:-0}" = 1 ] && echo '{"status":"ok"}' ;;
esac
exit 0
"""

SECRET_FILES = ("agent/.env", "deploy/tunnel/.env",
                "interface/modules/custom_modules/oe-module-copilot/config.local.php")
MUTATING = ("tunnel.sh", "copilot_register_module.sh", "seed_demo_patients.py", " down", " -v", "unharden", "harden")


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stub_scripts(root: Path) -> None:
    _exe(root / "scripts" / "copilot" / "copilot_register_module.sh",
         '#!/usr/bin/env bash\necho "copilot_register_module.sh $*" >> "$CALLS"\n')
    # `status` is read-only and not logged as a call. It reports "running" only for a live pid, like the real script.
    _exe(root / "scripts" / "copilot" / "tunnel.sh",
         '#!/usr/bin/env bash\n'
         'if [ "$1" = status ]; then\n'
         '  p="$(cat deploy/tunnel/cloudflared.pid 2>/dev/null)"\n'
         '  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then cat deploy/tunnel/url.txt; exit 0; fi\n'
         '  exit 1\n'
         'fi\n'
         'echo "tunnel.sh $*" >> "$CALLS"\n')
    # the real tunnel.sh text is also grepped for the tunnel origin, so keep an origin line that is OpenEMR only
    with open(root / "scripts" / "copilot" / "tunnel.sh", "a") as f:
        f.write("# cloudflared tunnel --no-autoupdate --url https://localhost:9300\n")
    _exe(root / "scripts" / "copilot" / "seed_demo_patients.py",
         '#!/usr/bin/env python3\nimport os\nopen(os.environ["CALLS"], "a").write("seed_demo_patients.py\\n")\nprint("-- sql")\n')


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "root"
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "docker", DOCKER_STUB)
    _exe(bin_dir / "curl", CURL_STUB)
    _stub_scripts(root)
    (root / "agent").mkdir(parents=True)
    shutil.copy(REPO / "agent" / "docker-compose.yml", root / "agent" / "docker-compose.yml")
    (root / "agent" / ".env").write_text("W2_ENABLED=true\nMODULE_SIGNING_SECRET=topsecretvalue\nOAUTH_CLIENT_ID=client-1\n")
    mod = root / "interface" / "modules" / "custom_modules" / "oe-module-copilot"
    mod.mkdir(parents=True)
    (mod / "config.local.php").write_text("<?php return ['signing_secret' => 'topsecretvalue'];\n")
    (root / "deploy" / "tunnel").mkdir(parents=True)
    (root / "deploy" / "tunnel" / ".env").write_text("DB_ROOT_PASS=dbsecretvalue\nOE_ADMIN_PASS=adminsecretvalue\n")
    (root / "deploy" / "tunnel" / "url.txt").write_text("https://example-demo.trycloudflare.com\n")
    (root / "deploy" / "tunnel" / "cloudflared.pid").write_text(str(os.getpid()))  # a live pid: the tunnel counts as running
    calls = tmp_path / "calls.log"
    calls.write_text("")

    def run(*args, **extra):
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "W2_DEPLOY_ROOT": str(root),
               "CALLS": str(calls), "STUB_COMPOSE_CONFIG": str(Path(__file__).with_name("compose_config_stub.py")),
               **{k: str(v) for k, v in extra.items()}}
        p = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
        return p, calls.read_text()

    run.root = root
    return run


def test_dry_run_changes_nothing_and_makes_no_mutating_call(world):
    p, calls = world()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "dry run" in p.stdout
    assert not [m for m in MUTATING if m in calls], calls
    assert "up -d" not in calls  # not even the idempotent agent update


def test_second_apply_on_a_deployed_stack_is_a_no_op(world):
    p, calls = world("--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    for line in ("hardening", "module oe-module-copilot is registered", "config.local.php exists", "tunnel running"):
        assert line in p.stdout, p.stdout
    assert "tunnel.sh" not in calls and "copilot_register_module.sh" not in calls and "seed_demo_patients.py" not in calls
    # the only compose mutation is the idempotent agent update; never a destructive one
    assert "docker compose -f agent/docker-compose.yml up -d --build --no-deps --force-recreate agent" in calls
    for bad in (" down", " -v", "unharden", "volume"):
        assert bad not in calls


def test_existing_secrets_and_config_are_untouched_and_never_printed(world):
    before = {p: _sha(world.root / p) for p in SECRET_FILES}
    p, _ = world("--apply")
    assert before == {p: _sha(world.root / p) for p in SECRET_FILES}
    for secret in ("topsecretvalue", "dbsecretvalue", "adminsecretvalue"):
        assert secret not in p.stdout + p.stderr


def test_seeding_is_skipped_by_default(world):
    p, calls = world("--apply")
    assert "seeding" in p.stdout and "seed_demo_patients.py" not in calls


@pytest.mark.parametrize("env", [{"FAKE_DOCS": 3}, {"FAKE_W2DOCS": 2}])
def test_seed_is_refused_when_documents_or_extractions_exist(world, env):
    p, calls = world("--apply", "--seed", **env)
    assert p.returncode == 1 and "--seed refused" in p.stdout
    assert "seed_demo_patients.py" not in calls


def test_seed_runs_only_when_explicit_and_nothing_would_be_lost(world):
    dry, calls = world("--seed")
    assert "would change" in dry.stdout and "seed_demo_patients.py" not in calls
    p, calls = world("--apply", "--seed")
    assert p.returncode == 0 and "seed_demo_patients.py" in calls


def test_agent_exposed_beyond_localhost_fails_the_deploy(world):
    compose = world.root / "agent" / "docker-compose.yml"
    compose.write_text(compose.read_text().replace('"127.0.0.1:8400:8400"', '"0.0.0.0:8400:8400"'))
    p, _ = world("--apply")
    assert p.returncode == 1 and "non-local address" in p.stdout


def test_agent_reachable_through_the_tunnel_fails_the_deploy(world):
    p, _ = world("--apply", FAKE_LEAK=1)
    assert p.returncode == 1 and "must stay private" in p.stdout


def test_missing_agent_env_is_reported_not_invented(world):
    (world.root / "agent" / ".env").unlink()
    p, calls = world("--apply")
    assert p.returncode == 1 and "agent/.env is missing" in p.stdout
    assert not (world.root / "agent" / ".env").exists() and "up -d" not in calls


def test_w2_is_enabled_only_when_asked_and_only_that_key_changes(world):
    env = world.root / "agent" / ".env"
    env.write_text("W2_ENABLED=false\nMODULE_SIGNING_SECRET=topsecretvalue\nOAUTH_CLIENT_ID=client-1\n")
    p, _ = world("--apply")
    assert "W2_ENABLED=false" in env.read_text()
    p, _ = world("--apply", "--enable-w2")
    assert env.read_text() == "W2_ENABLED=true\nMODULE_SIGNING_SECRET=topsecretvalue\nOAUTH_CLIENT_ID=client-1\n"
    assert "topsecretvalue" not in p.stdout


def test_first_deploy_hardens_and_registers_the_module_once(world):
    (world.root / "deploy" / "tunnel" / ".env").unlink()
    (world.root / "deploy" / "tunnel" / "cloudflared.pid").unlink()
    p, calls = world("--apply", FAKE_MODULE=0)
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls.count("tunnel.sh harden") == 1 and calls.count("tunnel.sh up") == 1
    assert calls.count("copilot_register_module.sh") == 1


# ---- D1: public-port detection. Real Compose omits host_ip for a bare "8400:8400"; the old check let that through.

@pytest.mark.parametrize("spec", ["0.0.0.0:8400:8400", "8400:8400", "8400", "[::]:8400:8400", "192.168.1.5:8400:8400"])
def test_every_public_bind_of_the_agent_is_refused_and_the_agent_is_not_recreated(world, spec):
    compose = world.root / "agent" / "docker-compose.yml"
    compose.write_text(compose.read_text().replace('"127.0.0.1:8400:8400"', f'"{spec}"'))
    p, calls = world("--apply")
    assert p.returncode == 1, p.stdout
    assert "non-local address" in p.stdout, p.stdout
    assert "agent recreate (refused" in p.stdout
    assert "up -d" not in calls, f"must not start the agent on a public bind: {calls}"


@pytest.mark.parametrize("spec", ["127.0.0.1:8400:8400", "[::1]:8400:8400"])
def test_loopback_binds_are_accepted(world, spec):
    compose = world.root / "agent" / "docker-compose.yml"
    compose.write_text(compose.read_text().replace('"127.0.0.1:8400:8400"', f'"{spec}"'))
    p, _ = world("--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "agent is private" in p.stdout


def test_unreadable_compose_config_fails_closed(world, tmp_path):
    docker = tmp_path / "bin" / "docker"
    docker.write_text(docker.read_text().replace('python3 "$STUB_COMPOSE_CONFIG" "$3"', 'echo "not json"'))
    p, calls = world("--apply")
    assert p.returncode == 1 and "non-local address" in p.stdout
    assert "up -d" not in calls


@pytest.mark.skipif(shutil.which("docker") is None, reason="real docker compose not installed")
def test_stub_matches_what_real_compose_emits_for_each_port_form(tmp_path):
    """Ties the stub to reality: if Compose changes how it normalises ports, this fails and the stub is revisited."""
    import json
    expected = {"127.0.0.1:8400:8400": "127.0.0.1", "0.0.0.0:8400:8400": "0.0.0.0", "8400:8400": None, "8400": None}
    for i, (spec, host) in enumerate(expected.items()):
        f = tmp_path / f"c{i}.yml"
        f.write_text(f'services:\n  agent:\n    image: scratch\n    ports: ["{spec}"]\n')
        r = subprocess.run(["docker", "compose", "-f", str(f), "config", "--format", "json"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            pytest.skip("docker compose config is not usable here")
        real = json.loads(r.stdout)["services"]["agent"]["ports"][0]
        assert real.get("host_ip") == host, (spec, r.stdout)
        stub = subprocess.run(["python3", str(Path(__file__).with_name("compose_config_stub.py")), str(f)],
                              capture_output=True, text=True)
        assert json.loads(stub.stdout)["services"]["agent"]["ports"][0].get("host_ip") == host, spec


# ---- D2: the agent is recreated from the current image, then the C5 page route is verified on the RUNNING agent.

def test_apply_recreates_only_the_agent_without_touching_data_or_other_services(world):
    p, calls = world("--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "up -d --build --no-deps --force-recreate agent" in calls
    assert "agent recreated from the current image" in p.stdout
    for bad in (" down", " -v", "volume", "rm ", "restart", "development-easy-light"):
        assert bad not in calls.replace("development-easy-light-openemr-1", "").replace("development-easy-light-mysql-1", ""), bad
    # the compose file named is the agent's own; the OpenEMR stack file is never passed to `up`
    assert "docker/development-easy-light" not in calls


def test_dry_run_reports_the_recreate_but_performs_none(world):
    p, calls = world()
    assert "would change" in p.stdout and "--force-recreate agent" in p.stdout
    assert "up -d" not in calls


def test_a_running_agent_without_the_page_route_fails_the_deploy_with_the_fix_command(world):
    p, _ = world("--apply", FAKE_ROUTE=0)
    assert p.returncode == 1
    assert "does not expose POST /v1/w2/documents/{document_id}/page" in p.stdout
    assert "--force-recreate agent" in p.stdout


def test_the_page_route_is_confirmed_on_the_running_agent_after_startup(world):
    p, calls = world("--apply")
    assert "the running agent exposes POST /v1/w2/documents/{document_id}/page" in p.stdout
    assert calls.index("up -d --build --no-deps --force-recreate agent") < calls.index("openapi.json"), \
        "the route must be checked AFTER the agent was recreated"


def test_the_route_check_also_runs_on_a_dry_run_so_a_stale_agent_is_visible_before_applying(world):
    p, _ = world(FAKE_ROUTE=0)
    assert p.returncode == 1 and "does not expose POST" in p.stdout


# ---- R2: generated files are git-ignored; the tracked tunnel config is not.

@pytest.mark.parametrize("path", ["agent/.env.bak", "deploy/tunnel/url.txt", "deploy/tunnel/cloudflared.pid",
                                  "deploy/tunnel/cloudflared.log", "agent/.env", "deploy/tunnel/.env"])
def test_generated_and_secret_files_are_git_ignored(path):
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
    assert r.returncode == 0, f"{path} must be git-ignored"


@pytest.mark.parametrize("path", ["deploy/tunnel/docker-compose.override.yml", "deploy/tunnel/security.conf"])
def test_the_tunnel_config_that_belongs_in_the_repo_is_not_ignored(path):
    assert subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 1
