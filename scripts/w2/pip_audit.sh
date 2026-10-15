#!/usr/bin/env bash
# Dependency audit for the agent. Fails on any known advisory that is not listed in scripts/w2/pip-audit-known.txt.
# The list holds advisories that exist today and need a framework upgrade; anything new blocks the build.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ARGS=()
while read -r _pkg _ver id _fix; do
  case "$_pkg" in ''|'#'*) continue ;; esac
  ARGS+=(--ignore-vuln "$id")
done < scripts/w2/pip-audit-known.txt
pip-audit -r agent/requirements.txt ${ARGS[@]+"${ARGS[@]}"}
