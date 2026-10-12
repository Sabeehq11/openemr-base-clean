#!/usr/bin/env bash
# Expose the local OpenEMR (and only OpenEMR) through a Cloudflare quick tunnel. Synthetic data only.
#
#   scripts/tunnel.sh harden   rotate admin and DB root passwords into deploy/tunnel/.env (git-ignored),
#                              apply the hardening overlay, restart the stack
#   scripts/tunnel.sh up       run the security gate; only if it passes, start the tunnel and print the URL
#   scripts/tunnel.sh down     stop the tunnel (the stack keeps running, now hardened)
#   scripts/tunnel.sh unharden remove the overlay and go back to the stock dev stack (default credentials NOT restored)
#
# Secrets are written to deploy/tunnel/.env and never printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
need_env() { [ -f "$ENV_FILE" ] || { echo "Run scripts/tunnel.sh harden first." >&2; exit 2; }; load_env; }

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
    echo "Hardened. Credentials are in $ENV_FILE (not printed). Run: scripts/tunnel.sh up"
}

cmd_up() {
    need_env
    command -v cloudflared >/dev/null || { echo "cloudflared is not installed (brew install cloudflared)." >&2; exit 2; }
    DB_ROOT_PASS="$DB_ROOT_PASS" ./scripts/security_gate.sh || { echo "Security gate failed. Not starting the tunnel." >&2; exit 1; }
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null && { echo "Tunnel already running: $(cat "$URL_FILE")"; return; }
    : > "$LOG_FILE"
    # --no-autoupdate keeps the process predictable. The origin is OpenEMR over HTTPS with its self-signed cert.
    nohup cloudflared tunnel --no-autoupdate --no-tls-verify --url https://localhost:9300 >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 40); do
        url=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -1 || true)
        [ -n "$url" ] && { echo "$url" > "$URL_FILE"; echo "Tunnel: $url"; return; }
        sleep 1
    done
    echo "Tunnel did not report a URL; see $LOG_FILE" >&2; exit 1
}

cmd_down() {
    [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null || true
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
    unharden) cmd_unharden ;;
    *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
