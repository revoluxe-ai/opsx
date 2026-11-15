import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Canonical authoritative execution identity for Break Glass binding (P0-1).
 *
 * Runtime identity is independent of grant payload; grant is compared against this
 * authoritative view only.
 */
import { createHash } from "node:crypto";
import { execSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { readWriterLock } from "./opsx-apply-change-writer.mjs";

const CHANGES_REL = "openspec/changes";
const ACTIVE_SUPER_RUN_STATUSES = new Set(["RUNNING", "PAUSED"]);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/**
 * Normalize caller context; authoritative fields must come from runtimeIdentity.
 */
export function normalizeExecutionContext(raw = {}, runtimeIdentity = {}) {
  const r = raw || {};
  const id = runtimeIdentity || {};
  return {
    repo: id.repo ?? r.repo ?? r.repository ?? null,
    branch: id.branch ?? r.branch ?? null,
    revision: id.revision ?? r.revision ?? r.head ?? null,
    change_id: id.change_id ?? id.changeId ?? r.change_id ?? r.changeId ?? null,
    execution_id: id.execution_id ?? id.executionId ?? r.execution_id ?? r.executionId ?? null,
    blocked_gate: id.blocked_gate ?? id.blockedGate ?? r.blocked_gate ?? r.blockedGate ?? null,
    failure_code:
      id.failure_code ?? id.failureCode ?? r.failure_code ?? r.failureCode ?? null,
    slice_id: id.slice_id ?? id.sliceId ?? r.slice_id ?? r.sliceId ?? null,
  };
}

function listOpsxChangeRoots(repoRoot) {
  const base = resourcePath(repoRoot, CHANGES_REL);
  if (!existsSync(base)) return [];
  return readdirSync(base, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith("."))
    .map((d) => `${CHANGES_REL}/${d.name}`);
}

/**
 * Read canonical `design.change_id` from APPLY-DESIGN.json under a change root.
 */
export function loadOpsxChangeDesignBinding(repoRoot, changeRootRel) {
  const designPath = resourcePath(repoRoot, changeRootRel, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    return { ok: false, code: "APPLY_DESIGN_MISSING", change_root: changeRootRel };
  }
  try {
    const design = JSON.parse(readFileSync(designPath, "utf8"));
    const changeId = design?.change_id;
    if (!changeId) {
      return { ok: false, code: "CHANGE_ID_MISSING", change_root: changeRootRel };
    }
    return {
      ok: true,
      change_id: changeId,
      change_root: design.planning_identity?.change_root || changeRootRel,
      design,
    };
  } catch {
    return { ok: false, code: "APPLY_DESIGN_CORRUPT", change_root: changeRootRel };
  }
}

function uniqueBindingOrAmbiguous(candidates, source) {
  if (!candidates.length) {
    return { ok: false, code: "ACTIVE_CHANGE_UNRESOLVED", reason: "NO_OPSX_CHANGE_CONTEXT" };
  }
  const byId = new Map();
  for (const c of candidates) {
    byId.set(c.change_id, c);
  }
  if (byId.size !== 1) {
    return {
      ok: false,
      code: "ACTIVE_CHANGE_AMBIGUOUS",
      reason: "MULTIPLE_OPSX_CHANGES",
      candidates: [...byId.values()],
    };
  }
  const only = [...byId.values()][0];
  return { ok: true, ...only, source };
}

/**
 * Resolve active OPSX change for break glass (design.change_id, not git branch name).
 */
export function resolveActiveOpsxChangeContext(
  repoRoot,
  { changeRootHint = null, changeIdHint = null } = {},
) {
  const root = resolve(repoRoot || ".");

  if (changeRootHint) {
    const bound = loadOpsxChangeDesignBinding(root, changeRootHint);
    if (!bound.ok) return bound;
    if (changeIdHint && bound.change_id !== changeIdHint) {
      return { ok: false, code: "CHANGE_ID_ROOT_MISMATCH", change_root: changeRootHint };
    }
    return { ok: true, change_id: bound.change_id, change_root: bound.change_root, source: "change_root_hint" };
  }

  if (changeIdHint) {
    const matches = [];
    for (const cr of listOpsxChangeRoots(root)) {
      const bound = loadOpsxChangeDesignBinding(root, cr);
      if (bound.ok && bound.change_id === changeIdHint) {
        matches.push({ change_id: bound.change_id, change_root: bound.change_root });
      }
    }
    if (matches.length) {
      return uniqueBindingOrAmbiguous(matches, "change_id_hint");
    }
    return {
      ok: true,
      change_id: changeIdHint,
      change_root: null,
      source: "change_id_explicit",
    };
  }

  const fromRuns = [];
  for (const cr of listOpsxChangeRoots(root)) {
    const runPath = resourcePath(root, cr, "super-implement/RUN.json");
    if (!existsSync(runPath)) continue;
    try {
      const run = JSON.parse(readFileSync(runPath, "utf8"));
      if (!ACTIVE_SUPER_RUN_STATUSES.has(run?.status)) continue;
      const bound = loadOpsxChangeDesignBinding(root, cr);
      if (bound.ok) {
        fromRuns.push({
          change_id: bound.change_id,
          change_root: bound.change_root,
          run_status: run.status,
        });
      }
    } catch {
      /* skip corrupt run */
    }
  }
  const runPick = uniqueBindingOrAmbiguous(fromRuns, "super_implement_run");
  if (runPick.ok) return runPick;

  const fromLocks = [];
  for (const cr of listOpsxChangeRoots(root)) {
    const lock = readWriterLock(root, cr);
    if (!lock?.holder) continue;
    const bound = loadOpsxChangeDesignBinding(root, cr);
    if (bound.ok) {
      fromLocks.push({
        change_id: bound.change_id,
        change_root: bound.change_root,
        writer_holder: lock.holder,
      });
    }
  }
  return uniqueBindingOrAmbiguous(fromLocks, "writer_lock");
}

export function executionIdentityDigest(ctx) {
  const n = normalizeExecutionContext({}, ctx);
  const payload = [
    `repo=${n.repo}`,
    `branch=${n.branch}`,
    `revision=${n.revision}`,
    `change_id=${n.change_id}`,
    `execution_id=${n.execution_id}`,
    `blocked_gate=${n.blocked_gate}`,
    `failure_code=${n.failure_code}`,
  ].join("\n");
  return createHash("sha256").update(payload, "utf8").digest("hex");
}

/**
 * Resolve authoritative runtime identity (git-backed when not overridden in hermetic tests).
 */
export function resolveAuthoritativeExecutionIdentity({
  repoRoot,
  changeId,
  executionId,
  blockedGate,
  failureCode,
  branch = null,
  revision = null,
  repo = "revoluxe-ai/stroy-system",
} = {}) {
  const root = resolve(repoRoot || ".");
  const hasGit = existsSync(resourcePath(root, ".git"));
  let gitHead = null;
  let gitBranch = null;
  if (hasGit) {
    try {
      gitHead = execSync("git rev-parse HEAD", { cwd: root, encoding: "utf8" }).trim();
    } catch {
      gitHead = null;
    }
    try {
      gitBranch = execSync("git rev-parse --abbrev-ref HEAD", {
        cwd: root,
        encoding: "utf8",
      }).trim();
    } catch {
      gitBranch = null;
    }
  }
  if (revision != null && revision !== "" && gitHead && String(revision) !== String(gitHead)) {
    return {
      ok: false,
      code: "PAUSED",
      reason: "OPERATOR_REVISION_MISMATCH",
      authoritative_revision: gitHead,
      operator_revision: revision,
    };
  }
  if (branch != null && branch !== "" && gitBranch && String(branch) !== String(gitBranch)) {
    return {
      ok: false,
      code: "PAUSED",
      reason: "OPERATOR_BRANCH_MISMATCH",
      authoritative_branch: gitBranch,
      operator_branch: branch,
    };
  }
  const head = gitHead || revision;
  const effectiveBranch = gitBranch || branch;
  if (!changeId || !executionId || !blockedGate || !failureCode || !head) {
    return { ok: false, code: "EXECUTION_IDENTITY_INCOMPLETE" };
  }
  const identity = {
    repo,
    branch: effectiveBranch || "unknown",
    revision: head,
    change_id: changeId,
    execution_id: executionId,
    blocked_gate: blockedGate,
    failure_code: failureCode,
  };
  return { ok: true, identity, digest: executionIdentityDigest(identity) };
}

/**
 * Bind grant to authoritative runtime identity (all predicates required).
 */
export function bindGrantToRuntimeIdentity(grant, runtimeIdentity) {
  if (!isObject(grant) || !isObject(runtimeIdentity)) {
    return { ok: false, code: "DENIED", reason: "BINDING_CONTEXT_MISSING" };
  }
  const rid = grant.repository_identity || {};
  const checks = [
    ["GRANT_REPO_MISMATCH", rid.repo, runtimeIdentity.repo],
    ["GRANT_BRANCH_MISMATCH", rid.branch, runtimeIdentity.branch],
    ["GRANT_REVISION_MISMATCH", rid.revision, runtimeIdentity.revision],
    ["GRANT_CHANGE_MISMATCH", grant.change_id, runtimeIdentity.change_id],
    ["GRANT_EXECUTION_MISMATCH", grant.execution_id, runtimeIdentity.execution_id],
    ["GRANT_GATE_MISMATCH", grant.blocked_gate, runtimeIdentity.blocked_gate],
    ["GRANT_FAILURE_CODE_MISMATCH", grant.original_failure_code, runtimeIdentity.failure_code],
  ];
  for (const [reason, grantVal, runtimeVal] of checks) {
    if (grantVal == null || runtimeVal == null) {
      return { ok: false, code: "DENIED", reason: "BINDING_FIELD_MISSING" };
    }
    if (String(grantVal) !== String(runtimeVal)) {
      const code = reason === "GRANT_REVISION_MISMATCH" ? "PAUSED" : "DENIED";
      return { ok: false, code, reason };
    }
  }
  if (grant.bound_execution_identity_digest) {
    const digest = executionIdentityDigest(runtimeIdentity);
    if (grant.bound_execution_identity_digest !== digest) {
      return { ok: false, code: "DENIED", reason: "EXECUTION_IDENTITY_DIGEST_MISMATCH" };
    }
  }
  return { ok: true };
}
