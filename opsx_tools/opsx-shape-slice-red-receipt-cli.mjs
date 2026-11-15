#!/usr/bin/env node
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Детерминированно дописать RED receipt: obligation_digest, test_content_identity,
 * command_executions из live pytest (не вычисляет digests моделью).
 */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import { computeSliceBehaviorObligationDigest } from "./opsx-apply-red-oracle-authority.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { ALREADY_SATISFIED, RED_PROVEN, RED_PROVEN_CLASSIFICATION } from "./opsx-apply-pipeline-identity.mjs";

const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

function parseArgs(argv) {
  const out = { repo: DEFAULT_ROOT, changeId: "", sliceId: "" };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) {
      out.repo = argv[i + 1];
      i += 1;
    } else if (argv[i] === "--change" && argv[i + 1]) {
      out.changeId = argv[i + 1];
      i += 1;
    } else if (argv[i] === "--slice" && argv[i + 1]) {
      out.sliceId = argv[i + 1];
      i += 1;
    }
  }
  return out;
}

function artifactSha256(repo, relPath) {
  const abs = resourcePath(repo, relPath);
  return createHash("sha256").update(readFileSync(abs)).digest("hex");
}

function main() {
  const { repo, changeId, sliceId } = parseArgs(process.argv);
  if (!changeId || !sliceId) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "ARGS" })}\n`);
    process.exit(1);
  }
  const changeRoot = `openspec/changes/${changeId}`;
  const redRel = `${changeRoot}/apply-red/${sliceId}/RED.json`;
  const designRel = `${changeRoot}/apply-design/APPLY-DESIGN.json`;
  const redPath = resourcePath(repo, redRel);
  const designPath = resourcePath(repo, designRel);
  if (!redPath || !designPath) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "MISSING_INPUT" })}\n`);
    process.exit(2);
  }
  const red = JSON.parse(readFileSync(redPath, "utf8"));
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const slice =
    (design.slices || []).find((s) => String(s.slice_id) === sliceId) || null;
  if (!slice) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "SLICE_MISSING" })}\n`);
    process.exit(3);
  }
  const obligation = computeSliceBehaviorObligationDigest(design, slice);
  if (!obligation.ok) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: obligation.code })}\n`);
    process.exit(4);
  }
  const testPaths = [...(red.test_paths || []), ...(red.fixture_paths || [])].map(String);
  const identity = computeFrozenTestContentIdentity(repo, testPaths);
  const testContentId = identity.ok ? identity.digest : null;
  const status = String(red.status || "");
  const commands = Array.isArray(red.test_commands) ? red.test_commands.map(String) : [];
  const commandExecutions = [];
  for (const command of commands) {
    const live = runFrozenRedCommand(command, { cwd: repo, timeoutMs: 120000 });
    const pass = live.exit_code === 0;
    const classification =
      status === ALREADY_SATISFIED
        ? pass
          ? "ALREADY_SATISFIED"
          : "INTENDED_BEHAVIOR_MISSING"
        : pass
          ? "ALREADY_SATISFIED"
          : RED_PROVEN_CLASSIFICATION;
    commandExecutions.push({
      command,
      final_exit_code: live.exit_code,
      final_classification: classification,
      red_qualified: !pass && classification === RED_PROVEN_CLASSIFICATION,
      pass_qualified: pass && classification === ALREADY_SATISFIED,
      attempts: [
        {
          attempt: 1,
          exit_code: live.exit_code,
          failure_classification: classification,
          relevant_output: String(live.relevant_output || "").slice(0, 4000),
        },
      ],
    });
  }
  const next = {
    ...red,
    test_content_identity: testContentId,
    behavior_obligation_identity: {
      slice_id: sliceId,
      planning_digest: design?.planning_identity?.planning_digest || null,
      obligation_digest: obligation.digest,
    },
    command_executions: commandExecutions,
    tests_frozen: true,
  };
  writeFileSync(redPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  process.stdout.write(
    `${JSON.stringify({
      ok: true,
      path: redRel,
      test_content_identity: testContentId,
      obligation_digest: obligation.digest,
    })}\n`,
  );
}

main();
