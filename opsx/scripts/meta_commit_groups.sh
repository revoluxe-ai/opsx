#!/usr/bin/env bash
# =============================================================================
# meta_commit_groups.sh
# =============================================================================
# Назначение: редактируемый список meaning-groups для meta_commit_batch.sh.
#   Перед `/opsx-meta-commit ALL GO` правьте блок ACTIVE GROUPS ниже.
# Вызывается: Owner или агент из корня репозитория (WSL/Linux bash).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=meta_commit_batch.sh
source "${SCRIPT_DIR}/meta_commit_batch.sh"

export META_COMMIT_REPO_ROOT="${META_COMMIT_REPO_ROOT:-$(meta_commit_batch__repo_root)}"
meta_commit_batch__assert_preflight "$META_COMMIT_REPO_ROOT"

# -----------------------------------------------------------------------------
# ACTIVE GROUPS — добавляйте/удаляйте commit_group после инвентаря dirty tree.
# Пример типичного разбиения (2026-11-02, уже закоммичено — для copy-paste):
#
# commit_group "docs(commands): …" .cursor/commands/opsx-apply-*.md …
# commit_group "chore(opsx-process): sources allowlist" opsx/sources.yaml
# commit_group "refactor(opsx-process): diagnostic verify …" opsx/opsx_runtime/agents/verify/diagnostic/…
# …
#
# Не включайте: opsx/runs/**, src/**, openspec/**, zip-архивы, run_dialogs logs.
# -----------------------------------------------------------------------------

# TODO(Owner): следующий ALL GO — раскомментируйте или добавьте группы.

# commit_group "refactor(opsx-process): example group (пример — удалите)" \
#   opsx/sources.yaml

echo "meta_commit_groups.sh: no ACTIVE GROUPS defined (edit file before GO)." >&2
echo "Plan: DRY_RUN=1 bash opsx/scripts/meta_commit_groups.sh" >&2
echo "Execute: OPSX_META_COMMIT_GO=1 bash opsx/scripts/meta_commit_groups.sh" >&2
exit 0
