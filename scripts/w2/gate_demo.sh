#!/usr/bin/env bash
# Proves the eval gate catches regressions. Injects three faults one at a time (citation, safety, PHI), runs the gate
# on each, expects exit 1, then restores the file byte for byte. Evidence goes to evals/results/regression-evidence-<date>.md.
#
#   scripts/w2/gate_demo.sh
#
# Files are restored from a private backup, not git, so uncommitted work is safe. The trap restores on any exit.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/agent/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
OUT="evals/results/regression-evidence-$(date +%Y-%m-%d).md"
BK="$(mktemp -d)"
CURRENT=""

restore() {
  if [ -n "$CURRENT" ] && [ -f "$BK/backup" ]; then
    cp "$BK/backup" "$CURRENT"
    cmp -s "$BK/backup" "$CURRENT" || echo "WARNING: $CURRENT was not restored" >&2
    CURRENT=""
  fi
}
trap 'restore; rm -rf "$BK"' EXIT

inject() { # file, exact text to replace, replacement
  CURRENT="$1"
  cp "$1" "$BK/backup"
  FILE="$1" OLD="$2" NEW="$3" "$PY" - <<'EOF'
import os, sys
p, old, new = os.environ["FILE"], os.environ["OLD"], os.environ["NEW"]
s = open(p).read()
if s.count(old) != 1:
    sys.exit(f"cannot inject: expected one occurrence of the target line in {p}, found {s.count(old)}")
open(p, "w").write(s.replace(old, new))
EOF
}

echo "Baseline run (must pass before any fault is injected)..."
"$PY" -m evals.gate > "$BK/clean.txt" 2>&1 || { cat "$BK/clean.txt"; echo "gate already failing: fix that first"; exit 2; }
cat "$BK/clean.txt"

{
  echo "# W2 gate regression evidence"
  echo
  echo "Generated $(date -u +%Y-%m-%dT%H:%M:%SZ). Each fault is injected alone, the gate is run, then the file is restored."
  echo
  echo "Clean run:"; echo '```'; cat "$BK/clean.txt"; echo '```'
} > "$OUT"

FAILED_TO_CATCH=0
run_fault() { # title, failure class
  echo; echo "== $1"
  "$PY" -m evals.gate > "$BK/out.txt" 2>&1
  code=$?
  cat "$BK/out.txt"
  restore
  {
    echo; echo "## $1"; echo; echo "Class: $2. Gate exit code: **$code** (1 means the gate blocked it)."; echo
    echo '```'; cat "$BK/out.txt"; echo '```'
  } >> "$OUT"
  if [ "$code" -ne 1 ]; then echo "NOT CAUGHT: $1"; FAILED_TO_CATCH=1; fi
}

inject agent/app/w2/verify.py \
  "        if not _numbers(claim.text) <= _numbers(evidence):" \
  "        if False:  # INJECTED FAULT: number check off"
run_fault "1. Verifier no longer checks that claim numbers appear in the cited source" "citation and factual accuracy"

inject agent/app/loop.py \
  "        advice = classify(question)" \
  "        advice = None  # INJECTED FAULT: advice requests bypass classify"
run_fault "2. Advice-shaped questions bypass the advice classifier" "safety refusal"

inject agent/app/w2/ingest.py \
  '        log_event(logger, "w2_extract", status="ok", document_id=document_id, facts=len(facts))' \
  '        log_event(logger, "w2_extract", status="ok", document_id=document_id, facts=len(facts), sample=str(facts))  # INJECTED FAULT'
run_fault "3. An extracted value is written to the log" "PHI in logs"

echo; echo "Final run with every file restored:"
"$PY" -m evals.gate | tee -a "$BK/final.txt"
{ echo; echo "## After restore"; echo; echo '```'; cat "$BK/final.txt"; echo '```'; } >> "$OUT"
echo; echo "Evidence: $OUT"
exit "$FAILED_TO_CATCH"
