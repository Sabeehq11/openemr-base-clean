#!/usr/bin/env python3
"""Small secret scan over tracked files and git history (no external tool needed). Exit 1 on any hit.

Looks for private keys, OpenRouter/OpenAI/GitHub/AWS style tokens and the literal values of secrets held in the
git-ignored agent/.env. Third-party vendored trees are skipped. Known-safe dev placeholders are allow-listed below.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "openrouter key": re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
SKIP_PREFIXES = ("vendor/", "node_modules/", "library/sql-ssl-certs-keys/", "sites/", "public/", "swagger/", "tests/")
# The upstream OpenEMR dev compose file ships a public composer token. It is not ours; flagged in SECURITY notes.
ALLOW_FILES = {"docker/development-easy-light/docker-compose.yml"}
# Upstream OpenEMR ships throw-away TLS keys for its dev containers and CI. Only the private-key pattern is waived there.
UPSTREAM_DEV_KEY_PATHS = ("docker/library/", "ci/nginx/")
SKIP_SUFFIXES = (".png", ".jpg", ".gif", ".pdf", ".zip", ".woff", ".woff2", ".ico", ".lock", ".min.js", ".map")


def env_secrets() -> list[str]:
    env = ROOT / "agent" / ".env"
    out: list[str] = []
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if any(w in k for w in ("KEY", "SECRET", "TOKEN", "PASSWORD")) and len(v.strip()) >= 12:
                    out.append(v.strip())
    return out


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, errors="replace").stdout


def main() -> int:
    secrets = env_secrets()
    hits: list[str] = []

    def scan(label: str, text: str, path: str = "") -> None:
        for name, rx in PATTERNS.items():
            if name == "private key" and path.startswith(UPSTREAM_DEV_KEY_PATHS):
                continue
            if rx.search(text):
                hits.append(f"{label}: {name}")
        for s in secrets:
            if s in text:
                hits.append(f"{label}: value of a secret from agent/.env")

    for path in git("ls-files").splitlines():
        if path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES) or path in ALLOW_FILES:
            continue
        try:
            scan(path, (ROOT / path).read_text(errors="ignore"), path)
        except OSError:
            continue
    # History: added lines only, on files outside the skipped trees.
    patch = git("log", "--all", "-p", "--no-color", "--pretty=format:commit %h", "--",
                *[f":(exclude){p}" for p in SKIP_PREFIXES], *[f":(exclude){p}" for p in ALLOW_FILES])
    commit, cur = "?", ""
    for line in patch.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            scan(f"history {commit}", line, cur)
    for h in sorted(set(hits)):
        print("HIT", h)
    print(f"secret scan: {len(set(hits))} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
