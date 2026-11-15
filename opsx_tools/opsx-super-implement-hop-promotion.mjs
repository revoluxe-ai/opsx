import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Trusted promotion from isolated agent workspace → canonical TARGET.
 */
import { cpSync, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { classifyPromotionPath } from "./opsx-super-implement-dual-write-policy.mjs";
import {
  snapshotWorkspaceContent,
  snapshotWorkspaceFiles,
} from "./opsx-super-implement-isolated-workspace.mjs";

function relFromIsolated(isolatedRoot, absPath) {
  const root = resolve(isolatedRoot);
  const norm = resolve(absPath);
  if (!norm.startsWith(root + "/") && norm !== root) return null;
  return norm.slice(root.length + 1).replace(/\\/g, "/");
}

/**
 * Diff isolated workspace vs initial snapshot; return changed paths (isolated-relative mapped to repo-relative).
 */
export function diffIsolatedWorkspace(isolatedRoot, beforeContentSnapshot, changeRoot) {
  const after = snapshotWorkspaceContent(isolatedRoot, { changeRoot });
  const changed = [];
  const deleted = [];
  for (const [path, hash] of beforeContentSnapshot) {
    if (!after.has(path)) deleted.push(path);
  }
  for (const [path, hash] of after) {
    const prev = beforeContentSnapshot.get(path);
    if (prev === undefined || prev !== hash) changed.push(path);
  }
  return {
    changed_isolated_rel: changed,
    changed_repo_rel: changed,
    deleted_isolated_rel: deleted,
    deleted_repo_rel: deleted,
  };
}

/**
 * Fail-closed gate: every changed path must match stage promotion policy.
 */
export function evaluatePromotionGate({
  changeRoot,
  stage,
  changedRepoRelPaths,
  deletedRepoRelPaths = [],
}) {
  const violations = [];
  const allowed = [];
  const allPaths = [...changedRepoRelPaths, ...deletedRepoRelPaths];
  for (const rel of allPaths) {
    const verdict = classifyPromotionPath(changeRoot, stage, rel);
    if (!verdict.ok) violations.push(verdict);
    else allowed.push({ path: rel, surface: verdict.surface });
  }
  if (violations.length) {
    return {
      ok: false,
      code: "PROMOTION_GATE_REJECTED",
      violations,
      recovery_contract: {
        code: "PROMOTION_GATE_REJECTED",
        next_action: "discard hop; agent produced out-of-scope writes",
        resume_requires: ["agent_hop_restarted"],
      },
    };
  }
  return { ok: true, allowed };
}

/**
 * Copy allowed files from isolated workspace into canonical repo (idempotent paths).
 */
export function applyPromotion({ repoRoot, isolatedRoot, allowedPaths }) {
  const applied = [];
  for (const entry of allowedPaths) {
    const rel = entry.path;
    const src = join(isolatedRoot, rel);
    const dest = resourcePath(repoRoot, rel);
    if (!existsSync(src)) continue;
    mkdirSync(dirname(dest), { recursive: true });
    cpSync(src, dest, { recursive: statSync(src).isDirectory() });
    applied.push(rel);
  }
  return { ok: true, applied };
}

export function loadPromotionReceipt(isolatedRoot) {
  const p = join(isolatedRoot, "opsx-agent-hop-promotion.json");
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}
