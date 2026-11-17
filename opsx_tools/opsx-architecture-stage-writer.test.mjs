import { strict as assert } from "node:assert";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { checkArchitectureForWriter } from "./opsx-apply-change-writer.mjs";

test("split-APPLY native write boundary blocks without independent Architecture review", () => {
  const root = mkdtempSync(join(tmpdir(), "opsx-stage-node-"));
  try {
    const change = join(root, "openspec", "changes", "change-test");
    mkdirSync(change, { recursive: true });
    writeFileSync(join(change, "owner-contract.md"), "Owner contract", "utf8");
    writeFileSync(join(change, "proposal.md"), "Proposal", "utf8");
    const outcome = checkArchitectureForWriter({
      repoRoot: root, changeRoot: "openspec/changes/change-test", changeId: "change-test",
    });
    assert.equal(outcome.ok, false);
    assert.equal(outcome.code, "ARCHITECTURE_STAGE_BLOCKED");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("split-APPLY rejects mismatch between explicit Change and path", () => {
  const outcome = checkArchitectureForWriter({
    repoRoot: process.cwd(),
    changeRoot: "openspec/changes/change-test",
    changeId: "different-change",
  });
  assert.equal(outcome.code, "ARCHITECTURE_STAGE_BLOCKED");
  assert.equal(outcome.detail, "unbound_change_identity");
});

test("split-APPLY rejects missing change root", () => {
  const outcome = checkArchitectureForWriter({
    repoRoot: process.cwd(), changeId: "change-test",
  });
  assert.equal(outcome.code, "ARCHITECTURE_STAGE_BLOCKED");
});

test("split-APPLY native writer accepts current synthetic review and rejects stale input", () => {
  const root = mkdtempSync(join(tmpdir(), "opsx-stage-native-"));
  try {
    const id = "arch-native-test";
    const change = join(root, "openspec", "changes", id);
    mkdirSync(change, { recursive: true });
    const owner = join(change, "owner-contract.md");
    writeFileSync(owner, "Owner contract accepted by fixture.\n", "utf8");
    writeFileSync(join(change, "proposal.md"), "Proposal with scope.\n", "utf8");
    const sections = [
      "Context and constraints", "Boundaries and ownership",
      "Decisions and rationale", "Data flow and interfaces",
      "Invariants and failure modes", "Compatibility and migration",
      "Technical design handoff",
    ];
    const architecture = "# Architecture\nARCHITECTURE_STATUS: READY\n\n" +
      sections.map((name) => "## " + name + "\n" +
        "The existing component owns state and keeps all writes atomic.\n").join("\n");
    const digest = createHash("sha256").update(architecture).digest("hex");
    writeFileSync(join(change, "architecture.md"), architecture, "utf8");
    const checker = join(import.meta.dirname ?? join(process.cwd(), "opsx_tools"),
      "..", "tools", "openspec", "architecture_stage_gate.py");
    const python = process.platform === "win32" ? "py" : "python3";
    const prefix = process.platform === "win32" ? ["-3"] : [];
    const proc = spawnSync(python, [
      ...prefix, checker, "record-review", "--root", root, "--change", id,
      "--author-id", "synthetic-author", "--reviewer-id", "synthetic-reviewer",
      "--review-execution-id", "synthetic-review-001", "--reviewed-sha256", digest,
    ], { encoding: "utf8", timeout: 12000 });
    assert.equal(proc.status, 0, proc.stdout + proc.stderr);
    const options = { repoRoot: root, changeRoot: "openspec/changes/" + id, changeId: id };
    assert.equal(checkArchitectureForWriter(options).ok, true);
    writeFileSync(owner, "Owner contract CHANGED after fixture review.\n", "utf8");
    assert.equal(checkArchitectureForWriter(options).code, "ARCHITECTURE_STAGE_BLOCKED");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
