#!/usr/bin/env bash
# Локальный gate: STRICT_ON_TOUCH guard + pyright-strict (как в CI/opsx-static-check Wave A).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

PYRIGHT_BIN="${ROOT}/.venv/bin/pyright"
if [[ ! -x "$PYRIGHT_BIN" ]]; then
  echo "ERROR: missing pyright at $PYRIGHT_BIN" >&2
  exit 2
fi

echo "==> strict-on-touch guard"
python3 -m tools.pyright.strict_on_touch --repo-root "$ROOT"
guard_status=$?
if [[ "$guard_status" -ne 0 ]]; then
  exit "$guard_status"
fi

echo "==> pyright-strict"
exec "$PYRIGHT_BIN" --project pyrightconfig_strict.json "$@"
