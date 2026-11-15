"""Промпт ProjectBugHuntAgent (семантика /opsx-project-bug-hunt для stroy-system verify)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    derive_referenced_repo_paths,
    load_change_artifact_bundle,
)


def get_prompt(
    state: GraphState,
    answer_model: type[BaseModel],
    answer_lang: Literal["русский"],
) -> str:
    """Собрать READ_ONLY project bug hunt prompt для текущего change slice.

    Жёсткий scope совпадает с PHASE_IMPL: только ``openspec/changes/<change_id>/**``
    и пути из planning-артефактов (``DERIVED_REFERENCED_PATHS``); адаптер path allowlist
    ``answer_model`` в текст не попадает. JSON-схему ответа добавляет ``ABCAgent``.
    """
    repository_path = state["repository_path"]
    change_id = str(state.get("change_id") or "").strip() or "unknown-change"
    _ = answer_model
    repo = Path(repository_path)
    artifact_bundle = load_change_artifact_bundle(change_id, repo_root=repo)
    referenced_paths = derive_referenced_repo_paths(change_id, artifact_bundle)
    artifacts_json = json.dumps(
        artifact_bundle,
        ensure_ascii=False,
        indent=2,
    )
    referenced_paths_json = json.dumps(
        referenced_paths,
        ensure_ascii=False,
        indent=2,
    )
    change_root_rel = f"openspec/changes/{change_id}"

    return f"""
You are the primary project-tuned bug hunter for stroy-system OPSX verify.

STROY-SYSTEM TARGET OVERRIDE:
- This hop runs inside `opsx/opsx_runtime` verify control-plane wiring.
- Do NOT treat donor literals (WB Sky, Poetry app/**, shop-runner, ClickHouse,
  Telegram contracts) as execution authority.
- Do NOT implement shop-runner/min_bids/Redis checks.
- Do NOT fail the whole task with TARGET_CAPABILITY_UNRESOLVED — bounded verify
  context only.

MODE: READ_ONLY — do not modify production code, do not commit or push.

## ЖЁСТКИЙ SCOPE (обязательно, только текущий change)

Ты ищешь баги **строго** в slice change `{change_id}`. Любой read/grep/glob/ls/shell **вне** списка ниже — **запрещён** (не «для контекста», не «на всякий случай», не repo-wide bug hunt).

## INPUT CONTEXT (embedded planning — do not re-read openspec)
```json
{artifacts_json}
```

**Разрешено открывать и искать только:**
- Production/tests/decisions paths из `DERIVED_REFERENCED_PATHS` ниже (если нужен код вне встроенного текста).
- Не читай `apply-*`, `verify/**`, `scenario-coverage/*.receipt.json`, `opsx/receipts/**`.

**Запрещено:**
- Repo-wide grep/glob/ls/shell без path-prefix из `DERIVED_REFERENCED_PATHS` или `{change_root_rel}/`.
- Другие `openspec/changes/<другой_id>/`, несвязанные пакеты, обход `src/` целиком без явного пути в artifacts change.
- `opsx/runs/**`, dogfood/repair trees, drive-by exploration.
- Если путь **не** в `{change_root_rel}/**` и **не** в `DERIVED_REFERENCED_PATHS` — **не открывай**; findings строй по уже прочитанным файлам в scope и diff slice change, не собирай доказательства вне scope.

DERIVED_REFERENCED_PATHS (repo-relative, из planning artifacts change `{change_id}`):
{referenced_paths_json}

Use read/grep/glob/ls/shell **only** inside ЖЁСТКИЙ SCOPE above (prefix every grep/glob path).

Full Repository Path: {repository_path}
Diff: change-anchored slice for change `{change_id}` (touched paths must stay inside ЖЁСТКИЙ SCOPE)
Base Branch: stage

METHOD (cheap-first, scope-bound):
1. Diff-anchored suspects only in allowed production paths for this change.
2. Reachability proof for each candidate (no reachability → not confirmed production bug).
3. Classify every finding with exactly one production classification:
   confirmed production bug | reachable but low-risk | unclear reachability |
   dead legacy | tests-only | unreachable | adjacent | false positive

LOC_LINK contract:
- Every finding with a code location MUST use loc_link as plain repo-relative path:line.
- loc_link MUST point to a path inside ЖЁСТКИЙ SCOPE; do not cite files you were forbidden to open.

FINDING BAR:
- Emit confirmed production bug only with realistic production path and incorrect behavior.
- Do not assign executors; repair is downstream bug-team workflow.

OUTPUT CONTRACT:
Return ONLY valid JSON matching the schema appended after this prompt (no markdown fences, no prose outside JSON).

Write human-readable string fields in {answer_lang}.
Keep JSON keys, enum values, and paths unchanged.
If no confirmed production bugs, return HUNT_COMPLETE with empty findings and
confirmed_production_bug_count=0.
"""
