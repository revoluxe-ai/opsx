# Verify receipt invalidation and stage reuse (v2)

## Invariants

1. **Product input identity** is a digest of current, selected product bytes and normative change artifacts. It never includes `opsx/**`, `tests/opsx/**`, Git HEAD, a branch name or a commit SHA. Dirty/untracked product files are relevant.
2. **Implementation review has no verifier fingerprint.** Its reuse depends only on the selected input files. Changes to verifier code, prompt, model or workspace location do not invalidate `IMPL_OK`. Other review roles retain their existing contract policies below.
3. **Independent gate:** a previous checkpoint/cycle green stamp is *not* evidence authorizing any check to be skipped. Each check must validate its own checksum-verified, current-input, current-contract PASS receipt, otherwise execute or fail closed.
4. No PASS may be created from `UNKNOWN`, `BLOCKED`, a partial SDK response, a missing model/environment identity, or a failed receipt write. Reuse carries prior run provenance but is classified `REUSED_PASS` in the current execution.
5. Model/executor tier cannot silently change. Per-check contract defines whether changed model authority invalidates a receipt.
6. The scenario-coverage plan can additionally narrow product inputs to independently proven **scenario-row digests**, allowing one changed isolated test module to invalidate only the affected rows.

## Exact top-level invalidation scopes

| Check | Product inputs | Verifier inputs and selectors | Reuse |
|---|---|---|---|
| `impl_verify` | `src/**`, `migrations/**`; `openspec/changes/<change>/{proposal,design,tasks,owner-contract}.md`, `specs/**/*.md`, `decisions/**/*.md`; `pyproject.toml`, `poetry.lock`, `setup.cfg` | None: no reviewer code, prompt, model or workspace fingerprint | Semantic PASS on identical inputs; run deterministic preflight afresh |
| `project_bug` | `src/**`, repo entry points (`run_opsx_main.py`, registered `src/**` from `opsx/project/coverage_activation_mypy_paths.txt`); **static** change plans only: `proposal.md`, `design.md`, `tasks.md`, `specs/**/*.md` (no `verify/`, logs, `owner-contract`, decisions) | `agents/verify/project_bug_hunt/**/*.py` excluding tests; shared receipt/schema; prompt/model; stage comparison **content diff** on `src` + entry points, not HEAD | Clean hunt (`HUNT_COMPLETE`, 0 confirmed bugs) |
| `bugbot` | Same product scope as `project_bug` | `agents/verify/bugbot/**/*.py` excluding tests; shared receipt/schema; prompt/model; durable key `check=bugbot` (no level suffix); stage comparison content diff on `src` + entry points | Clean review |
| `security` | Same product scope as `project_bug` | `agents/verify/security_review/**/*.py` excluding tests; shared receipt/schema; prompt/model; durable key `check=security`; stage comparison content diff on `src` + entry points | Clean security review |
| `coverage_activation` | `src/**`, `migrations/**`, product `tests/**`, change planning/specs and config | `agents/verify/coverage_activation/**/*.py` excluding tests; inventory content fingerprint and model; no-Git-HEAD baseline of product files | No-op PASS only; never reuse side-effecting activation |
| `scenario_coverage` | `tests/**/*.py` excluding OPSX, `src/stroy/**/*.py`, `migrations/**/*.py`, `docs/**/*.md`, frozen scenario plan/receipt/matrix and `specs/**/*.md` | Explicit semantic sources: `evidence_contract.py`, `semantic_validation.py`, `delta_rows.py`, `row_receipts.py`, `input_pack.py`, prompt/schema plus shared receipts; model | Full verified PASS or individually supported rows |
| `quality_gates` | `src/**`, product `tests/**`, `migrations/**`, `configs/**` and explicit product Python/testing configs | Quality gate runner, diagnostics, schemas and shared receipt logic; exact commands, timeout, Python/platform/environment and explicit `quality_gate_environment_id` | Complete three-command PASS only |

Path policies are executable declarations in `agents/verify/check_reuse.py`, `scenario_coverage_verify/evidence_contract.py`, and `workflows/verify/gate_repair/runner.py`. They MUST NOT silently expand to repo-wide `git HEAD`. When a product-specific path inventory is unavailable, do not infer a PASS.

## Stage-level behavior

- `cycle_checks` uses a *routing-only* content marker, never Git HEAD. The pipeline always visits a check whose own receipt must be validated; expensive SDK work is skipped only after verified reuse.
- `coverage_activation` inventory uses an explicit comparison baseline tree versus worktree product content. Arbitrary Git operations do not change evidence identity unless relevant product bytes or baseline comparison content change.
- Existing `read_only_review_v2` implementation receipts reuse directly when their original product input digest still matches. Their retired verifier fingerprint is ignored; checksum, result schema, clean verdict and original execution provenance remain checked. New implementation receipts contain no verifier fingerprint and do not rewrite historical evidence.
- No automatic retry on provider overload or un-reconciled SDK runs. PAUSE remains terminal until recovery conditions are demonstrably satisfied.

## Acceptance examples

- Commit/rebase/checkout with identical relevant product bytes and baseline comparison content: **no repeat SDK call**.
- Edit `opsx/opsx_runtime/workflows/verify/` routing or SDK concurrency: **no product digest change**.
- Edit implementation reviewer code/prompt/model with identical product inputs: **no repeat model call**. Other checks retain their own verifier contract policy.
- Edit only `tests/opsx/**`: **no product receipt invalidation**.
- Edit an isolated scenario test: invalidate its **row receipt**; unaffected supported sibling rows remain reusable.
- Edit production `src/**`, relevant spec/decision, or attested environment: **targeted cache MISS**.
- Missing identity, corrupt receipt, unbound environment or stale proof: **MISS/PAUSE, never false PASS**.

