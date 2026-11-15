# Architecture & dependency evidence (V1)

```text
CONTRACT_ID: ARCHITECTURE_DEPENDENCY_EVIDENCE_V1
SCHEMA: opsx/registry/architecture_evidence.schema.json
VALIDATOR: .cursor/pipeline/architecture-evidence/architecture_evidence.py
```

## Purpose

Machine-checkable **evidence** for library/SDK pins, external API operations, and
implementation-critical architecture invariants declared in frozen `design.md`.
The receipt is **not** a second design authority.

## Authority

| Role | Owner |
|------|--------|
| Architecture SoT | `design.md` per Change |
| Evidence receipt | `architecture-evidence/ARCH-EVIDENCE.json` |
| Independent verifier | Validator tool or separate agent id ≠ `design_writer_id` |
| Structural vs technical proof | Validator proves **evidence structure**, source digests, and **fixture-bound** contract parity — not live provider correctness |

## Applicability

```text
REQUIRED          — material new/changed library, external API, or critical arch surface
NOT_APPLICABLE    — internal-only; documented scope_rationale; no required claims
```

## Three verdict dimensions (per claim and rolled up)

1. **source_validity** — source files exist; `content_sha256` matches bytes; revision pinned when required.
2. **claim_compatibility** — claim fields match authoritative `fixture_snapshot` or `lockfile_pin` evidence (not MCP/search alone).
3. **freshness** — `design_digest` and `inputs_digest` match current planning bytes.

`VERSION_CONFIRMED` / API PASS MUST NOT rely on `mcp_discovery` as sole source.

## Coverage obligations (independent of receipt author)

Design MUST declare obligations in durable blocks:

```text
ARCHITECTURE_COVERAGE_OBLIGATIONS
- obligation_id: <id>
  category: LIBRARY | EXTERNAL_API | ARCHITECTURE_INVARIANT
  design_anchor: <section or quote>

ARCHITECTURE_INTEGRATION_INVENTORY
- operation_id: <id>
  category: LIBRARY | EXTERNAL_API | ARCHITECTURE_INVARIANT
  design_anchor: <section or quote>
```

The validator merges both blocks (dedup by `obligation_id` / `operation_id`) and
requires a matching `claims[]` entry for each obligation when
`applicability: REQUIRED`. When `TECHNOLOGY_ASSESSMENT` contains `REQUIRED: yes`
but neither block lists obligations → `COVERAGE_MANIFEST_MISSING`. Silent per-id
omission → `API_CONTRACT_EVIDENCE_MISSING` or `LIBRARY_VERSION_UNCONFIRMED`.

Receipt MAY duplicate obligations in `coverage_obligations[]` for audit; entries
not present in design → `COVERAGE_MANIFEST_MISSING`. Design blocks are
authoritative for **what must be covered**.

### Bounded code discovery (S2)

Design MAY declare scoped code paths:

```text
ARCHITECTURE_AFFECTED_SURFACE
affected_path: <repo-relative file or directory>
```

S2 discovery scans those paths (cap 200 `.py` files) for imports/call-site
signals, in addition to Change markdown + `pyproject.toml` deps. Unresolved
surface when `TECHNOLOGY_ASSESSMENT REQUIRED:yes` → `DISCOVERY_SURFACE_UNRESOLVED`.

### Integrated documentation (S2)

Planning gates always use an integrated document fetcher: HTTP
`authoritative_doc_digest` claims on PASS require acquisition or an offline
repo snapshot with matching digest (`DOC_ACQUISITION_UNAVAILABLE` when neither).
Live MCP is not assumed in S2; S3 may add trusted acquisition.

### Agent documentation capture (S2)

MCP/Context7 acquisition runs in the **Cursor agent layer**, not inside the
Python gate subprocess.

1. Agent obtains read-only documentation (Context7, official export, etc.).
2. Record bounded excerpt via:

```bash
python .cursor/pipeline/architecture-evidence/doc_capture.py record \
  --change-root openspec/changes/<change-id> \
  --capture-id <id> --change-id <change-id> \
  --inputs-digest <sha256> --source-uri <uri> --publisher <name> \
  --documented-version <ver> --acquisition-tool context7-mcp \
  --acquisition-occurred true --claim-id <claim_id> \
  --content-file /path/to/excerpt.md
```

3. Files land under `architecture-evidence/captured-docs/<id>.md` +
   `<id>.meta.json` (schema: `opsx/registry/architecture_evidence_capture.schema.json`).
4. Receipt `sources[]` use `kind: captured_doc` with `capture_id` + `content_sha256`.
5. Gate validates bytes, safe paths, identity (`change_id`, `inputs_digest`),
   claim binding, and version. `manual_unattested` captures remain
   `PROVENANCE_NOT_TRUSTED_S2` (integrity ≠ trusted authority).

### Trusted provenance and independent review (S3)

S2 agent attestation (`acquisition_occurred`, `capture_tool`, receipt
`verifier.id`) is **not** trusted authority. S3 requires offline, hash-bound:

1. **Tool execution receipt** per `captured_doc`:
   `architecture-evidence/trusted-provenance/tool-executions/<capture_id>.tool-exec.json`
   (schema: `opsx/registry/architecture_evidence_tool_execution.schema.json`),
   bound to capture bytes and an allowed `execution_proof_fixture` under the repo.
2. **Independent review attestation**:
   `architecture-evidence/independent-review/REVIEW-ATTESTATION.json`
   (schema: `opsx/registry/architecture_evidence_review_attestation.schema.json`),
   bound to `ARCH-EVIDENCE.json` digest, planning digests, and per-claim
   `verified_facts` (library/API version, content markers).

Record via:

```bash
python .cursor/pipeline/architecture-evidence/trust_provenance.py record-tool-execution ...
python .cursor/pipeline/architecture-evidence/trust_provenance.py record-review-attestation ...
```

**Strict gates** (`planning_gate.py planning-review`, `apply-preflight`) enforce
S3 trust in **production** policy: test fixtures under
`.cursor/pipeline/architecture-evidence-tests/fixtures/` are **not** trusted;
`captured_doc` PASS claims and test-bound review attestations yield
`TRUSTED_PROVENANCE_UNAVAILABLE_S3` (fail closed). Isolated pytest uses
`trust_policy=test_fixture` only inside `architecture-evidence-tests/`.
`ready_for_apply_preflight` triggers production S3 in the validator CLI.
`openspec_guard.py` scenarios `opsx_planning_review` and `opsx_apply` use
production policy when `--change` is set.

### Custodian trust root (S4A)

Production material trust requires custodian JSON under
`opsx/trusted-provenance/acquisitions/` and `opsx/trusted-provenance/reviews/`
issued only by **pinned GitHub Actions workflows** defined in
`trusted_policy_v1.py` (compiled into the verifier — not PR-supplied policy).

- `cursor_bc_id` is metadata only.
- Change-local capture/review files are non-authoritative.
- CI acquisition receipts do **not** substitute independent compatibility review.
- `NOT_APPLICABLE` is independently checked (`FALSE_NOT_APPLICABLE_REJECTED`).
- **S4A:** production trust remains **blocked** until S4B enables live custodian
  workflows; verifier + schemas + CI regression ship in S4A.

### Automatic discovery limits (V1)

The validator does **not** scrape prose, OpenAPI URLs, or dependency graphs to
infer obligations. Undeclared material APIs/libraries in narrative-only form can
still pass if `TECHNOLOGY_ASSESSMENT` is `REQUIRED: no` and obligation blocks are
empty — that is an authoring gap, not a machine proof. Live provider correctness
is out of scope; only digest-bound fixtures / lockfile bytes are checked.

## Execution identity

Receipt MUST include:

```text
execution_identity.validator_implementation_id
execution_identity.schema_version
```

Mismatch with running validator → `STALE_ARCHITECTURE_EVIDENCE` (tool upgrade).

## Failure codes

```text
API_CONTRACT_MISMATCH
API_CONTRACT_EVIDENCE_MISSING
LIBRARY_VERSION_UNCONFIRMED
LIBRARY_API_INCOMPATIBLE
ARCHITECTURE_INVARIANT_VIOLATION
STALE_ARCHITECTURE_EVIDENCE
SOURCE_PROVENANCE_UNTRUSTED_S3
TOOL_EXECUTION_RECEIPT_INVALID
INDEPENDENT_REVIEW_AUTHORITY_MISSING
INDEPENDENT_REVIEW_AUTHORITY_REJECTED
CLAIM_COMPATIBILITY_UNVERIFIED
APPLY_PREFLIGHT_BLOCKED
CHANGE_ID_REQUIRED
SELF_ATTESTATION_REJECTED
UNKNOWN_IDEMPOTENCY_ASSERTED_AS_PROVEN
UNSAFE_RETRY_WITHOUT_POLICY
SOURCE_DIGEST_MISMATCH
SCHEMA_VIOLATION
COVERAGE_MANIFEST_MISSING
```

## JSON Schema enforcement

Every `validate` run loads `opsx/registry/architecture_evidence.schema.json`
(`schema_version: 1`) and rejects structural violations as `SCHEMA_VIOLATION`
before semantic checks.

## Independent verifier

`verifier.kind: INDEPENDENT_TOOL` MUST use
`id: architecture-evidence-validator-v1` (the running validator). Other tool ids
→ `SELF_ATTESTATION_REJECTED`. `summary.ready_for_design_freeze` /
`ready_for_tasks` / `ready_for_apply_preflight` MUST be false when
`verifier.id` equals `design_writer_id` or when any validation finding exists.

## Design / task / review integration (S2+)

S0/S1 deliver contract + validator only. Hard hooks in `/opsx-continue`,
`/opsx-apply`, etc. are **S2–S4** and MUST NOT be inferred from validator PASS alone.

When wired (later slices):

- `DESIGN_GENERATION.md` §0 — receipt required when technology assessment required
- `TASK_GENERATION.md` — `ARCH_EVIDENCE_FRESHNESS: current | stale | not_required`
- `PLANNING_REVIEW.md` — consume receipt; do not rebuild coverage matrix

## NOT_APPLICABLE

`applicability: NOT_APPLICABLE` with `scope_rationale` and zero required obligations
MUST pass without external research claims.
