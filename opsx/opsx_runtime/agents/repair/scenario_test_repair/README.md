# ScenarioTestRepairAgent V0 — standalone contract

## Purpose

Repair **test-only** gaps identified by `ScenarioCoverageVerifyAgent`, not
implementation defects. This role is a candidate producer; only an independent
fresh Scenario Coverage audit may close a gap.

**Connected to the Verify graph** on ``SCENARIO_TEST_COVERAGE_FAIL``: check
(``scenario_coverage_verify``) → ``scenario_test_repair`` → shared
``verify_apply_commit`` → ``scenario_coverage_verify`` re-audit. Opt-in live /
pinned runs still require ``isolated_workspace=True`` (no graph hop).

## Inputs — identical source assembly to current Apply

The agent consumes the same source values as the existing Apply hop:

- `state["answer"]` — current ImplVerify answer;
- `state["scenario_coverage_answer"]` — complete failure ledger;
- `gaps_for_prompt` — that same ledger, without selection/relabeling;
- `load_change_artifact_bundle(change_id, repo_root=...)`;
- `collect_apply_machine_context(change_id, repo_root=...)`;
- authoritative `.cursor/commands/opsx-apply.md`, as contextual constraints.

The repository can be passed explicitly through
`ScenarioTestRepairAgent(adapter).run(state, repository_path=repo_path)`.
An override cannot use an audit from a different repository.

## Preconditions

- Explicit `scenario_test_repair_authorized=True`;
- For **graph hop**: `scenario_test_repair_graph_hop=True` and
  `verify_scenario_repair_audit_run_uuid` equal to current `run_uuid` (in-repo
  verify execution; no disposable clone required);
- For **live/pinned** callers: `isolated_workspace=True` and
  `scenario_coverage_execution_id` equal to `run_uuid`;
- exact `plan_evidence_id` and content-bound `source_digest` of current files;
- explicit `model: {"id": "..."}` from the active execution (no silent tier change);
- `SCENARIO_TEST_COVERAGE_FAIL` with nonempty unique gap identifiers;
- underlying SDK adapter must support the same mutating tool API as Apply.

## Outcomes

The model response is parsed with `ScenarioTestRepairAnswer` (Pydantic
`extra="forbid"`) through the SDK adapter and explicitly validated again.
Gap partition and actual workspace changes are then checked deterministically.

- `CANDIDATE_PENDING_VERIFY`: all input gaps addressed with test changes,
  **not** `SCENARIO_TEST_COVERAGE_PASS` and not a machine-verified test result.
- `PARTIAL`: some gaps unresolved with an explicit recovery contract.
- `BLOCKED`: stale input, unauthorised/restricted write, model error,
  invalid output, unknown SDK completion, inconsistent receipts.
- `NO_CHANGE`: no changes. Not PASS.

## Safety limit

Git tracked/untracked hashing **detects** surviving out-of-scope changes after
an SDK hop; it does not prevent writes at the OS boundary, detect reverted
transient writes, or guarantee that excluded/ignored paths are unchanged.
The boolean isolated-workspace attestation is not independent isolation proof.

**Do not run a live mutating model until the authorized execution contour has
proved OS/SDK write isolation, read-only frozen artifacts, and fresh input
evidence.** Historical `run_dialogs` files are used by offline tests as
diagnostic input, never as authority to mutate a new revision.

## Tests

Run from the repository root:

```sh
poetry run python -m pytest \
  opsx/opsx_runtime/agents/tests/repair/scenario_test_repair -q
```

Offline tests use the exact recorded failure answer for
`external-client-notifications-pilot` and the existing ImplVerify receipt.
Their fake SDK responses and simulated file deltas test control-plane
contracts without changing the repository.

OPSX Python tests live separately under `tests/opsx/`, carry the
`opsx` marker, and are **not collected** by normal product `pytest`
or `pytest -m unit`. Use `pytest -m opsx` for OPSX-only checks.
That selection skips the live model test.

### Live-agent tests: explicit replay ONLY

The opt-in `tests/opsx/test_opsx_scenario_repair_live.py` entrypoint is marked
`live_agent`. Ordinary pytest never invokes the SDK. Explicitly requesting:

```bash
pytest -m live_agent
```

**does invoke a real Cursor model** and can incur costs. The command
now prints `[OPSX-LIVE ...] STAGE` messages directly to the terminal under
pytest capture (no `-s` required), including checkout/context/agent/SDK
entry and cleanup. While waiting for the model, a heartbeat every 20 seconds
reports elapsed time and stream-log byte size. Heartbeats are liveness
observations, not proof that the live adapter completed or that any gaps were fixed.

**After Ctrl+C during an SDK call, the remote run state is UNKNOWN.**
Do not blindly restart a mutating live test; inspect the previous external
`scenario-repair-replay-*.json` and `*.ndjson` receipts first. An interrupted
pytest item is not a failed model-quality verdict.

The test runs a
**historical replay** of the real ScenarioCoverage FAIL from run folder
`56736d90f28c` for `external-client-notifications-pilot`:
original gap ledger, actual ImplVerify receipt, real Apply model ID from
`run.log` and plan/spec/trace-matrix files from the checkout.

The source repository is the pytest root. The complete 40-character
`git rev-parse HEAD` SHA is recorded at startup. To replay on a specific
historic commit instead, set `OPSX_SCENARIO_REPLAY_SHA=<full SHA>` (the
commit must be accessible in the local repo).

`create_pinned_checkout()` clones a fresh disposable Git repository at the
exact SHA, without copying dirty/untracked source edits. The model operates
only with that checkout as its SDK workspace. On completion or exception,
the helper cleans **only** its own `TemporaryDirectory`. Replay artifacts
persist in the source repository at
`opsx/opsx_runtime/agents/tests/agents_outputs/`: JSON report, full raw
SDK final answer (`*.assistant.txt` with SHA-256), full conversation
(`*.conversation.json`), stream NDJSON, and full test diff. The test fails
closed if the final SDK answer/conversation recording is missing. Generated
recordings are gitignored to avoid accidentally committing potentially
sensitive raw transcripts; review and explicitly promote selected immutable
fixtures before using them in committed no-live regression tests.

`ScenarioTestRepairAgent` receives only read/grep/glob/ls/edit (plus the
existing Windows edit aliases), with **no SDK shell permission**. The
separate test runner, not the model, owns pytest execution and its evidence.

**Limits:** a disposable Git checkout is not OS-level sandboxing; filesystem
access outside it is not made impossible by this test. The replay is
non-authoritative: it deliberately does not forge historic `source_digest`,
does not yield Verify PASS and does not mutate production. Production
`ScenarioTestRepairAgent` still has strict freshness/identity validation;
the replay uses a separate test-only subclass that checks its pinned SHA and
original plan identity.

Lifecycle creation/deletion functions are covered by
`tests/opsx/test_opsx_pinned_checkout.py` (14 offline unit cases: pinned SHA,
dirty source, adjacent directory preservation, exception cleanup, injected
Git failures before and after clone, invalid input). These tests can run
without live adapter:

```bash
pytest -m opsx -q tests/opsx/test_opsx_pinned_checkout.py
```

The earlier strict live-Verify gate in
`agents/tests/repair/scenario_test_repair/test_live_model.py` remains
separate and is **not** the default replay entrypoint.
