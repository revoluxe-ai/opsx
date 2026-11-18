# Provider Execution Session V1 ? adapter boundary

Status: IMPLEMENTED_SLICE / NOT_RELEASED / CODEX_PARENT_NOT_READY

## Owner requirement

OPSX owns one workflow. Cursor SDK and Codex are replaceable implementations
of the same adapter/runtime contract, not separate workflows. Provider bridge,
transport, run identity, model selection and reconciliation belong to provider.

## Contract

The parent opens a provider-specific ExecutionSession selected by the pinned
OPSX_ADAPTER_MODULE. The session owns its transport/bridge lifecycle,
review/repair adapter creation, model tier and pre-dispatch reconciliation.
OPSX owns state machine, task semantics, evidence gate, write authorization,
recovery policy and final outcome.

The registry is keyed by provider identity: import order cannot switch tier.

## Current realization

CursorExecutionSession owns SDK bridge, Cursor GetRun reconciliation, ledger
updates and adapters. CodexExecutionSession owns its Codex adapters, with no
Cursor bridge. Codex reconciliation currently raises
CODEX_RECONCILIATION_NOT_IMPLEMENTED. No empty clean ledger or false PASS.
The legacy safety gate is excluded from this candidate: provider-specific runtime
is selected through the common worker contract. Parent E2E remains unproven,
and this PR must not be represented as a verified production rollout.

## SQLite durable-state policy (Owner decision)

Technical operational state is SQLite-only: worktree manifest/identity,
Codex thread/turn ledger, before-image write journal, cross-provider dispatch
and recovery. Any JSON inside SQLite columns is a serialized value, not an
independent mutable source of truth. Source tree and model tool callbacks
must never own a competing state file. Readable diagnostics may be exported
separately but cannot satisfy state transitions.

All native Codex SQLite stores use short transactions, WAL and synchronous
FULL, with a bounded ~350 ms lock timeout. No database transaction remains
open during Codex app-server calls, Git worktree setup, model inference or
external test execution. Lock contention is explicit FAIL/PAUSE with
reconciliation, not an indefinite wait/retry loop. This does not mean SQLite
eliminates external provider/IPC hangs; those need their own watchdogs.

Legacy OPSX panel status JSON and checkpoint stores are outside this adapter
slice and must not be silently treated as converted or retired.

## Target states

CREATED -> PROVIDER_BOUND -> OPEN -> RECONCILED -> DISPATCHABLE -> IN_FLIGHT
-> VERIFIED -> CLOSED.

Unknown in-flight state must PAUSE; permanent mismatch FAIL. CLOSED is not
PASS; the verification gate requires execution- and revision-bound evidence.

## Authority, writes, retries and evidence

Execution identity binds provider, model/tier, run/thread/turn, project, scope,
input revision and execution ID. No fallback to different provider/model.
Codex writes require stage-scoped guard, linked worktree, durable before-image
journal and net-effect evidence; model output is not proof of mutation.
Retry must reconcile original identity, prove restoration or target state,
and stop on repeated unresolved cause, sibling failure or regression.
Stale evidence or evidence from another run cannot satisfy the gate.

## Acceptance results

PASS: parent composition root no longer opens Cursor bridge/GetRun.
PASS: Cursor retains its bridge and reconciliation behind adapter contract.
PASS: Codex session does not acquire Cursor bridge and rejects unproven replay.
PASS: factories/hooks select exact provider despite mixed import ordering.

PASS: bounded native read/edit/create and scoped pytest tool binding,
plus apply-design capability bridge using the original three manifest tools.
OPEN: full implementation and verification stage coverage, including
specialized and image operations not yet mapped to native authority.
PASS: native Codex durable thread/turn identity, exact-id resume authorization,
and live cross-process resume with the same thread and a new turn.
OPEN: recovery of a provider turn with unknown remote completion; do not
infer successful termination from a lost response.
OPEN: tool authority and hard-pinned model tier for every graph hop.
OPEN: independent stage-scoped sandbox and complete E2E including crashes,
stale evidence, retry/recovery, mixed-provider denial, final verification.

This adapter-boundary change is not live Codex parent execution authorization.
Do not approve production rollout until all OPEN items are proven.
