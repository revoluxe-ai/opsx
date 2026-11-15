# INCREMENTAL QUALITY COVERAGE V1

Contract id: `OPSX-INCREMENTAL-QUALITY-COVERAGE-V1`

## Purpose

STROY product typing quality is rolled out incrementally: broad **configured**
pyright strict scope (`pyrightconfig_strict.json` with `include` + `ignore`) is
not the same as **verified** zero-error strict scope (`pyrightconfig_strict_incremental.json`).
This contract defines machine states, scopes, monotonic expansion, and consumer
bindings without activating migration on product branches until Owner sets
`activation: ACTIVE` on the manifest.

## Semantic layers (authority)

| Layer | STROY authority | adv-bot donor (non-authority) |
|-------|-----------------|------------------------------|
| SYSTEM_SEMANTICS | Diff ⊆ strict include; additive GO; separate from typecheck run | Same coverage audit semantics |
| REALIZATION | `src/stroy`, capabilities.yaml, STRICT_ON_TOUCH baseline | `app/**`, WB paths — historical only |
| USER_CONTRACT | `/opsx-pyright-strict-coverage`, Wave A `pyright-strict`, IQC manifest | shop-wide-common-sales-collection evidence patterns only |

## File state machine

| State | Meaning |
|-------|---------|
| `LEGACY_UNCOVERED` | In CONFIGURED_SCOPE but not in VERIFIED_SCOPE (often still in `ignore`) |
| `STRICT_COVERED` | Listed in VERIFIED_SCOPE manifest/config |
| `COVERAGE_EXPANSION` | REQUIRED_CHANGE_SCOPE needs additive verified include (monotonic GO path) |

Transitions are monotonic: verified include may only grow; shrink or mode relax → `BLOCKED`.

## Scopes

| Scope | Definition |
|-------|------------|
| `CONFIGURED_SCOPE` | All in-policy `.py` under product roots covered by configured strict config minus `ignore` |
| `VERIFIED_SCOPE` | Explicit paths from verified config (`pyrightconfig_strict_incremental.json` until manifest ACTIVE) |
| `CANDIDATE_VERIFIED` | Listed in verified config — **not** evidence until pyright PASS bound to revision/config/toolchain |
| `EVIDENCE_VERIFIED` | Candidate scope with machine pyright evidence (`verified_scope_evidence` in report) |

ACTIVE enforcement: `STATUS=PASS` requires executed pyright (`exit_code=0`) and
`verified_scope_layer=EVIDENCE_VERIFIED`. Evidence binding includes `git_head`,
`verified_path_content_hashes`, configured/verified config fingerprints,
`pyright_toolchain_fingerprint`, and per-run `execution_id`.
`OPSX_IQC_SKIP_PYRIGHT_EVIDENCE=1` is test-only; ACTIVE never PASS under skip.
Pyright unavailable → `BLOCKED` / `PYRIGHT_UNAVAILABLE` (no fake PASS).
| `REQUIRED_CHANGE_SCOPE` | In-policy changed `.py` paths for a change/worktree that must be ⊆ VERIFIED or trigger `COVERAGE_EXPANSION` |

Broad `include: [src/stroy]` ≠ verified: IQC reports the gap explicitly.

## Manifest

- Proposed bootstrap: `opsx/incremental-quality-coverage/manifest.proposed.yaml`
- Schema: `opsx/schemas/incremental-quality-coverage-manifest-v1.schema.json`
- `activation: PROPOSED` — audit and gates report only; no enforce shrink/expand on product branches.

## Consumers (V1)

| Consumer | Entry |
|----------|-------|
| Quality audit | `engineering_adoption.run_verify` → `incremental_quality_coverage` block |
| Pyright coverage | `bounded_verify_s5_pyright_coverage` enrichment |
| IQC adapter | `bounded_verify_s5_adapters` obligation `incremental-quality-coverage` |
| Static-check preflight | `OPSX_IQC_AUDIT=1` on pyright-strict pack |
| Split-APPLY | `iqc_split_apply.preflight_for_change` |
| Bounded-verify snapshot | manifest in validator digests → policy revision drift |
| Gate CLI | `python -m tools.openspec.incremental_quality_coverage_gate` |
| Temporal | `tools/temporal/incremental_quality_policy.py` |

## Frozen plan supersession

When IQC manifest `revision` or verified config fingerprint changes, v2 snapshot
`validators` role digest moves → `implementation_snapshot` drift → bounded-verify
supersession (same path as other validator policy revisions).

## Evidence contract

`INCREMENTAL_QUALITY_COVERAGE_REPORT`
