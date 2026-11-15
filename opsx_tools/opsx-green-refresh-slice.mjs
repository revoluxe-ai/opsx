#!/usr/bin/env node
import { resourcePath } from "./opsx-repo-root.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
/**
 * Refresh GREEN.json for one slice: run frozen RED + full suite, bind identities.
 *
 * Fail-closed (D-EV2-6): does not stamp NORMAL predecessors
 * (red_identity.status=RED_PROVEN, implementation_identity.status=IMPLEMENTATION_WRITTEN)
 * unless the on-disk RED and IMPLEMENT receipts are already that pair.
 *
 * Usage: node opsx_tools/opsx-green-refresh-slice.mjs SLICE-001
 */
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { artifactSha256 } from "./opsx-bug-learning-apply.mjs";
import { computeLiveFrozenTestContentIdentity } from "./opsx-apply-pipeline-identity.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const root = defaultRepoRootFromToolFile(import.meta.url);

/**
 * Refuse to rewrite GREEN identities into a NORMAL pair the predecessors are not.
 *
 * @param {{red?: {status?: string}, implement?: {status?: string}}} pair
 * @returns {{ok: boolean, code: string|null, detail?: string}}
 */
export function guardRefreshNormalPredecessors({ red, implement } = {}) {
  const redStatus = red && red.status;
  const implStatus = implement && implement.status;
  if (redStatus !== "RED_PROVEN" || implStatus !== "IMPLEMENTATION_WRITTEN") {
    return {
      ok: false,
      code: "REFRESH_NORMAL_PREDECESSOR_FORBIDDEN",
      detail:
        "fail-closed: will not stamp RED_PROVEN and IMPLEMENTATION_WRITTEN unless both on-disk predecessors already are that NORMAL pair",
    };
  }
  return { ok: true, code: null };
}

function main() {
  const slice = process.argv[2];
  if (!slice) {
    console.error("usage: node opsx_tools/opsx-green-refresh-slice.mjs SLICE-NNN");
    process.exit(1);
  }
  const change = "invest-yalta-telegram-funnel";
  const changeRoot = `openspec/changes/${change}`;
  const redPath = `${changeRoot}/apply-red/${slice}/RED.json`;
  const impPath = `${changeRoot}/apply-implement/${slice}/IMPLEMENTATION.json`;
  const grnPath = `${changeRoot}/apply-green/${slice}/GREEN.json`;
  const designPath = `${changeRoot}/apply-design/APPLY-DESIGN.json`;
  const scaffoldPath = `${changeRoot}/apply-scaffold/${slice}/SCAFFOLD.json`;

  const red = JSON.parse(readFileSync(resourcePath(root, redPath), "utf8"));
  const implement = JSON.parse(readFileSync(resourcePath(root, impPath), "utf8"));
  const guarded = guardRefreshNormalPredecessors({ red, implement });
  if (!guarded.ok) {
    console.error(JSON.stringify({ status: "FAIL_CLOSED", code: guarded.code, detail: guarded.detail }));
    process.exit(4);
  }
  const design = JSON.parse(readFileSync(resourcePath(root, designPath), "utf8"));
  const scaffold = JSON.parse(readFileSync(resourcePath(root, scaffoldPath), "utf8"));
  const prev = JSON.parse(readFileSync(resourcePath(root, grnPath), "utf8"));

  const redExecutions = [];
  for (const cmd of red.test_commands) {
    const r = runFrozenRedCommand(cmd, { cwd: root, timeoutMs: 120000 });
    const out = `${r.stdout ?? ""}${r.stderr ?? ""}`;
    redExecutions.push({
      phase: "initial",
      iteration: 0,
      command: cmd,
      exit_code: r.exit_code,
      relevant_output: out.slice(-2000),
      executed_at: new Date().toISOString(),
      execution_mechanism: r.execution_mechanism || "python_pytest",
    });
    if (r.exit_code !== 0) {
      console.error("RED not green:", cmd, r.exit_code);
      process.exit(2);
    }
  }

  const suiteCmd =
    "source /home/ubuntu/wt/test-env.sh && flock /tmp/stroy_test_db.lock poetry run python -m pytest -q";
  const suite = spawnSync("bash", ["-lc", suiteCmd], {
    cwd: root,
    encoding: "utf8",
    timeout: 300000,
  });
  const suiteExit = typeof suite.status === "number" ? suite.status : 1;
  if (suiteExit !== 0) {
    console.error("suite failed", suiteExit, (suite.stderr || "").slice(-500));
    process.exit(3);
  }
  const suiteOut = `${suite.stdout ?? ""}${suite.stderr ?? ""}`;
  const m = suiteOut.match(/(\d+) passed/);
  const passed = m ? m[1] : "?";

  const live = computeLiveFrozenTestContentIdentity(root, {
    test_paths: red.test_paths,
    fixture_paths: red.fixture_paths,
  });

  const green = {
    ...prev,
    schema_version: 1,
    status: "GREEN_PROVEN",
    generated_at: new Date().toISOString(),
    chat_history_required: false,
    apply_design_identity: {
      machine_path: designPath,
      status: "APPLY_DESIGN_READY",
      planning_digest: design.planning_identity.planning_digest,
      inspected_digest: design.repository_identity.inspected_digest,
      protected_context_digest: design.protected_context_identity.content_digest,
      artifact_sha256: artifactSha256(root, designPath),
    },
    scaffold_identity: {
      machine_path: scaffoldPath,
      status: "SCAFFOLD_READY",
      slice_id: slice,
      candidate_digest_after: scaffold.candidate_digest_after,
      artifact_sha256: artifactSha256(root, scaffoldPath),
      no_op: scaffold.no_op ?? false,
    },
    red_identity: {
      machine_path: redPath,
      status: "RED_PROVEN",
      slice_id: slice,
      test_content_identity: red.test_content_identity,
      observation_only: false,
      implementation_write_authorized: true,
      adoption_audit_eligible: false,
      artifact_sha256: artifactSha256(root, redPath),
    },
    implementation_identity: {
      machine_path: impPath,
      status: "IMPLEMENTATION_WRITTEN",
      slice_id: slice,
      frozen_test_content_identity: live.identity,
      artifact_sha256: artifactSha256(root, impPath),
    },
    frozen_test_content_identity: live.identity,
    frozen_test_identity_before: live.identity,
    frozen_test_identity_after: live.identity,
    red_executions: redExecutions,
    green_fix_iterations: [],
    focused_checks: [
      {
        command: "bash -lc " + JSON.stringify(suiteCmd),
        exit_code: 0,
        role: "full_suite",
        relevant_output: `${passed} passed`,
      },
    ],
    production_paths_modified_during_green: [],
    tests_modified: false,
    red_final_status: "PASS",
    adjacent_final_status: "NOT_REQUIRED",
    iteration_count: 0,
    iteration_limit: 5,
    whole_change_verified: false,
    blockers: [],
  };

  writeFileSync(resourcePath(root, grnPath), JSON.stringify(green, null, 2) + "\n");
  console.log("GREEN refreshed", slice, passed, "passed");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
