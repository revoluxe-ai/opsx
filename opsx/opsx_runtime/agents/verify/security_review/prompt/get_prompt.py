"""Промпт SecurityReviewAgent (семантика `/review-security` + tempo-security для stroy-system)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agents.common.prompt_embed import bounded_json_block
from agents.state import GraphState
from agents.verify.prompt_diff_pack import collect_baseline_diff_pack


def get_prompt(
    state: GraphState,
    answer_model: type[BaseModel],
    answer_lang: Literal["русский"],
) -> str:
    """Собрать READ_ONLY security review prompt для текущего change slice.

    JSON-схему ответа не вставляет: её добавляет ``ABCAgent`` из ``payload_model``.
    ``answer_model`` оставлен в сигнатуре для вызывающих и в текст не попадает.
    """
    repository_path = state["repository_path"]
    change_id = str(state.get("change_id") or "").strip() or "unknown-change"
    _ = answer_model
    repo = Path(repository_path)
    diff_pack = collect_baseline_diff_pack(repo, "security")
    diff_json = bounded_json_block(diff_pack, max_chars=100_000)

    return f"""
You are the security discovery hop for stroy-system OPSX verify.

READ_ONLY security review rules (inline — do not open `.cursor/**`):
- MODE: READ_ONLY — find security problems; do not patch product code, commit, or push.
- Launch mindset: branch diff review scoped to product code only.

STROY-SYSTEM TARGET OVERRIDE:
- This hop runs inside `opsx/opsx_runtime` verify control-plane wiring.
- Do NOT treat donor WB/adv-bot literals (`app/**`, Poetry, shop-runner) as authority.
- Product tree for this repository: `src/**` and `tests/**` only (stroy-system).
- Exclude process lanes before emitting repair-eligible rows:
  `opsx/**`, `.cursor/**`, `tools/**`, `openspec/**`, `.agents/**`, `opsx-knowledge/**`.
- Do NOT fail the whole task with TARGET_CAPABILITY_UNRESOLVED — bounded verify only.
- Do NOT implement shop-runner / ClickHouse / Telegram contract checks.

REVIEW_SCOPE: SRC_TESTS_ONLY
Base Branch: stage (compare change slice vs stage unless state says otherwise)

Full Repository Path: {repository_path}
Change id: `{change_id}`

## EMBEDDED DIFF PACK (authoritative)
```json
{diff_json}
```

METHOD:
1. Review only touched `src/**` and `tests/**` paths in touched_paths above.
2. Do not repo-wide grep; do not read `.cursor/**`, `opsx/**`, receipts.
3. Classify each row with exactly one security_classification:
   confirmed security issue | informational | process_lane | false positive
4. Mark process_lane for any finding whose location is outside `src/**` / `tests/**`
   or inside excluded process prefixes above.

FINDING BAR:
- Emit confirmed security issue only for realistic exploitable or high-risk defects
  in product code (auth, injection, secrets, unsafe deserialization, etc.).
- Informational or process-only rows must NOT count toward confirmed_security_issue_count.
- Do not run repair yourself; downstream bug-team consumes mapped findings only.

OUTPUT CONTRACT:
Return ONLY valid JSON matching the schema appended after this prompt (no markdown fences, no prose outside JSON).

Write human-readable string fields in {answer_lang}.
Keep JSON keys, enum values, and paths unchanged.
If no confirmed security issues in scope, return SECURITY_COMPLETE with empty findings and
confirmed_security_issue_count=0.
"""
