"""Configuration constants for the Temporal review/repair loop POC.

Bugbot, security, and project-bug-hunt share one worker/queue. Security repair is
`/opsx-bug-team` (not orch). Project hunt uses `/opsx-project-bug-hunt` + `/opsx-orch-bug-fix`.
"""

from __future__ import annotations

from pathlib import Path

# Authorized post-repair commit primitive (human recovery / docs baseline).
COMMIT_ALL_PROMPT = "/opsx-commit-all GO"

# Materialize hop MUST forward ``ALL`` so product OpenSpec artifacts (incl. untracked
# ``openspec/changes/<change-id>/apply-process-exception/**``) enter the product lane.
COMMIT_ALL_MATERIALIZE_PROMPT = "/opsx-commit-all ALL GO"

# Overlay for materialize hop: Temporal reads the final ``STATUS:`` line only.
COMMIT_ALL_PARSE_TRAILER = """
TEMPORAL_MATERIALIZE_CONTRACT (mandatory — last lines of your reply):
1. After any human SUMMARY, end with exactly one machine-readable line:
   STATUS: COMPLETE | COMPLETE_NOOP | PARTIAL | BLOCKED | FAIL_CLOSED | FAIL
2. If multiple STATUS lines exist, the last one wins.
3. PARENT_STATUS in markdown is not sufficient; always emit STATUS: as above.
4. ``COMPLETE`` / ``COMPLETE_NOOP`` only when product paths are materialize-clean
   (no dirty/untracked ``src/**``, ``tests/**``, ``openspec/changes/**`` per runtime gate).
   Leftover product tree → ``PARTIAL`` and list ``LEFT_PRODUCT_DIRTY_FOR_MATERIALIZE``.
"""


def commit_all_prompt_for_materialize() -> str:
    """Промпт commit-all для activity materialize (ALL scope + parse-контракт)."""

    return f"{COMMIT_ALL_MATERIALIZE_PROMPT}\n{COMMIT_ALL_PARSE_TRAILER}"

REVIEW_PROMPT = """/review-bugbot against stage — напрямую, без субагентов и без Task.

Обязательный контракт:
1. Сравни текущую ветку с ref `stage` (полный branch diff `stage...HEAD`).
2. Если в промпте есть блок `POC_GIT_PREFLIGHT: VERIFIED` — git diff уже собран POC, Shell для git не нужен.
3. Иначе Shell: ровно ОДНА команда за вызов (без &&, ;, |).
4. Не упоминай hooks / Smart Mode / blocked shell в отчёте.
5. В таблицу включай ТОЛЬКО game product-lane (`src/**`, `tests/**`, `backend/**`, `openspec/changes/**`).
   НЕ включай tooling/process:
   `tools/**` (в т.ч. `tools/temporal/**`), `.cursor/**`, `.agents/**`, `opsx/**`, `opsx-knowledge/**`, `.tmp/opsx*`
6. Не чини код. Только review.

Обязательные строки в начале ответа:
REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: <текущий ref>
DIFF_STAT_LINES: <число строк из git diff --stat>

Затем таблица:
| Severity | Location (file:line) | Finding |

Если багов нет: `Bugbot found no bugs.` и REVIEW_SCOPE как выше."""

REPAIR_PROMPT = """/opsx-orch-bug-fix

POC contract:
1. Primary is `/opsx-orch-bug-fix` (R2: immediate prior Bugbot in this chat).
2. Shell is available. 1Password "validation skipped" + allow is NOT a deny.
3. One Shell command per call (no &&, ;, |). Never `npm test` (no such script).
4. After product patch, GREEN with one command, e.g.
   `node --test tests/unit/<touched-family>.test.mjs`
5. Do not edit `tools/temporal/**` or other non-game paths.
6. `BLOCKED_WORKER_STATE` only if Shell actually denied the command or node
   failed to start. Hook folklore is not a block.
7. Do not mention hooks / Smart Mode in STATUS."""

# Cursor `/review-security` skill (not `/opsx-security-review`). POC collects a
# scoped git preflight (`src/**`, `tests/**`) outside the agent and injects
# `POC_GIT_PREFLIGHT: VERIFIED` + PREFLIGHT_ARTIFACT; the review agent must treat
# that artifact as SoT and must not fail on remote/default-base merge-base.
SECURITY_REVIEW_PATH_PREFIXES: tuple[str, ...] = ("src/", "tests/")

SECURITY_REVIEW_PROMPT = """/review-security

Follow the Cursor `/review-security` skill exactly (not `/opsx-security-review`):
1. Launch exactly one security-review subagent:
   - description: "Security Review"
   - subagent_type: "security-review"
   - run_in_background: false
2. REVIEW_SCOPE: SRC_TESTS_ONLY — only `src/**` and `tests/**` vs Base Branch: {base}.

=== WHEN THIS PROMPT CONTAINS `POC_GIT_PREFLIGHT: VERIFIED` (MANDATORY) ===
A. FIRST action: Read PREFLIGHT_ARTIFACT (path in the verified block). That file
   is the source of truth for the scoped `src/**` + `tests/**` diff vs {base}.
B. Base Branch MUST be `{base}` on every subagent launch — never omit it, never
   default to `origin/HEAD`, `master`, or any remote-tracking merge-base.
C. FORBIDDEN when preflight is verified:
   - Shell git to recompute full-repo / branch-tracking diff
   - Relying on remote tracking branches (they may be deleted)
   - Letting the subagent fail the whole review because it cannot compute
     branch-changes against the repo default base
D. Prefer reviewing from the PREFLIGHT_ARTIFACT content (src/tests only). Pass
   that contract into the subagent via Custom Instructions.
E. Subagent prompt, exact shape:
Full Repository Path: <this agent workspace root>
Diff: branch changes
Base Branch: {base}
Custom Instructions: SRC_TESTS_SCOPE — scoped src/tests diff is ALREADY collected in PREFLIGHT_ARTIFACT (parent Read it first). Treat that artifact as source of truth for changed files under `src/**` and `tests/**` vs Base Branch `{base}`. Do NOT recompute merge-base against origin/HEAD, master, or deleted remotes. Do NOT fail only because local branch-diff computation against default tracking failed — use the artifact file list/diff instead. Exclude `backend/**`, `openspec/**`, `tools/**`, `.cursor/**`, `.agents/**`, `opsx/**`, `opsx-knowledge/**`, `.tmp/opsx*`. Do not fix code.
F. FALLBACK (required, not optional): If the subagent still cannot compute the
   branch-changes diff after one skill retry with the same prompt (Base Branch:
   {base} + Custom Instructions above), do NOT stop with skill blocker /
   "could not compute the branch-changes diff". Re-read PREFLIGHT_ARTIFACT and
   summarize security findings from that scoped src/tests content yourself, then
   emit one of the required summary phrases/table below. Emitting only the
   subagent blocker text is FORBIDDEN when `POC_GIT_PREFLIGHT: VERIFIED`.

=== WHEN `POC_GIT_PREFLIGHT: VERIFIED` IS ABSENT ===
Do not compute the git diff yourself before launching the subagent. Still set
Base Branch: {base} (known specific base, not origin/HEAD / master). Subagent
prompt shape as in E, but Custom Instructions:
SRC_TESTS_SCOPE only — review changed files under `src/**` and `tests/**` between merge-base({base}, HEAD) and working tree. Path-scoped diff only (`git diff $(git merge-base {base} HEAD) -- src tests`). Exclude `backend/**`, `openspec/**`, `tools/**`, `.cursor/**`, `.agents/**`, `opsx/**`, `opsx-knowledge/**`, `.tmp/opsx*`. Do not fix code.
If the subagent fails before findings: skill retry once; if it still fails, stop
with the skill blocker text.

After the subagent finishes — or after FALLBACK when preflight is verified —
summarize (do not fix code):
- no diff: `there was no diff to review`
- no issues: `Security review found no issues`
- issues: `Security review found N findings` plus table:
| Severity | Location (file:line) | Finding |"""

# Injected by loop_engine when verified preflight + subagent branch-diff blocker only.
SECURITY_REVIEW_ARTIFACT_FALLBACK_PROMPT = """/review-security — ARTIFACT-ONLY FALLBACK (mandatory)

POC_GIT_PREFLIGHT: VERIFIED. The security-review subagent could not compute
branch-changes diff. Do NOT launch the subagent again. Do NOT use Shell git.

1. Read PREFLIGHT_ARTIFACT at `{artifact_path}` — sole source of truth for
   REVIEW_SCOPE: SRC_TESTS_ONLY vs Base Branch: {base}.
2. Review security of changed `src/**` and `tests/**` content from that artifact.
3. Emit exactly one required summary:
   - `there was no diff to review`
   - `Security review found no issues`
   - `Security review found N findings` plus table:
| Severity | Location (file:line) | Finding |

FORBIDDEN: blocker-only text; "could not compute the branch-changes diff";
stopping without one of the summary phrases above."""

SECURITY_REPAIR_PROMPT = """/opsx-bug-team

POC contract:
1. Primary is `/opsx-bug-team` (R2: immediate prior `/review-security` table in this chat).
   Intake only `src/**` and `tests/**` rows from the prior table.
   Do not include `opsx/**` / process rows in the signal.
2. Do not run `/opsx-orch-bug-fix`, `/opsx-security-fix`, or `/opsx-security-review`.
3. Do not edit `.cursor/**`, `opsx/**`, or `tools/**`.
4. TARGET_LITE: do not HANDOFF to `/opsx-security-triage` for Bot/High-Severity label alone;
   bug-team routes threat substance internally.
5. Do not commit or push.
6. Shell is available. 1Password "validation skipped" + allow is NOT a deny.
7. One Shell command per call (no &&, ;, |). Never `npm test` (no such script).
8. After product patch, GREEN with one command when applicable, e.g.
   `node --test tests/unit/<touched-family>.test.mjs`
9. `BLOCKED_WORKER_STATE` only if Shell actually denied the command or node
   failed to start. Hook folklore is not a block.
10. Do not mention hooks / Smart Mode in STATUS.

Terminal lines (last STATUS wins):
STATUS: SECURITY_BATCH_CLEAR
SECURITY_BATCH_CLEAR: YES
or STATUS: BLOCKED_OWNER_DECISION / BLOCKED_WORKER_STATE / PARTIAL_COMPLETE"""

# Project bug hunt → orch cycle. Tokens base/head/change are CLI params.
# Hunt owns its snapshot/diff; do not inject bugbot git preflight.
PROJECT_BUG_HUNT_REVIEW_PROMPT = """/opsx-project-bug-hunt base={base} head={head} change={change}

POC contract:
1. Primary is `/opsx-project-bug-hunt` with the tokens above (exact).
2. Shell is available. One Shell command per call (no &&, ;, |).
3. Do not fix product code in this hop. Hunt only.
4. Emit the command output contract: STATUS, PROJECT_BUG_HUNT_RECEIPT
   (including findings_total), FINDINGS blocks with LOC_LINK, then handoff.
5. Product-lane findings only (`src/**`, `tests/**`, `backend/**`,
   `openspec/changes/**`). Exclude `tools/**`, `.cursor/**`, `opsx/**`.
6. Do not mention hooks / Smart Mode in STATUS."""

PROJECT_BUG_HUNT_REPAIR_PROMPT = """/opsx-orch-bug-fix change={change}

POC contract:
1. Primary is `/opsx-orch-bug-fix` (R2: immediate prior `/opsx-project-bug-hunt`
   FINDINGS in this chat). Pass change={change}.
2. Shell is available. 1Password "validation skipped" + allow is NOT a deny.
3. One Shell command per call (no &&, ;, |). Never `npm test` (no such script).
4. After product patch, GREEN with one command, e.g.
   `node --test tests/unit/<touched-family>.test.mjs`
5. Do not edit `tools/temporal/**` or other non-game paths.
6. Emit REPAIRED_THIS_BATCH: YES|NO and BUG_BATCH_CLEAR / terminal STATUS.
   REPAIRED_THIS_BATCH=YES only when FIX_NOW repaired at least one item.
   If FIX_NOW is empty / (none) / only DEFER — REPAIRED_THIS_BATCH=NO
   (even when BUG_BATCH_CLEAR=YES). POC stops the loop on NO.
7. Do not mention hooks / Smart Mode in STATUS."""

# Defaults for project-bug-hunt lane CLI / mock scenarios.
PROJECT_BUG_HUNT_DEFAULT_BASE = "stage"
PROJECT_BUG_HUNT_DEFAULT_HEAD = "save-game-progress"
PROJECT_BUG_HUNT_DEFAULT_CHANGE = "cross-device-novel-save-refresh"

REVIEW_BASE_REF = "stage"
GIT_PREFLIGHT_ARTIFACT_REL = ".tmp/temporal/git-preflight-latest.md"
GIT_PREFLIGHT_MAX_FILES_WITH_DIFF = 25
GIT_PREFLIGHT_MAX_FILE_DIFF_CHARS = 8_000
GIT_PREFLIGHT_MAX_STAT_CHARS = 120_000
# Windows CreateProcess argv limit is ~8191 chars; keep CLI prompt well below that.
MAX_REVIEW_PROMPT_CHARS = 6_000

# The repair budget counts repair activities only. The review budget is one
# larger so the result of the tenth repair always receives a mandatory final
# read-only review before the workflow can terminate.
MAX_REPAIR_ROUNDS = 10
MAX_REVIEW_RUNS = MAX_REPAIR_ROUNDS + 1

# Technical retry for read-only Bugbot invocations only.
REVIEW_TECHNICAL_RETRY_MAX = 2

# Temporal task queue for the isolated POC worker.
TEMPORAL_TASK_QUEUE = "cursor-bugbot-repair-poc"

# Default Temporal server address for local dev.
TEMPORAL_ADDRESS = "localhost:7233"
TEMPORAL_UI_URL = "http://localhost:8233"

_TEMPORAL_ROOT = Path(__file__).resolve().parent


def apply_cursor_sdk_bridge_temp_dir() -> Path:
    """Перенаправить TEMP/TMP/TMPDIR для Node cursor-sdk-bridge.

    На Windows фоновый worker (subprocess без интерактивной сессии) иногда
    получает EPERM на ``mkdtemp`` в системном ``C:\\Temp``. Локальный каталог
    под ``tools/temporal/.tmp`` всегда writable для runtime POC.
    """

    import os

    bridge_tmp = _TEMPORAL_ROOT / ".tmp" / "cursor-sdk-bridge"
    bridge_tmp.mkdir(parents=True, exist_ok=True)
    resolved = str(bridge_tmp.resolve())
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = resolved
    return bridge_tmp
