"""Промпт apply-scaffold hop: встроенный stage authority + контекст change."""

from __future__ import annotations

from typing import Any

_STAGE_KEY = "apply_scaffold"

_SCAFFOLD_TOOL_SURFACE = """
## Scaffold tool surface
Tools: read and edit only. grep, glob, ls, and shell are not available.
Search results are `scoped_files` in the hop context. They were produced by the
runtime inside `work_scope` from APPLY-DESIGN. Do not pass a repository root,
`.`, or `git show`.
If `work_scope` is empty, return status=BLOCKED and put INPUT_SCOPE_INCOMPLETE
in remaining_gaps. Do not discover files outside that list.

## Read scope (hard)
Allowed read roots for this hop only:
- `src/**`, `app/**`, `tests/**`, `openspec/changes/**`, `openspec/decisions/**`,
  `openspec/specs/**`, `migrations/**`, `pyproject.toml`, `poetry.lock`

Forbidden (blocked as outside_restrict_allow): the workflow runtime tree,
registry schema tooling, agent configuration trees, and any openspec path
outside the allowed planning roots listed above. Do not read registry JSON or
JSON Schema on disk to learn hop or receipt shapes — use this prompt and
embedded slice input pack only.

## Hop answer JSON (appended after this prompt — sole source for your tool response)
Keys: `status`, `tasks_summary`, `gap_closure_summary`, `files_changed`,
`remaining_gaps`, optional `arbiter_request`. `status` must be one of
`APPLY_COMPLETE`, `APPLY_PARTIAL`, `BLOCKED`, `NO_CHANGE`, `ESCALATE_ARBITER`.
Do not open any file to discover these fields — use only this block and the injected
schema section the runtime appends next.
Do not author or edit on-disk slice receipt JSON (`SCAFFOLD.json`). The runtime
records the receipt from design, git diff, and hop status after this hop completes.
"""


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None = None,
) -> str:
    """Собрать SDK prompt для ``ApplyScaffoldAgent``.

    JSON-схему ответа не вставляет: её добавляет ``ABCAgent`` из ``payload_model``.
    """
    from agents.implementation.apply_scaffold.scaffold_receipt_prompt import (
        scaffold_receipt_authority_block,
    )
    from agents.implementation.prompt.build_split_apply_prompt import build_split_apply_prompt

    return build_split_apply_prompt(
        stage_key=_STAGE_KEY,
        repository_path=repository_path,
        change_id=change_id,
        impl_verify_answer=impl_verify_answer,
        artifact_bundle=artifact_bundle,
        machine_context=machine_context,
    ) + _SCAFFOLD_TOOL_SURFACE + "\n" + scaffold_receipt_authority_block()
