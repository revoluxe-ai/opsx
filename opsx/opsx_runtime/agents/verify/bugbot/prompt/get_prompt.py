from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agents.common.prompt_embed import bounded_json_block
from agents.verify.prompt_diff_pack import collect_baseline_diff_pack
from ....state import GraphState


def get_prompt(state: GraphState,
              AnswerModel: type[BaseModel],
              answer_lang: Literal["русский"] ) -> str:
    """Собрать prompt BugBot.

    ``AnswerModel`` в текст не попадает. JSON-схему ответа добавляет ``ABCAgent``.
    """
    _ = AnswerModel
    ABSOLUTE_REPOSITORY_PATH = state["repository_path"]
    change_id = str(state.get("change_id") or "").strip()
    repo = Path(ABSOLUTE_REPOSITORY_PATH)
    diff_pack = collect_baseline_diff_pack(repo, "bugbot")
    diff_json = bounded_json_block(diff_pack, max_chars=120_000)

    prompt = f"""

    Full Repository Path: {ABSOLUTE_REPOSITORY_PATH}
Change id: {change_id or "n/a"}
Diff: embedded below (baseline {diff_pack.get("baseline", "stage")})
Base Branch: stage

## EMBEDDED DIFF PACK (authoritative — do not run git or repo-wide grep)
```json
{diff_json}
```

Use read/grep/glob/ls only on paths listed in touched_paths (src/**, tests/**, openspec/**).
No repo root scan. Do not use shell. Do not read opsx/** or run_opsx_main.py.

Custom Instructions:

Perform a deep, read-only bug review of the current branch against the
merge-base with `stage`.

The branch diff is the starting point, NOT the boundary of inspection.

Your goal is to find concrete bugs introduced, exposed, or made reachable by
the branch changes across the production project.

Do not modify files.
Do not fix findings.
Do not perform style review, cleanup review, naming review, or speculative
architecture redesign.

REVIEW METHOD

1. Read the complete branch diff against `stage`.

2. For every behaviorally meaningful changed area, determine what changed in:
   - control flow;
   - state transitions;
   - reads / writes / mutations;
   - persistence;
   - external API calls;
   - validation and authorization;
   - retries and recovery;
   - async or concurrent execution;
   - serialization / parsing / DTO boundaries;
   - user-visible behavior.

3. Follow changed behavior beyond the changed lines whenever necessary.

   Inspect relevant:
   - callers;
   - callees;
   - sibling implementations of the same responsibility;
   - adapters;
   - handlers;
   - workers and schedulers;
   - persistence layers;
   - external boundaries;
   - tests;
   - specifications, design documents, and accepted decisions when they
     define intended behavior.

   Do not limit review to files present in the diff when changed behavior can
   affect unchanged production code elsewhere.

4. For every candidate defect, establish a causal path:

   branch change
   → reachable production path
   → defective logic/state/boundary
   → incorrect observable result

   Do not report a finding merely because code looks suspicious.

5. Specifically inspect for these defect classes:

CORRECTNESS
- wrong conditions or inverted predicates;
- missing branches;
- incorrect defaults;
- incorrect state transitions;
- stale or invalid state being accepted;
- boundary-value or off-by-one errors;
- incorrect field/data mapping;
- semantic mismatch between caller and callee.

STATE / PERSISTENCE
- state mutation not durably persisted;
- read-after-write inconsistencies;
- partial writes;
- inconsistent state across stores;
- stale reads;
- lost state after restart;
- a failed or no-op write reported as success.

IDEMPOTENCY / RETRY / CONCURRENCY
- duplicate side effects;
- retries causing repeated external writes;
- missing deduplication;
- race conditions;
- unsafe replay;
- retry after an ambiguous external outcome;
- recovery that corrupts or duplicates state;
- non-convergent retry behavior.

ERROR HANDLING
- swallowed errors;
- incorrect fallback;
- fail-open behavior where failure should stop execution;
- partial execution treated as successful;
- missing recovery or cleanup after failure;
- incorrect error-to-state translation.

BOUNDARIES / ADAPTERS
- incorrect translation between internal and external contracts;
- wrong request or response shapes;
- enum/type/optionality mismatches;
- sync/async semantic mismatch;
- incompatible exception behavior;
- external platform details leaking into shared business logic;
- caller assumptions not guaranteed by the adapter/provider.

BACKWARD COMPATIBILITY
- existing callers broken by changed signatures or semantics;
- unchanged channels, modes, adapters, or flows affected by shared-code changes;
- sibling production paths now violating an invariant;
- behavior that worked before `stage...HEAD` becoming invalid.

TEST QUALITY
- changed behavior without meaningful regression coverage;
- a test mocking away the causal boundary it claims to prove;
- assertions unable to detect the actual failure;
- tests validating implementation details instead of observable behavior;
- impossible stub/fake behavior producing false-green results.

6. When relevant repository specifications, design documents, accepted
   decisions, or behavioral contracts exist, use them to determine expected
   behavior.

   Treat those sources as evidence of intent, not proof that implementation
   is correct.

7. When the diff changes a shared abstraction, inspect its production
   consumers and sibling paths for regressions.

   Examples:
   - shared state machine;
   - adapter interface;
   - persistence helper;
   - common service;
   - retry/recovery primitive;
   - validation or authorization gate.

   Keep expansion causally connected to the branch changes. Do not turn the
   review into an unrelated whole-repository audit.

FINDING BAR

Emit a finding only when you can determine:

- exact code location;
- realistic triggering condition;
- reachable runtime path;
- causal defect;
- incorrect observable consequence;
- why the current branch introduced, exposed, or made the defect reachable.

Before accepting each candidate finding, verify:

1. Is it reachable in production?
2. Does the current branch cause or expose it?
3. Is the resulting behavior actually incorrect?
4. Is it distinct from another finding?
5. Is there a realistic trigger?
6. Is there enough evidence to make the claim without speculation?

If evidence is insufficient, investigate further or omit the finding.

DO NOT REPORT

- style preferences;
- naming preferences;
- optional refactors;
- generic hardening suggestions;
- speculative architecture improvements;
- hypothetical failures without a reachable path;
- dead code unless the branch makes it reachable;
- missing comments or documentation;
- code that is merely ugly or complex;
- duplicate symptoms of the same root defect.

SEVERITY

Use severity based on concrete impact:

- critical:
  catastrophic security issue, data loss/corruption, or system-wide
  production failure.

- high:
  major reachable production behavior is broken, or a defect can cause
  serious corruption, repeated side effects, or large-scale failure.

- medium:
  reachable incorrect behavior with meaningful user or business impact.

- low:
  real but bounded production defect with limited impact.

Do not inflate severity.

OUTPUT CONTRACT

Return ONLY valid JSON.

No markdown.
No prose before the JSON.
No prose after the JSON.
No code fences.
No comments.
No trailing commas.
Do not invent values required by the schema.

The JSON MUST conform exactly to the schema appended after this prompt.

Write human-readable descriptions in {answer_lang}.
Keep JSON keys, schema enum values, identifiers, and paths unchanged.


Output rules:

- If there is no diff, represent that using the schema without inventing findings.
- If the review completes and no concrete bugs are found, return an empty findings collection and the schema's clean/success state.
- Every finding must refer to one root defect only.
- `location` must identify the most relevant concrete `file:line`.
- Finding text must describe:
  trigger → causal defect → observable consequence.
- Do not include proposed fixes unless the supplied schema explicitly requires them.
- Do not add fields that are not defined by the supplied JSON shape.
- Preserve valid JSON even when a review is incomplete or blocked; represent
  that state using the supplied schema.



    """

    return prompt

    