import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Crash-safe promotion journal for agent hop V0 (PREPARED → APPLYING → VERIFIED → COMMITTED).
 *
 * Canonical TARGET is not mutated until isolated operative verification passes and
 * target revision freshness is confirmed. Partial APPLYING states pause with recovery.
 */
import { createHash } from "node:crypto";
import {
  closeSync,
  cpSync,
  existsSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { allowedPromotionPaths } from "./opsx-super-implement-dual-write-policy.mjs";
import { posixPath } from "./opsx-apply-pipeline-identity.mjs";
import { recordAgentHopIntegration, loadIntegrationLedger } from "./opsx-super-implement-agent-hop-integration.mjs";

export const TX_SCHEMA = "opsx.super_implement_agent_hop_transaction";
export const TX_SCHEMA_VERSION = 1;

export const TX_PREPARED = "PREPARED";
export const TX_APPLYING = "APPLYING";
export const TX_VERIFIED = "VERIFIED";
export const TX_COMMITTED = "COMMITTED";
export const TX_PAUSED = "PAUSED";

function journalDir(repoRoot, changeRoot) {
  return resourcePath(repoRoot, changeRoot, "opsx", "agent-hop-transactions");
}

export function transactionJournalPath(repoRoot, changeRoot, executionId) {
  return join(journalDir(repoRoot, changeRoot), `${executionId}.json`);
}

function changePromotionLockPath(repoRoot, changeRoot) {
  return resourcePath(repoRoot, changeRoot, "opsx", "agent-hop-promotion.lock");
}

function lockPath(repoRoot, changeRoot, executionId) {
  return join(journalDir(repoRoot, changeRoot), `${executionId}.lock`);
}

function backupRoot(repoRoot, changeRoot, executionId) {
  return join(journalDir(repoRoot, changeRoot), `${executionId}.backup`);
}

function atomicWriteJson(path, value) {
  const dir = dirname(path);
  mkdirSync(dir, { recursive: true });
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
  renameSync(tmp, path);
}

export function loadTransaction(repoRoot, changeRoot, executionId) {
  const path = transactionJournalPath(repoRoot, changeRoot, executionId);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return { corrupt: true, state: TX_PAUSED, execution_id: executionId };
  }
}

/**
 * Exclusive integration lock (fresh process safe).
 */
export function acquireIntegrationLock(repoRoot, changeRoot, executionId) {
  const path = lockPath(repoRoot, changeRoot, executionId);
  mkdirSync(dirname(path), { recursive: true });
  try {
    const fd = openSync(path, "wx");
    writeFileSync(fd, `${process.pid}\n`);
    closeSync(fd);
    return { ok: true, path };
  } catch {
    return {
      ok: false,
      code: "AGENT_HOP_INTEGRATION_LOCK_HELD",
      recovery_contract: {
        code: "AGENT_HOP_INTEGRATION_LOCK_HELD",
        next_action: "wait for concurrent integration or clear stale lock with Owner review",
        resume_requires: ["integration_lock_released"],
      },
    };
  }
}

export function releaseIntegrationLock(repoRoot, changeRoot, executionId) {
  const path = lockPath(repoRoot, changeRoot, executionId);
  if (existsSync(path)) unlinkSync(path);
}

/**
 * Serialize all promotion into canonical TARGET for one Change.
 */
export function acquireChangePromotionLock(repoRoot, changeRoot) {
  const path = changePromotionLockPath(repoRoot, changeRoot);
  mkdirSync(dirname(path), { recursive: true });
  try {
    const fd = openSync(path, "wx");
    writeFileSync(fd, `${process.pid}\n`);
    closeSync(fd);
    return { ok: true, path };
  } catch {
    return {
      ok: false,
      code: "CHANGE_PROMOTION_LOCK_HELD",
      recovery_contract: {
        code: "CHANGE_PROMOTION_LOCK_HELD",
        next_action: "wait for in-flight promotion on this change or Owner lock review",
        resume_requires: ["change_promotion_lock_released"],
      },
    };
  }
}

export function releaseChangePromotionLock(repoRoot, changeRoot) {
  const path = changePromotionLockPath(repoRoot, changeRoot);
  if (existsSync(path)) unlinkSync(path);
}

export function verifyTargetRevisionDigest(repoRoot, changeRoot, stage, expectedDigest) {
  const live = snapshotCanonicalTargetRevision(repoRoot, changeRoot, stage);
  return {
    ok: live.digest === expectedDigest,
    expected_digest: expectedDigest,
    live_digest: live.digest,
    entries: live.entries,
  };
}

function walkRepoFiles(repoRoot, relRoot, out) {
  const absRoot = resourcePath(repoRoot, relRoot);
  if (!existsSync(absRoot)) return;
  function walk(abs, rel) {
    const st = statSync(abs);
    if (st.isDirectory()) {
      for (const name of readdirSync(abs)) walk(join(abs, name), `${rel}/${name}`);
      return;
    }
    out.push(rel.replace(/\\/g, "/"));
  }
  walk(absRoot, relRoot);
}

/**
 * Digest of canonical TARGET files under stage promotion surfaces.
 */
export function snapshotCanonicalTargetRevision(repoRoot, changeRoot, stage) {
  const { change_local, repo_root } = allowedPromotionPaths(changeRoot, stage);
  const roots = [...change_local, ...repo_root];
  const files = [];
  for (const root of roots) {
    if (root.endsWith("/**")) {
      walkRepoFiles(repoRoot, root.slice(0, -3), files);
    } else {
      walkRepoFiles(repoRoot, root, files);
    }
  }
  const unique = [...new Set(files)].sort();
  const entries = [];
  for (const rel of unique) {
    const abs = resourcePath(repoRoot, rel);
    if (!existsSync(abs)) {
      entries.push({ path: rel, sha256: null, missing: true });
      continue;
    }
    const body = readFileSync(abs);
    entries.push({
      path: rel,
      sha256: createHash("sha256").update(body).digest("hex"),
      missing: false,
    });
  }
  const digest = createHash("sha256")
    .update(JSON.stringify(entries), "utf8")
    .digest("hex");
  return { digest, entries, captured_at: new Date().toISOString() };
}

export function evaluateTargetRevisionFresh(repoRoot, changeRoot, stage, expectedDigest) {
  const live = snapshotCanonicalTargetRevision(repoRoot, changeRoot, stage);
  if (live.digest !== expectedDigest) {
    return {
      ok: false,
      code: "TARGET_REVISION_STALE",
      expected_digest: expectedDigest,
      live_digest: live.digest,
      recovery_contract: {
        code: "TARGET_REVISION_STALE",
        next_action: "discard hop; canonical TARGET changed during agent authoring",
        resume_requires: ["fresh_target_revision", "agent_hop_restarted"],
      },
    };
  }
  return { ok: true, digest: live.digest };
}

export function resolveTransactionAtStart(repoRoot, changeRoot, executionId) {
  const ledger = loadIntegrationLedger(repoRoot, changeRoot);
  const committed = ledger.entries?.find(
    (e) => e.execution_id === executionId && e.transaction_state === TX_COMMITTED,
  );
  if (committed) {
    return {
      ok: false,
      code: "AGENT_HOP_DUPLICATE_INTEGRATION",
      prior: committed,
      replay: true,
    };
  }
  const tx = loadTransaction(repoRoot, changeRoot, executionId);
  if (!tx) return { ok: true, resume: false };
  if (tx.state === TX_COMMITTED) {
    return { ok: false, code: "AGENT_HOP_DUPLICATE_INTEGRATION", prior: tx, replay: true };
  }
  if (tx.state === TX_APPLYING || tx.corrupt) {
    return {
      ok: false,
      code: "AGENT_HOP_TRANSACTION_PAUSED",
      transaction: tx,
      recovery_contract: tx.recovery_contract || {
        code: "AGENT_HOP_TRANSACTION_PAUSED",
        next_action: "Owner must restore from backup or discard transaction journal",
        resume_requires: ["transaction_journal_cleared"],
      },
    };
  }
  if (tx.state === TX_PAUSED || tx.state === TX_VERIFIED) {
    return {
      ok: false,
      code: "AGENT_HOP_TRANSACTION_PAUSED",
      transaction: tx,
      recovery_contract: tx.recovery_contract || {
        code: "AGENT_HOP_TRANSACTION_PAUSED",
        next_action: "Owner must clear or resume transaction journal",
        resume_requires: ["transaction_journal_cleared"],
      },
    };
  }
  return { ok: true, resume: false, transaction: tx };
}

function backupCanonicalFile(repoRoot, backupDir, rel) {
  const src = resourcePath(repoRoot, rel);
  const dest = join(backupDir, rel);
  if (!existsSync(src)) return { backed_up: false, rel };
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(src, dest);
  return { backed_up: true, rel };
}

function restoreFromBackup(repoRoot, backupDir, appliedPaths) {
  const restored = [];
  for (const rel of appliedPaths) {
    const backup = join(backupDir, rel);
    const dest = resourcePath(repoRoot, rel);
    if (existsSync(backup)) {
      mkdirSync(dirname(dest), { recursive: true });
      cpSync(backup, dest);
      restored.push(rel);
    } else if (existsSync(dest)) {
      unlinkSync(dest);
      restored.push(rel);
    }
  }
  return restored;
}

function assertRestoredTargetIdentity(repoRoot, changeRoot, stage, prePromotionDigest) {
  const check = verifyTargetRevisionDigest(repoRoot, changeRoot, stage, prePromotionDigest);
  if (!check.ok) {
    return {
      ok: false,
      code: "TARGET_RESTORATION_IDENTITY_FAILED",
      expected_digest: check.expected_digest,
      live_digest: check.live_digest,
    };
  }
  return { ok: true };
}

/**
 * Apply promotion ops after PREPARED + isolated verification.
 */
export function executePromotionTransaction({
  repoRoot,
  isolatedRoot,
  changeRoot,
  stage,
  executionId,
  hopId,
  operations,
  targetRevisionDigest,
  modelRoute,
  sliceId,
  canonicalOperativeVerify = null,
  transactionTestHooks = null,
  prePromotionRevisionDigest = null,
}) {
  const journalPath = transactionJournalPath(repoRoot, changeRoot, executionId);
  const backupDir = backupRoot(repoRoot, changeRoot, executionId);
  let tx = loadTransaction(repoRoot, changeRoot, executionId) || {
    schema: TX_SCHEMA,
    schema_version: TX_SCHEMA_VERSION,
    execution_id: executionId,
  };

  const fresh = evaluateTargetRevisionFresh(repoRoot, changeRoot, stage, targetRevisionDigest);
  if (!fresh.ok) return { ok: false, ...fresh };

  const preDigest =
    prePromotionRevisionDigest ||
    snapshotCanonicalTargetRevision(repoRoot, changeRoot, stage).digest;

  tx = {
    ...tx,
    state: TX_PREPARED,
    execution_id: executionId,
    hop_id: hopId,
    stage,
    slice_id: sliceId,
    target_revision_digest: targetRevisionDigest,
    pre_promotion_revision_digest: preDigest,
    operations,
    prepared_at: new Date().toISOString(),
  };
  atomicWriteJson(journalPath, tx);

  tx.state = TX_APPLYING;
  tx.applying_at = new Date().toISOString();
  atomicWriteJson(journalPath, tx);

  const applied = [];
  let inFlightRel = null;
  try {
    mkdirSync(backupDir, { recursive: true });
    for (const op of operations) {
      const rel = op.path;
      backupCanonicalFile(repoRoot, backupDir, rel);
      const dest = resourcePath(repoRoot, rel);
      if (op.op === "delete") {
        if (existsSync(dest)) unlinkSync(dest);
      } else {
        const src = join(isolatedRoot, rel);
        if (!existsSync(src)) {
          throw new Error(`missing_isolated_source:${rel}`);
        }
        mkdirSync(dirname(dest), { recursive: true });
        cpSync(src, dest, { recursive: statSync(src).isDirectory() });
      }
      inFlightRel = rel;
      if (transactionTestHooks?.crashAfterCopyBeforeJournal === applied.length) {
        throw new Error("TEST_CRASH_BEFORE_APPLIED_PATHS_JOURNAL");
      }
      applied.push(rel);
      inFlightRel = null;
      tx.applied_paths = [...applied];
      atomicWriteJson(journalPath, tx);
      if (transactionTestHooks?.crashAfterFileIndex === applied.length - 1) {
        throw new Error("TEST_CRASH_DURING_FILE_COPY");
      }
    }
  } catch (err) {
    const pathsToRestore = inFlightRel ? [...applied, inFlightRel] : applied;
    const restored = restoreFromBackup(repoRoot, backupDir, pathsToRestore);
    const identity = assertRestoredTargetIdentity(repoRoot, changeRoot, stage, preDigest);
    tx.state = TX_PAUSED;
    tx.paused_at = new Date().toISOString();
    tx.error = String(err?.message || err);
    tx.restored_paths = restored;
    tx.restoration_identity = identity;
    tx.recovery_contract = {
      code: "AGENT_HOP_PROMOTION_PARTIAL_WRITE",
      next_action: "canonical restored from transaction backup; clear journal before retry",
      resume_requires: ["transaction_journal_cleared", "fresh_execution_id"],
    };
    atomicWriteJson(journalPath, tx);
    return {
      ok: false,
      code: identity.ok ? "AGENT_HOP_PROMOTION_PARTIAL_WRITE" : "TARGET_RESTORATION_IDENTITY_FAILED",
      transaction: tx,
      restored,
      restoration_identity: identity,
    };
  }

  if (typeof canonicalOperativeVerify === "function") {
    const operative = canonicalOperativeVerify();
    if (!operative?.authorized) {
      const restored = restoreFromBackup(repoRoot, backupDir, applied);
      tx.state = TX_PAUSED;
      tx.paused_at = new Date().toISOString();
      tx.recovery_contract = {
        code: "CANONICAL_OPERATIVE_REJECTED",
        next_action: "canonical restored; candidate failed post-promotion verification",
        resume_requires: ["transaction_journal_cleared"],
      };
      tx.restored_paths = restored;
      tx.restoration_identity = assertRestoredTargetIdentity(
        repoRoot,
        changeRoot,
        stage,
        preDigest,
      );
      atomicWriteJson(journalPath, tx);
      return {
        ok: false,
        code: operative?.code || "CANONICAL_OPERATIVE_REJECTED",
        operative,
        transaction: tx,
        restored,
        restoration_identity: tx.restoration_identity,
      };
    }
    tx.canonical_operative = operative;
  }

  tx.state = TX_VERIFIED;
  tx.verified_at = new Date().toISOString();
  atomicWriteJson(journalPath, tx);

  if (transactionTestHooks?.crashAfterVerifiedBeforeCommitted) {
    tx.state = TX_PAUSED;
    tx.paused_at = new Date().toISOString();
    tx.recovery_contract = {
      code: "AGENT_HOP_TRANSACTION_PAUSED",
      next_action: "crash after VERIFIED before COMMITTED; Owner must resume or clear journal",
      resume_requires: ["transaction_journal_cleared"],
    };
    atomicWriteJson(journalPath, tx);
    return { ok: false, code: "AGENT_HOP_TRANSACTION_PAUSED", transaction: tx };
  }

  tx.state = TX_COMMITTED;
  tx.committed_at = new Date().toISOString();
  atomicWriteJson(journalPath, tx);

  recordAgentHopIntegration(repoRoot, changeRoot, {
    execution_id: executionId,
    hop_id: hopId,
    stage,
    slice_id: sliceId,
    model_route: modelRoute,
    transaction_state: TX_COMMITTED,
    target_revision_digest: targetRevisionDigest,
  });

  return { ok: true, applied, transaction: tx };
}

export function buildPromotionOperations(diffResult) {
  const ops = [];
  for (const del of diffResult.deleted_repo_rel || []) {
    ops.push({ path: del, op: "delete" });
  }
  for (const ch of diffResult.changed_repo_rel || []) {
    const path = typeof ch === "string" ? ch : ch.path;
    ops.push({ path, op: typeof ch === "string" ? "upsert" : ch.op || "upsert" });
  }
  return ops;
}
