#!/usr/bin/env bash
# Local setup: start OpenEMR (light dev stack) and add synthetic patients
# (labs, meds, encounters) for the agent and audit. Demo data only.
#
# Safe to re-run: by default nothing is reset or overwritten.
#   (no flags)     start the stack; seed synthetic patients only if they are missing
#   --reset-demo   DESTRUCTIVE: wipe the OpenEMR database, reinstall, load stock demo data,
#                  then seed synthetic patients (all existing data is lost)
#   --reseed       re-create the synthetic patients (pid >= 100); other data is untouched
#   -h, --help     show this help
#
# App: http://localhost:8300 (admin / pass), phpMyAdmin: http://localhost:8310
set -euo pipefail

RESET_DEMO=0
RESEED=0
for arg in "$@"; do
    case "$arg" in
        --reset-demo) RESET_DEMO=1 ;;
        --reseed) RESEED=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_DIR="$ROOT/docker/development-easy-light"
OE=development-easy-light-openemr-1
DB=development-easy-light-mysql-1

db() { docker exec -i "$DB" mariadb -uroot -proot openemr "$@"; }

cd "$COMPOSE_DIR"
docker compose up -d

echo "Waiting for OpenEMR to become healthy (first boot takes ~5 min)..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$OE")" = "healthy" ]; do sleep 15; done

if [ "$RESET_DEMO" = "1" ]; then
    echo "WARNING: --reset-demo wipes the OpenEMR database and reloads stock demo data."
    docker exec "$OE" /root/devtools dev-reset-install-demodata >/dev/null
    RESEED=1
fi

seeded=$(db -N -e "SELECT COUNT(*) FROM patient_data WHERE pid >= 100" 2>/dev/null || echo 0)
if [ "$RESEED" = "1" ] || [ "${seeded:-0}" = "0" ]; then
    echo "Seeding synthetic patients..."
    python3 "$ROOT/scripts/seed_demo_patients.py" | db
else
    echo "Synthetic patients already present ($seeded rows); leaving data untouched (use --reseed to recreate)."
fi

echo "Ready: http://localhost:8300  (admin / pass); phpMyAdmin http://localhost:8310"
