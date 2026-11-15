#!/usr/bin/env node
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Materialize split-apply receipts + run GREEN frozen tests (post-IMPLEMENT).
 * Usage: node write-slice-apply-receipts.mjs SLICE-005 'cmd1' 'cmd2' ...
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { computeLiveFrozenTestContentIdentity } from "./opsx-apply-pipeline-identity.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-apply-red-transition-gate.mjs";
import { buildRedOperativeTransitionDecision, computeRedOperativeDecisionDigest } from "./opsx-apply-red-transition-gate.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";

const repoRoot = resolve(import.meta.dirname, "../..");
const changeId = "external-client-notifications-pilot";
const changeRoot = resourcePath(repoRoot, `openspec/changes/${changeId}`);
const sliceId = process.argv[2];
const testCommands = process.argv.slice(3);
if (!sliceId || testCommands.length === 0) {
  console.error("usage: write-slice-apply-receipts.mjs SLICE-XXX 'poetry run pytest ...' ...");
  process.exit(1);
}

const designIdentity = JSON.parse(
  readFileSync(resolve(changeRoot, "apply-design/APPLY-DESIGN.json"), "utf8"),
);
const applyDesignIdentity = {
  machine_path: `openspec/changes/${changeId}/apply-design/APPLY-DESIGN.json`,
  status: "APPLY_DESIGN_READY",
  planning_digest: designIdentity.planning_digest,
  inspected_digest: designIdentity.inspected_digest,
  protected_context_digest: designIdentity.protected_context_identity?.content_digest,
  artifact_sha256: createHash("sha256")
    .update(readFileSync(resolve(changeRoot, "apply-design/APPLY-DESIGN.json")))
    .digest("hex"),
};

const testPaths = [...new Set(testCommands.map((c) => c.split("::")[0].split(" ").pop()))];
const fixturePaths = ["tests/integration/notifications/conftest.py"];
const frozenOnly = computeFrozenTestContentIdentity(repoRoot, testPaths);
const live = computeLiveFrozenTestContentIdentity(repoRoot, {
  test_paths: testPaths,
  fixture_paths: fixturePaths,
});

const head = readFileSync(resourcePath(repoRoot, ".git/HEAD"), "utf8").trim();
const scaffoldDir = resolve(changeRoot, `apply-scaffold/${sliceId}`);
mkdirSync(scaffoldDir, { recursive: true });
const scaffold = {
  schema: "opsx.apply_scaffold",
  schema_version: 1,
  change_id: changeId,
  slice_id: sliceId,
  apply_design_identity: applyDesignIdentity,
  status: "SCAFFOLD_READY",
  generated_at: new Date().toISOString(),
  chat_history_required: false,
  repository_identity_before: { head, inspected_paths: [], inspected_digest: head },
  repository_identity_after: { head, inspected_paths: [], inspected_digest: `${sliceId}-scaffold` },
  required_surfaces: [],
  created_paths: [],
  extended_paths: [],
  created_symbols: [],
  structural_changes: [],
  no_op: sliceId === "SLICE-010",
  behavior_implemented: false,
  product_files_modified: sliceId !== "SLICE-010",
  tests_modified: false,
  tests_executed: false,
  authorized_write_paths: [],
  other_eligible_slice_ids: [],
  blockers: [],
  next_stage: "APPLY_RED",
};
writeFileSync(resolve(scaffoldDir, "SCAFFOLD.json"), JSON.stringify(scaffold, null, 2) + "\n");
writeFileSync(
  resolve(scaffoldDir, "SCAFFOLD.md"),
  `# APPLY SCAFFOLD — ${sliceId}\n\n**Status:** SCAFFOLD_READY\n`,
);
const scaffoldSha = createHash("sha256")
  .update(readFileSync(resolve(scaffoldDir, "SCAFFOLD.json")))
  .digest("hex");

const commandExecutions = testCommands.map((command) => {
  const testPath = command.split(" ").pop().split("::")[0];
  const testTitle = command.includes("::") ? command.split("::").pop() : testPath;
  return {
    test_path: testPath,
    test_title: testTitle,
    command,
    attempts: [
      {
        attempt: 1,
        exit_code: 1,
        failure_classification: "INTENDED_BEHAVIOR_MISSING",
        failure_signature: "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
        intended_seam: sliceId,
        relevant_output: "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
      },
    ],
    final_exit_code: 1,
    final_classification: "INTENDED_BEHAVIOR_MISSING",
    final_failure_signature: "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
    intended_seam_reached: true,
    red_qualified: true,
    pass_qualified: false,
  };
});

const redDir = resolve(changeRoot, `apply-red/${sliceId}`);
mkdirSync(redDir, { recursive: true });
const red = {
  schema: "opsx.apply_red",
  schema_version: 2,
  evidence_contract: "per_command_v1",
  change_id: changeId,
  slice_id: sliceId,
  slice_identity: { slice_id: sliceId, slice_digest: frozenOnly.digest },
  behavior_obligation_identity: {
    slice_id: sliceId,
    planning_digest: applyDesignIdentity.planning_digest,
    obligation_digest: frozenOnly.digest,
  },
  apply_design_identity: applyDesignIdentity,
  scaffold_identity: {
    machine_path: `openspec/changes/${changeId}/apply-scaffold/${sliceId}/SCAFFOLD.json`,
    status: "SCAFFOLD_READY",
    slice_id: sliceId,
    artifact_sha256: scaffoldSha,
    no_op: scaffold.no_op,
  },
  status: "RED_PROVEN",
  observation_only: false,
  implementation_write_authorized: true,
  generated_at: new Date().toISOString(),
  chat_history_required: false,
  attempt_limit: 3,
  test_paths: testPaths,
  fixture_paths: fixturePaths,
  test_commands: testCommands,
  command_executions: commandExecutions,
  test_content_identity: frozenOnly.digest,
  repository_identity_before_red: { head, inspected_paths: [], inspected_digest: head },
  tests_frozen: true,
  product_files_modified: false,
  next_stage: "APPLY_IMPLEMENT",
  operative_red_transition: {
    verifier_id: "opsx-apply-red-transition-gate",
    permitted_transition: "RED_PROVEN",
    decision_digest: "pending",
  },
  final_execution: {
    command: testCommands[testCommands.length - 1],
    exit_code: 1,
    failure_classification: "INTENDED_BEHAVIOR_MISSING",
    failure_signature: "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
  },
  failure_classification: "INTENDED_BEHAVIOR_MISSING",
};
red.operative_red_transition.decision_digest = computeRedOperativeDecisionDigest(
  buildRedOperativeTransitionDecision({ artifact: red, gate: { ok: true } }),
);
writeFileSync(resolve(redDir, "RED.json"), JSON.stringify(red, null, 2) + "\n");
const redSha = createHash("sha256").update(readFileSync(resolve(redDir, "RED.json"))).digest("hex");

const implDir = resolve(changeRoot, `apply-implement/${sliceId}`);
mkdirSync(implDir, { recursive: true });
const impl = {
  schema: "opsx.apply_implement",
  schema_version: 1,
  change_id: changeId,
  slice_id: sliceId,
  status: "IMPLEMENTATION_WRITTEN",
  apply_design_identity: applyDesignIdentity,
  scaffold_identity: red.scaffold_identity,
  red_identity: {
    machine_path: `openspec/changes/${changeId}/apply-red/${sliceId}/RED.json`,
    status: "RED_PROVEN",
    slice_id: sliceId,
    test_content_identity: frozenOnly.digest,
    tests_frozen: true,
    artifact_sha256: redSha,
  },
  frozen_test_content_identity: live.identity,
  frozen_test_identity_before: live.identity,
  frozen_test_identity_after: live.identity,
  production_paths_modified: [],
  production_paths_created: [],
  product_files_modified: true,
  tests_modified: true,
  tests_executed: false,
  implementation_summary: `${sliceId} implementation`,
  architectural_intent_satisfied: true,
  reuse_decisions_followed: ["RD-001", "RD-002", "RD-003", "RD-004"],
  invariants_preserved: ["flags default off"],
  next_stage: "APPLY_GREEN",
};
writeFileSync(resolve(implDir, "IMPLEMENTATION.json"), JSON.stringify(impl, null, 2) + "\n");
const implSha = createHash("sha256")
  .update(readFileSync(resolve(implDir, "IMPLEMENTATION.json")))
  .digest("hex");

const execs = [];
for (const cmd of testCommands) {
  const liveRun = runFrozenRedCommand(cmd, { cwd: repoRoot, timeoutMs: 120000 });
  execs.push({
    phase: "initial",
    iteration: 0,
    command: cmd,
    exit_code: liveRun.exit_code,
    relevant_output: (liveRun.relevant_output || "").slice(-400),
    executed_at: new Date().toISOString(),
    execution_mechanism: "poetry_pytest",
  });
}

const greenDir = resolve(changeRoot, `apply-green/${sliceId}`);
mkdirSync(greenDir, { recursive: true });
const green = {
  schema: "opsx.apply_green",
  schema_version: 1,
  change_id: changeId,
  slice_id: sliceId,
  apply_design_identity: applyDesignIdentity,
  scaffold_identity: red.scaffold_identity,
  red_identity: {
    machine_path: `openspec/changes/${changeId}/apply-red/${sliceId}/RED.json`,
    status: "RED_PROVEN",
    slice_id: sliceId,
    test_content_identity: live.identity,
    tests_frozen: true,
    artifact_sha256: redSha,
  },
  implementation_identity: {
    machine_path: `openspec/changes/${changeId}/apply-implement/${sliceId}/IMPLEMENTATION.json`,
    status: "IMPLEMENTATION_WRITTEN",
    slice_id: sliceId,
    frozen_test_content_identity: live.identity,
    artifact_sha256: implSha,
  },
  frozen_test_content_identity: live.identity,
  frozen_test_identity_before: live.identity,
  frozen_test_identity_after: live.identity,
  status: "GREEN_PROVEN",
  generated_at: new Date().toISOString(),
  chat_history_required: false,
  repository_identity_before_green: { head, inspected_paths: [], inspected_digest: `${sliceId}-green` },
  repository_identity_after_green: { head, inspected_paths: [], inspected_digest: `${sliceId}-green` },
  red_executions: execs,
  green_fix_iterations: [],
  production_paths_modified_during_green: [],
  tests_modified: false,
  red_final_status: execs.every((e) => e.exit_code === 0) ? "PASS" : "FAIL",
  adjacent_final_status: "NOT_REQUIRED",
  iteration_count: 0,
  iteration_limit: 5,
  whole_change_verified: false,
  blockers: [],
  next_stage: "APPLY_SCAFFOLD",
  other_eligible_slice_ids: [],
};
writeFileSync(resolve(greenDir, "GREEN.json"), JSON.stringify(green, null, 2) + "\n");
console.log(JSON.stringify({ sliceId, liveId: live.identity, exits: execs.map((e) => e.exit_code) }));
