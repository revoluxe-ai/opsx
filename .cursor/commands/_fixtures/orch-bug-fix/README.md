# Fixtures: /opsx-orch-bug-fix

Process-only regression cases for the experimental bug-fix orchestration scenario.
Validates outer PP semantics against `OPSX-ORCH-CORE-01`, `OPSX-ORCH-ASYNC-TRANSPORT-01`,
and responsibility boundaries with `/opsx-bug-team`.

Contract tests: `tests/unit/opsxOrchBugFixContract.test.mjs`

## Index

| Fixture | Theme |
|---------|-------|
| `bug_team_ready_for_closure_then_outer_materialize.md` | READY_FOR_CLOSURE → outer materialize close=fixed |
| `bug_team_already_closed_fixed_no_duplicate_materialize.md` | durable closed_fixed → skip materialize |
| `bug_team_blocked_no_close_no_evolution_waiver.md` | BLOCKED MUST_FIX → no close, no tail waiver |
| `three_distinct_disposition_debt_tails_preserved_exactly.md` | 3 tails from disposition receipts, no merge |
| `bug_team_learning_does_not_create_or_merge_evolution_queue.md` | learning ≠ evolution queue authority |
| `bug_team_project_bug_hunt_next_action_not_followed_by_orch_v1.md` | no auto discovery loop |
| `one_outer_background_task_per_complete_bug_team_episode.md` | OUTER_TASKS=1 per episode |
| `no_outer_wave_duplication.md` | no WAVE_1–4 outer modeling |
| `timeout_expired_but_worker_liveness_unconfirmed_no_lock_release_no_retry.md` | STOP_UNCONFIRMED safety |
| `auto_persist_disposition_no_owner_wake.md` | S2 DELEGATED_PERSIST without Owner wake |
| `auto_persist_family_plan_no_owner_wake.md` | S1 DELEGATED_PERSIST without Owner wake |
| `auto_persist_materialize_closure_no_owner_wake.md` | S5b materialize without Owner wake |
| `release_clear_passthrough_must_fix_only.md` | CHANGE_CLEAR/RELEASE_CLEAR disposition passthrough |
| `worktree_two_work_items_split_ownership.md` | S3.5 splits dirty paths per work item |
| `worktree_cross_item_absorption_forbidden.md` | no cross-item partial-repair absorption |
| `terminal_previous_mission_does_not_shadow_immediate_bugbot_findings.md` | R2 beats terminal prior mission on bare invoke |
| `mixed_product_process_intake_split.md` | S0 splits product vs process; meta-fix handoff |
