import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Test authoring helpers for agent hop V0 (deterministic frozen RED / IMPLEMENT / GREEN).
 *
 * Not a production SDK substitute; drives operative gates with real command execution.
 */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { computeSliceBehaviorObligationDigest } from "./opsx-apply-red-oracle-authority.mjs";
import {
  APPLY_RED_SCHEMA_VERSION,
  GREEN_PROVEN,
  IMPLEMENTATION_WRITTEN,
  RED_EVIDENCE_CONTRACT,
  RED_PROVEN,
  RED_PROVEN_CLASSIFICATION,
  SCAFFOLD_READY,
} from "./opsx-apply-pipeline-identity.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";

export const AGENT_HOP_TEST_REL = "tests/unit/fixtures/agentHopV0/hop_red.test.mjs";
export const AGENT_HOP_PRODUCT_REL = "app/agent_hop_fixture/hop.mjs";

export function agentHopTestCommand() {
  return `node --test ${AGENT_HOP_TEST_REL}`;
}

export function seedAgentHopProduct(isolatedRoot, { implemented = false } = {}) {
  const body = implemented
    ? `/** Disposable product seam for Super Implement agent-hop V0 acceptance. */\n\nexport function hopValue() {\n  return 7;\n}\n`
    : `/** Disposable product seam for Super Implement agent-hop V0 acceptance. */\n\nexport function hopValue() {\n  return 0;\n}\n`;
  const dir = join(isolatedRoot, "app", "agent_hop_fixture");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "hop.mjs"), body);
}

export function seedAgentHopFrozenTest(isolatedRoot) {
  const dir = join(isolatedRoot, "tests", "unit", "fixtures", "agentHopV0");
  mkdirSync(dir, { recursive: true });
  const src = `// @opsx-red-spec-value: 7
// @opsx-red-behavior-value: 0

import assert from "node:assert/strict";
import { test } from "node:test";
import { hopValue } from "../../../../app/agent_hop_fixture/hop.mjs";

test("agent hop v0 frozen red", () => {
  assert.equal(hopValue(), 7);
});
`;
  writeFileSync(join(dir, "hop_red.test.mjs"), src);
}

export function buildOperativeRedReceipt(repoRoot, sliceId, changeRoot) {
  const designPath = resourcePath(repoRoot, changeRoot, "apply-design", "APPLY-DESIGN.json");
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const slice =
    design.slices?.find((s) => s.slice_id === sliceId) ||
    { slice_id: sliceId, title: sliceId, source_task_ids: [], dependencies_on_prior_slices: [], red_test_targets_or_seams: [] };
  const obligation = computeSliceBehaviorObligationDigest(design, slice);
  const cmd = agentHopTestCommand();
  const live = runFrozenRedCommand(cmd, { cwd: repoRoot, timeoutMs: 120000 });
  assert.notEqual(live.exit_code, 0);
  const identity = computeFrozenTestContentIdentity(repoRoot, [AGENT_HOP_TEST_REL]);
  assert.equal(identity.ok, true);
  return {
    schema: "opsx.apply_red",
    schema_version: APPLY_RED_SCHEMA_VERSION,
    evidence_contract: RED_EVIDENCE_CONTRACT,
    status: RED_PROVEN,
    slice_id: sliceId,
    tests_frozen: true,
    test_paths: [AGENT_HOP_TEST_REL],
    test_content_identity: identity.digest,
    behavior_obligation_identity: {
      slice_id: sliceId,
      planning_digest: design.planning_identity.planning_digest,
      obligation_digest: obligation.digest,
    },
    apply_design_identity: {
      artifact_path: join(changeRoot, "apply-design/APPLY-DESIGN.json").replace(/\\/g, "/"),
      status: design.status,
      planning_digest: design.planning_identity.planning_digest,
    },
    test_commands: [cmd],
    command_executions: [
      {
        command: cmd,
        test_path: AGENT_HOP_TEST_REL,
        red_qualified: true,
        pass_qualified: false,
        final_exit_code: live.exit_code,
        final_classification: RED_PROVEN_CLASSIFICATION,
        attempts: [{ exit_code: live.exit_code }],
      },
    ],
  };
}

export function buildImplementReceipt(sliceId, frozenDigest) {
  return {
    status: IMPLEMENTATION_WRITTEN,
    slice_id: sliceId,
    next_stage: "APPLY_GREEN",
    tests_modified: false,
    tests_executed: false,
    frozen_test_content_identity: frozenDigest,
    red_identity: {
      status: RED_PROVEN,
      slice_id: sliceId,
      test_content_identity: frozenDigest,
    },
    production_paths_modified: [AGENT_HOP_PRODUCT_REL],
  };
}

export function buildGreenReceiptFromUpstream(sliceId, ctx) {
  const { design, maps } = ctx;
  const red = maps.redBySlice?.[sliceId];
  const implement = maps.implementBySlice?.[sliceId];
  const scaffold = maps.scaffoldBySlice?.[sliceId];
  const frozen =
    maps.liveFrozenTestContentBySlice?.[sliceId] ||
    maps.frozenTestContentBySlice?.[sliceId] ||
    red?.test_content_identity ||
    implement?.frozen_test_content_identity ||
    null;
  return {
    status: GREEN_PROVEN,
    slice_id: sliceId,
    apply_design_identity: {
      status: design?.status || "APPLY_DESIGN_READY",
      planning_digest: design?.planning_identity?.planning_digest,
      artifact_sha256: maps.designArtifactSha256,
    },
    scaffold_identity: {
      status: scaffold?.status || SCAFFOLD_READY,
      slice_id: sliceId,
      artifact_sha256: maps.scaffoldArtifactSha256BySlice?.[sliceId],
    },
    red_identity: {
      status: red?.status || RED_PROVEN,
      slice_id: sliceId,
      test_content_identity: frozen,
      artifact_sha256: maps.redArtifactSha256BySlice?.[sliceId],
    },
    implementation_identity: {
      status: implement?.status || IMPLEMENTATION_WRITTEN,
      slice_id: sliceId,
      artifact_sha256: maps.implementArtifactSha256BySlice?.[sliceId],
      frozen_test_content_identity: implement?.frozen_test_content_identity,
    },
    frozen_test_content_identity: frozen,
  };
}
