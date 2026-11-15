#!/usr/bin/env bash
# =============================================================================
# meta_commit_batch.sh
# =============================================================================
# Назначение: общий раннер для пакетных semantic-коммитов process-поверхности
#   при `/opsx-meta-commit ALL GO` — один `commit_group` = один `git commit`.
# Таблица(и): n/a (git only; allowlist — `opsx/sources.yaml`)
# Параметры: env `OPSX_META_COMMIT_GO=1` или флаг `--go` для записи в git;
#   `DRY_RUN=1` — только plan (stat без commit).
# Вызывается: `opsx/scripts/meta_commit_groups.sh`, агент/Owner после ALL GO.
# =============================================================================

set -euo pipefail

meta_commit_batch__repo_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  printf '%s\n' "$here"
}

meta_commit_batch__path_forbidden() {
  local relpath="$1"
  case "$relpath" in
    opsx/runs/*|opsx/runs/*/*|opsx/upstream/*|.local/*|.local/*/*)
      return 0
      ;;
    src/*|tests/*|backend/*|openspec/*|run_opsx_main.py|.vscode/*)
      return 0
      ;;
    *.zip|*/*.zip)
      return 0
      ;;
  esac
  return 1
}

meta_commit_batch__assert_preflight() {
  local root="$1"
  cd "$root"
  local branch
  branch="$(git branch --show-current)"
  case "$branch" in
    main|master|prod|production)
      echo "BLOCKED: protected branch $branch (use GO_ON_PROTECTED_BRANCH in command, not this script)" >&2
      exit 2
      ;;
  esac
  if [[ -n "$(git ls-files -u)" ]]; then
    echo "BLOCKED: unmerged paths — resolve before meta batch commit" >&2
    exit 2
  fi
}

# commit_group "subject" path [path ...]
# Stages only listed paths; skips missing paths; refuses forbidden prefixes.
commit_group() {
  local subject="$1"
  shift
  if [[ $# -eq 0 ]]; then
    echo "SKIP empty group: $subject" >&2
    return 0
  fi

  local root="${META_COMMIT_REPO_ROOT:-$(meta_commit_batch__repo_root)}"
  cd "$root"

  local -a to_add=()
  local p
  for p in "$@"; do
    if [[ ! -e "$p" && ! -L "$p" ]]; then
      echo "SKIP missing: $p" >&2
      continue
    fi
    if meta_commit_batch__path_forbidden "$p"; then
      echo "REFUSE out-of-scope/forbidden: $p" >&2
      exit 3
    fi
    to_add+=("$p")
  done

  if [[ ${#to_add[@]} -eq 0 ]]; then
    echo "SKIP no existing paths: $subject" >&2
    return 0
  fi

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "DRY_RUN commit_group: $subject"
    printf '  would add: %s\n' "${to_add[@]}"
    return 0
  fi

  if [[ "${OPSX_META_COMMIT_GO:-0}" != "1" ]]; then
    echo "BLOCKED: set OPSX_META_COMMIT_GO=1 (pairs with /opsx-meta-commit ALL GO)" >&2
    exit 4
  fi

  git add "${to_add[@]}"
  if [[ -z "$(git diff --cached --name-only)" ]]; then
    echo "SKIP empty index after add: $subject" >&2
    return 0
  fi
  git diff --cached --stat
  git commit -m "$subject" \
    -m "What: batch process/runtime changes (meta_commit_groups.sh)." \
    -m "Why: /opsx-meta-commit ALL GO cleanup." \
    -m "Verification: not run - process contract change (FAST_CHECK)."
}

meta_commit_batch__usage() {
  cat <<'EOF'
Usage:
  DRY_RUN=1 bash opsx/scripts/meta_commit_groups.sh     # plan only
  OPSX_META_COMMIT_GO=1 bash opsx/scripts/meta_commit_groups.sh   # execute

Edit group lists in meta_commit_groups.sh before each ALL GO run.
EOF
}

if [[ "${BASH_SOURCE[0]}" == "${0}" && "${1:-}" == "--help" ]]; then
  meta_commit_batch__usage
  exit 0
fi
