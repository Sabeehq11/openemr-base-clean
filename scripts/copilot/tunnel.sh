#!/usr/bin/env bash
# Expose the local OpenEMR (and only OpenEMR) through a Cloudflare quick tunnel. Synthetic data only.
#
#   scripts/copilot/tunnel.sh harden   rotate admin and DB root passwords into deploy/tunnel/.env (git-ignored),
#                              apply the hardening overlay, restart the stack
#   scripts/copilot/tunnel.sh up       run the security gate; only if it passes, start the tunnel and print the URL
#   scripts/copilot/tunnel.sh down     stop the tunnel (the stack keeps running, now hardened)
#   scripts/copilot/tunnel.sh status   print the tunnel URL and exit 0 if it is running, else exit 1 (changes nothing)
#   scripts/copilot/tunnel.sh unharden remove the overlay and go back to the stock dev stack (default credentials NOT restored)
#
# Secrets are written to deploy/tunnel/.env and never printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ENV_FILE="deploy/tunnel/.env"
COMPOSE=(docker compose -f docker/development-easy-light/docker-compose.yml -f deploy/tunnel/docker-compose.override.yml)
OE=development-easy-light-openemr-1
DB=development-easy-light-mysql-1
PID_FILE="deploy/tunnel/cloudflared.pid"
URL_FILE="deploy/tunnel/url.txt"
LOG_FILE="deploy/tunnel/cloudflared.log"

rand() { openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-24; }
load_env() { set -a; [ -f "$ENV_FILE" ] && . "./$ENV_FILE"; set +a; }
need_env() { [ -f "$ENV_FILE" ] || { echo "Run scripts/copilot/tunnel.sh harden first." >&2; exit 2; }; load_env; }

cmd_harden() {
    local old_root="${DB_ROOT_PASS:-root}"
    if [ -f "$ENV_FILE" ]; then load_env; old_root="$DB_ROOT_PASS"; else
        umask 077
        printf 'DB_ROOT_PASS=%s\nOE_ADMIN_PASS=%s\n' "$(rand)" "$(rand)" > "$ENV_FILE"
        load_env
    fi
    # 1. Change the DB root password inside the running database (the compose env var only matters on first boot).
    docker exec "$DB" mariadb -uroot -p"$old_root" -e \
        "ALTER USER 'root'@'%' IDENTIFIED BY '${DB_ROOT_PASS}'; ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_ROOT_PASS}'; FLUSH PRIVILEGES;" 2>/dev/null \
        || docker exec "$DB" mariadb -uroot -p"$DB_ROOT_PASS" -e "select 1" >/dev/null   # already rotated
    # 2. Recreate with the overlay: localhost-only ports, secure cookies, portal and password grant off.
    "${COMPOSE[@]}" up -d
    echo "Waiting for OpenEMR..."
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$OE")" = healthy ]; do sleep 5; done
    # 3. Settings that live in the database (the env vars above only apply on first install).
    local sql="UPDATE globals SET gl_value=0 WHERE gl_name IN ('oauth_password_grant','portal_onsite_two_enable','rest_portal_api');
               UPDATE globals SET gl_value=1 WHERE gl_name='api_log_option';"
    docker exec "$DB" mariadb -uroot -p"$DB_ROOT_PASS" openemr -e "$sql"
    # 4. Admin password (hashed by OpenEMR's own AuthHash, so login keeps working).
    docker exec -u apache -e NEWPASS="$OE_ADMIN_PASS" "$OE" php -r '
        $_GET["site"]="default"; $ignoreAuth=true;
        require "/var/www/localhost/htdocs/openemr/interface/globals.php";
        $h=(new OpenEMR\Common\Auth\AuthHash("auth"))->passwordHash(getenv("NEWPASS"));
        sqlStatement("UPDATE users_secure SET password=?, last_update_password=NOW(), login_fail_counter=0 WHERE username=?", [$h,"admin"]);
        echo "admin password rotated\n";'
    echo "Hardened. Credentials are in $ENV_FILE (not printed). Run: scripts/copilot/tunnel.sh up"
}

# --- tunnel process lifecycle -------------------------------------------------------------------------------------------
# The PID file alone is not proof of a tunnel: a crashed tunnel leaves it behind and the number can later be reused by an
# unrelated process (which `down` would then kill). So "running" means: the file holds a number, that process is alive,
# AND its command line is cloudflared.
tunnel_pid() { local p; p="$(cat "$PID_FILE" 2>/dev/null || true)"; [[ "$p" =~ ^[0-9]+$ ]] && echo "$p"; }
# A zombie has already exited. `kill -0` still succeeds on it, and it can linger until PID 1 reaps it, so it is not a
# running tunnel and `down` must not wait on it.
process_alive() {
    local p="$1" stat
    kill -0 "$p" 2>/dev/null || return 1
    stat="$(ps -p "$p" -o stat= 2>/dev/null || true)"
    [[ "$stat" == Z* ]] && return 1
    return 0
}
tunnel_running() {
    local p; p="$(tunnel_pid)" || return 1
    [ -n "$p" ] && process_alive "$p" && ps -p "$p" -o command= 2>/dev/null | grep -q cloudflared
}
# A recorded URL or PID with no live tunnel behind it is stale and must not be reported as a running tunnel.
clear_stale_state() { tunnel_running || rm -f "$PID_FILE" "$URL_FILE"; }

# Start cloudflared in its own session so it outlives this shell and the process that launched it (a plain `nohup ... &`
# stays in the caller's process group and is killed with it). stdin is closed so it can never wait on a terminal.
# setsid is not part of macOS, so fall back to perl's POSIX::setsid, which then exec()s so the PID stays cloudflared's.
# Ubuntu 24.04 setsid (the GitHub Actions runner) does not fork: $! is cloudflared, already in its own session.
# Do not pass --fork or --wait. --fork exits before the child can be recorded; --wait's child is not cloudflared.
launch_detached() {
    if command -v setsid >/dev/null 2>&1; then
        setsid "$@" </dev/null >"$LOG_FILE" 2>&1 &
    else
        perl -MPOSIX -e 'POSIX::setsid() != -1 or die "setsid failed\n"; exec @ARGV or die "exec failed\n"' "$@" </dev/null >"$LOG_FILE" 2>&1 &
    fi
    echo $! > "$PID_FILE"
    disown 2>/dev/null || true
}

cmd_status() {
    clear_stale_state
    if tunnel_running && [ -s "$URL_FILE" ]; then cat "$URL_FILE"; else echo "Tunnel is not running." >&2; return 1; fi
}

cmd_up() {
    need_env
    command -v cloudflared >/dev/null || { echo "cloudflared is not installed (brew install cloudflared)." >&2; exit 2; }
    DB_ROOT_PASS="$DB_ROOT_PASS" ./scripts/copilot/security_gate.sh || { echo "Security gate failed. Not starting the tunnel." >&2; exit 1; }
    clear_stale_state
    if tunnel_running; then echo "Tunnel already running: $(cat "$URL_FILE" 2>/dev/null || echo unknown)"; return; fi
    : > "$LOG_FILE"
    # --no-autoupdate keeps the process predictable. The origin is OpenEMR over HTTPS with its self-signed cert.
    launch_detached cloudflared tunnel --no-autoupdate --no-tls-verify --url https://localhost:9300
    for _ in $(seq 1 "${TUNNEL_WAIT_SECONDS:-40}"); do
        url=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -1 || true)
        if [ -n "$url" ]; then
            if tunnel_running; then echo "$url" > "$URL_FILE"; echo "Tunnel: $url"; return; fi
            break  # a URL was printed but the process is already gone: do not publish it
        fi
        # cloudflared died before announcing a URL: stop waiting
        tunnel_running || break
        sleep 1
    done
    echo "Tunnel did not start or did not stay up; see $LOG_FILE" >&2
    cmd_down >/dev/null; exit 1
}

cmd_down() {
    # Only signal the recorded process if it really is cloudflared; never a reused PID. Always clear the state files.
    if tunnel_running; then
        local p; p="$(tunnel_pid)"
        kill "$p" 2>/dev/null || true
        # Do not report the tunnel stopped while the recorded process is still running. SIGKILL is the backstop for a
        # process that ignores SIGTERM. A zombie counts as exited: it is already dead, only unreaped.
        for _ in $(seq 1 50); do process_alive "$p" || break; sleep 0.1; done
        if process_alive "$p"; then
            kill -9 "$p" 2>/dev/null || true
            for _ in 1 2 3 4 5; do process_alive "$p" || break; sleep 0.1; done
        fi
        process_alive "$p" && { echo "Tunnel process $p did not exit." >&2; return 1; }
    fi
    rm -f "$PID_FILE" "$URL_FILE"; echo "Tunnel stopped."
}

cmd_unharden() {
    cmd_down
    docker compose -f docker/development-easy-light/docker-compose.yml up -d
}

case "${1:-}" in
    harden) cmd_harden ;;
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    unharden) cmd_unharden ;;
    *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
