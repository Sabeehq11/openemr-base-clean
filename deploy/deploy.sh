#!/usr/bin/env bash
# Usage on a fresh Ubuntu VPS (>=4 GB RAM, ports 80/443 open):
#   git clone <fork> && cd <fork>/deploy && cp .env.example .env && $EDITOR .env && ./deploy.sh
# Installs Docker if missing, starts the stack, seeds synthetic patients, checks readiness.
set -euo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
set -a; . ./.env; set +a
docker compose up -d
echo "Waiting for OpenEMR (first boot ~5 min)..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q openemr)" 2>/dev/null || echo none)" = healthy ] \
   || docker compose exec -T openemr curl -fsk https://localhost/meta/health/readyz >/dev/null 2>&1; do sleep 15; done
python3 ../scripts/copilot/seed_demo_patients.py | docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASS" openemr
curl -fsS "https://$DOMAIN/meta/health/readyz" && echo && echo "Live: https://$DOMAIN"
