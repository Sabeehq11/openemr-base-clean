#!/usr/bin/env bash
# Installs the W2 pre-push hook for this clone by pointing git at scripts/w2/hooks.
#   scripts/w2/install_hooks.sh            install
#   scripts/w2/install_hooks.sh --remove   undo
# Note: core.hooksPath replaces .git/hooks for this repository. If you already rely on hooks there (the OpenEMR
# pre-commit hooks installed by `openemr-cmd prek-install`), the script refuses rather than silently disabling them.
set -eu
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
HOOKS="scripts/w2/hooks"
if [ "${1:-}" = "--remove" ]; then
  git config --unset core.hooksPath || true
  echo "removed: git hooks path is back to the default"
  exit 0
fi
current="$(git config --get core.hooksPath || true)"
if [ -n "$current" ] && [ "$current" != "$HOOKS" ]; then
  echo "core.hooksPath is already set to '$current'; not changing it. Call scripts/w2/pre-push from that hook instead." >&2
  exit 1
fi
if ls .git/hooks 2>/dev/null | grep -qv '\.sample$'; then
  echo ".git/hooks already holds hooks (for example from prek). Setting core.hooksPath would disable them." >&2
  echo "Instead, add this line to .git/hooks/pre-push:  exec \"$ROOT/scripts/w2/pre-push\" \"\$@\"" >&2
  exit 1
fi
mkdir -p "$HOOKS"
printf '#!/usr/bin/env bash\nexec "$(git rev-parse --show-toplevel)/scripts/w2/pre-push" "$@"\n' > "$HOOKS/pre-push"
chmod +x "$HOOKS/pre-push" scripts/w2/pre-push
git config core.hooksPath "$HOOKS"
echo "installed: git push now runs the W2 gate first (bypass with --no-verify; CI is the authoritative check)"
