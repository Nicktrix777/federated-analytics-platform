#!/usr/bin/env python3
"""
Checks docker-compose.yml / docker-compose.dev.yml against .env.example so the
class of bug from the 2026-08-04 changelog (a compose var silently hardcoded
instead of ${...}-interpolated, so setting it in .env had zero effect) can't
land again unnoticed.

Exits non-zero on:
  - a ${VAR} referenced in either compose file with no matching key in .env.example
  - a PASSWORD/SECRET/TOKEN/KEY-shaped compose value that's a bare literal
    instead of ${...}-interpolated

Warns (non-fatal) on:
  - a .env.example key never referenced via ${...} in either compose file
    (often fine — some values are intentionally fixed docker-network internals
    like TRINO_HOST, documented in .env.example for reference only)

Usage: python3 scripts/check-env-sync.py   (also wired into `make env-check` and CI)
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO / ".env.example"
COMPOSE_FILES = [REPO / "docker-compose.yml", REPO / "docker-compose.dev.yml"]

VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:[-?][^}]*)?\}")
KV_LINE_RE = re.compile(r"^\s*-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$")
SECRET_KEY_RE = re.compile(r"PASSWORD|SECRET|TOKEN|KEY", re.IGNORECASE)


def load_env_example_keys(path):
    keys = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def scan_compose(path):
    referenced = set()
    hardcoded_secrets = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        for m in VAR_REF_RE.finditer(raw):
            referenced.add(m.group(1))
        kv = KV_LINE_RE.match(raw)
        if not kv:
            continue
        key, value = kv.group(1), kv.group(2)
        if not SECRET_KEY_RE.search(key):
            continue
        if "${" in value:
            continue
        hardcoded_secrets.append((lineno, key, value))
    return referenced, hardcoded_secrets


def main():
    if not ENV_EXAMPLE.exists():
        print(f"missing {ENV_EXAMPLE}", file=sys.stderr)
        return 1

    example_keys = load_env_example_keys(ENV_EXAMPLE)

    all_referenced = set()
    errors = []
    for compose_file in COMPOSE_FILES:
        if not compose_file.exists():
            continue
        referenced, hardcoded = scan_compose(compose_file)
        all_referenced |= referenced
        for lineno, key, value in hardcoded:
            errors.append(
                f"{compose_file.name}:{lineno}: {key} looks like a secret but is "
                f"a bare literal ({value!r}) instead of ${{...}}-interpolated"
            )

    for var in sorted(all_referenced - example_keys):
        errors.append(
            f"${{{var}}} is referenced in compose but has no matching key in .env.example"
        )

    unreferenced = sorted(example_keys - all_referenced)

    if errors:
        print("env-sync check FAILED:\n")
        for e in errors:
            print(f"  ✗ {e}")
        print()

    if unreferenced:
        print(
            "env-sync check: .env.example keys not referenced via ${...} in compose "
            "(informational — may be intentionally fixed/internal values):"
        )
        for k in unreferenced:
            print(f"  · {k}")
        print()

    if errors:
        return 1

    print("env-sync check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
