#!/usr/bin/env bash
# Security gate for exposing the local OpenEMR through a tunnel. Read-only: it checks and reports, it never changes anything.
# Exit 0 only if every check passes. Run before ./scripts/copilot/tunnel.sh (which calls it). Synthetic data only.
set -uo pipefail
cd "$(dirname "$0")/../.."
DB="${DB_CONTAINER:-development-easy-light-mysql-1}"
OE="${OE_CONTAINER:-development-easy-light-openemr-1}"
DB_ROOT_PASS="${DB_ROOT_PASS:-root}"
fail=0
ok()  { printf 'PASS  %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
q() { docker exec "$DB" mariadb -uroot -p"$DB_ROOT_PASS" openemr -N -e "$1" 2>/dev/null; }

# 1. Credentials
check "DB root password is not the default 'root'" \
  '! docker exec "$DB" mariadb -uroot -proot -e "select 1"'
admin_is_default() {
  local h; h=$(q "select password from users_secure where username='admin'")
  [ -n "$h" ] && docker exec "$OE" php -r 'exit(password_verify("pass", $argv[1]) ? 0 : 1);' "$h"
}
check "admin password is not the default 'pass'" '! admin_is_default'

# 2. Ports: only OpenEMR may be reachable from other machines
for spec in "8320:MariaDB" "8310:phpMyAdmin" "8400:agent"; do
  port="${spec%%:*}"; name="${spec#*:}"
  bound=$(docker ps --format '{{.Ports}}' | tr ',' '\n' | grep -E ":${port}->" | grep -E '^ *(0\.0\.0\.0|\[::\])' || true)
  if [ -z "$bound" ]; then ok "$name port $port is not published to the network"; else bad "$name port $port is published to the network ($bound)"; fi
done

# 3. OpenEMR settings
check "password grant is disabled (oauth_password_grant=0)" '[ "$(q "select gl_value from globals where gl_name=\"oauth_password_grant\"")" = 0 ]'
check "portal is disabled (portal_onsite_two_enable=0)" '[ "$(q "select gl_value from globals where gl_name=\"portal_onsite_two_enable\"")" = 0 ]'
check "REST portal API is disabled (rest_portal_api=0)" '[ "$(q "select gl_value from globals where gl_name=\"rest_portal_api\"")" = 0 ]'
check "API log option is 1" '[ "$(q "select gl_value from globals where gl_name=\"api_log_option\"")" = 1 ]'

check "session cookies over HTTPS carry the Secure flag" \
  '[ "$(curl -sk -D - -o /dev/null "https://localhost:9300/interface/login/login.php?site=default" | grep -i "^set-cookie" | grep -vic "; secure")" = 0 ]'

# 4. Secrets are not tracked or in history
check "agent/.env and module config.local.php are git-ignored" \
  'git check-ignore -q agent/.env && git check-ignore -q interface/modules/custom_modules/oe-module-copilot/config.local.php'
check "no .env or config.local.php was ever committed" \
  '[ -z "$(git log --all --name-only --pretty=format: | grep -E "(^|/)(\.env|config\.local\.php)$" | sort -u)" ]'
check "secret scan of tracked files and history finds nothing" 'python3 scripts/copilot/secret_scan.py'

exit $fail
