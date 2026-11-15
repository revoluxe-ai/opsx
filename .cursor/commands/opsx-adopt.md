---
component_id: opsx.commands.adopt
component_version: 1.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
contract_id: OPSX-PROJECT-ENGINEERING-ADOPTION-V1
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
---

# /opsx-adopt

**Быстрая инженерная подготовка** репозитория к обычным OPSX-командам (gates,
lock, toolchain). **Не** remediation продукта, **не** починка тестов, **не**
«довести suite до зелёного кодом».

Establishes verifiable quality gates (discover → assess → policy → Owner gate →
configure → verify → ADOPTED) before ordinary mutation-capable OPSX commands run.

## Agent execution boundary (mandatory — COMMIT STOP if violated)

The agent running `/opsx-adopt` **must not** use this command to fix failing
tests or product logic. Boundaries are **hard**, not guidelines.

### Forbidden writes (never for adoption green)

| Area | Paths (non-exhaustive) | Examples of forbidden work |
|------|------------------------|----------------------------|
| Product code | `src/**`, `app/**`, domain packages | FSM, handlers, CRM, «enum drift» fixes |
| Tests | `tests/**` | `@pytest.mark.skip`, imports, asserts, deletes, new tests |
| Product tooling | `tools/**` except adoption runner wiring noted below | consultation baseline pins, openspec guards in product sense |
| Migrations / CI product | `migrations/**`, `.github/workflows/**` | workflow triggers, schema |
| OpenSpec product | `openspec/changes/**` (product packets) | spec/task fixes for failures |

**No exceptions** for «only one line», «obvious bug», or «suite will pass». That
is **`/opsx-apply`**, **`/opsx-test`**, **`/opsx-commit`** — not adoption.

### Allowed writes (narrow whitelist only)

| Class | Allowed | Notes |
|-------|---------|--------|
| Adoption CONFIGURE surfaces | `pyrightconfig.json`, `opsx/project/capabilities.yaml` (typecheck fields only), `opsx/engineering-adoption/**` | Per POLICY_PROPOSED |
| Adoption runner / gate (infra) | `.cursor/pipeline/engineering-adoption/**`, `tools/openspec/opsx_engineering_adoption*.py` | Runner crashes, timeouts reporting — **not** product |
| Toolchain bootstrap (infra) | `npm ci` / `setup.sh` under `opsx_tools/openspec-cli-node/**` only | Local CLI provider install |
| Quarantine manifest | `opsx/engineering-adoption/quarantine.json` via VERIFY `--quarantine-json` | Owner-approved deselect for active suite |

Everything else → **STOP** with `NEXT_ACTION: /opsx-commit` or product change,
not silent edits under `/opsx-adopt`.

### How to get VERIFY green without touching code/tests

1. **Pure infra** — fix only if failure is **100% infrastructure** (missing CLI
   binary after bootstrap, runner bug, wrong `pyrightconfig` path). Still **no**
   `src/**` or `tests/**`.
2. **Owner quarantine** — exclude failing `test_node_id` from **active suite**
   via approved JSON (`ADOPTED_WITH_QUARANTINE`). May quarantine **many or all**
   failing nodes when Owner accepts risk + expiry + reactivation — fast prep, not
   hidden quality.
3. **FAIL_CLOSED** — if infra cannot be fixed and Owner will not quarantine,
   stop; do **not** drift into product/test edits.

Ambiguous «test vs product» → **quarantine with Owner gate** or **FAIL_CLOSED**,
**not** test file edits, **not** product edits.

## Preconditions

1. `opsx/projectization.yaml` → `status: COMPLETE`, `normal_commands_enabled: true`
2. During CONFIGURE/VERIFY: **no** product or test file edits (see boundary above).

## Lifecycle

| Phase | Action |
|-------|--------|
| DISCOVER | Stack, CI, tests, tooling from `opsx/project/capabilities.yaml` + repo facts |
| ASSESS | Bounded checks (compileall, pytest collect, pyright probe) |
| POLICY_PROPOSED | Draft Project Quality Contract (Pyright ratchet, pytest, optional Ruff) |
| OWNER_POLICY_GATE | Owner approves toolchain, baseline, quarantine |
| CONFIGURE | Install/configure approved tools (`pyrightconfig.json`, capabilities typecheck) |
| VERIFY | Active quality gates; quarantine excluded from active suite |
| ADOPTED / ADOPTED_WITH_QUARANTINE | Writes `opsx/engineering-adoption.yaml` + contract/receipt |

Unsafe transitions → `FAIL_CLOSED` with recovery contract.

## Machine runner

```bash
python -m tools.openspec.opsx_engineering_adoption DISCOVER --root .
python -m tools.openspec.opsx_engineering_adoption ASSESS --root .
python -m tools.openspec.opsx_engineering_adoption POLICY_PROPOSED --root .
python -m tools.openspec.opsx_engineering_adoption OWNER_POLICY_GATE --owner-token OWNER_POLICY_APPROVED:<policy_revision_sha256> --root .
python -m tools.openspec.opsx_engineering_adoption CONFIGURE --root .
python -m tools.openspec.opsx_engineering_adoption VERIFY --root .
```

Or bounded chain:

```bash
# After POLICY_PROPOSED, read policy_revision_sha256 from policy-proposed.json for Owner token.
python -m tools.openspec.opsx_engineering_adoption RUN_ALL --owner-token OWNER_POLICY_APPROVED:<policy_revision_sha256> --root .
```

Node bridge:

```bash
node opsx_tools/opsx-engineering-adoption.mjs RUN_ALL --owner-token OWNER_POLICY_APPROVED
```

## Owner policy gate — quarantine (primary lever for failing tests)

Emit approved quarantine JSON when VERIFY cannot pass without excluding nodes
(environment, missing services, or Owner-accepted fast prep):

```json
{
  "records": [
    {
      "test_node_id": "tests/integration/test_slow.py::test_hangs",
      "reason": "environment_blocker",
      "evidence": "timed out at 30s during ASSESS",
      "risk": "integration_coverage_gap",
      "owner_authorization_ref": "owner-message:adopt-quarantine-001",
      "expires_at": "2026-12-31T00:00:00Z",
      "reactivation_condition": "CI postgres available"
    }
  ]
}
```

Pass to VERIFY: `--quarantine-json path/to/quarantine.json`

Quarantine **deselects** nodes from the **active adoption suite** only. It does
**not** modify test sources. Records require Owner fields (`validate_quarantine_record`).

Bulk quarantine for fast prep is allowed **only** with explicit Owner approval
and documented risk — not agent initiative to «make green».

## Output contract

STATUS: DISCOVER | ASSESS | POLICY_PROPOSED | OWNER_POLICY_GATE | CONFIGURE | VERIFY | ADOPTED | ADOPTED_WITH_QUARANTINE | FAIL_CLOSED
ACTIVE_QUALITY_GATES:
QUARANTINED_TESTS:
FULL_SUITE_VERIFICATION_STATUS:
NEXT_ACTION:

### VERIFY active gates (Python)

Mandatory at ADOPTED:

- `syntax` — `compileall`
- `pytest_active_suite` — full pytest minus approved quarantine
- `pyright_no_new_standard_errors` — standard Pyright ratchet vs baseline

**Not** adoption VERIFY (run on implementation contour instead):

- repo-wide `pyright-strict` and `mypy` (`/opsx-static-check`)
- strict-on-touch / `pyright_strict_modified_clean` → `/opsx-static-check-coverage`
  then typing on **changed** in-policy production Python only

Change contour (`pytest-targeted`, verify) still owns test/product quality;
adoption quarantine does **not** grant skip-in-repo for future changes.

## Hard bans

- **No edits** to `src/**`, `tests/**`, `migrations/**`, product `openspec/changes/**`.
- **No** `@pytest.mark.skip`, assert rewrites, test delete, or «fix test to match code» under this command.
- **No** product bugfixes disguised as adoption (FSM, enums, handlers, baseline pins in product trees).
- **No** silent quarantine without Owner JSON and required record fields.
- **No** downgrade Pyright strict policy without Owner authorization.
- **No** push / PR / merge from this command.

Violating agent boundary → report `ADOPT_BOUNDARY_VIOLATION`, revert product/test
edits, `NEXT_ACTION: Owner quarantine or /opsx-commit`.
