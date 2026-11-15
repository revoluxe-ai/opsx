#!/usr/bin/env bash
# ============================================================================
# setup.sh — TARGET-local OpenSpec CLI provider bootstrap (explicit network).
# ============================================================================
# Назначение: установить pinned @fission-ai/openspec из package-lock.json.
# Вызывается: вручную или CI перед первым использованием `opsx_tools/openspec-cli`.
# Обычные OPSX/openspec команды НЕ должны вызывать этот скрипт автоматически.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if ! command -v npm >/dev/null 2>&1; then
  echo "OPENSPEC_CLI_SETUP_FAIL: npm not found on PATH" >&2
  exit 1
fi

npm ci --no-fund --no-audit

BIN="${SCRIPT_DIR}/node_modules/.bin/openspec"
if [[ ! -x "${BIN}" ]]; then
  echo "OPENSPEC_CLI_SETUP_FAIL: missing binary after npm ci: ${BIN}" >&2
  exit 1
fi

VERSION="$("${BIN}" --version 2>/dev/null || true)"
if [[ "${VERSION}" != "1.14.1" ]]; then
  echo "OPENSPEC_CLI_SETUP_FAIL: expected openspec 1.14.1, got ${VERSION:-<empty>}" >&2
  exit 1
fi

echo "OPENSPEC_CLI_SETUP_OK: @fission-ai/openspec@${VERSION}"
