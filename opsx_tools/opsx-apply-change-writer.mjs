import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Exclusive change-root writer lock shared by manual split-APPLY and Super Implement.
 *
 * Single lock file: `<changeRoot>/super-implement/WRITER_LOCK.json`.
 * Acquisition uses O_EXCL (wx). Release requires matching writer_lease_id.
 * No time-based lock stealing.
 */
import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { posixPath } from "./opsx-apply-pipeline-identity.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  enforceMutationAuthority,
  mutationAuthorityBlockedResponse,
} from "./opsx-mutation-authority.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

/** Mandatory independently-reviewed Architecture gate for any APPLY writer lease.
 * The read-only Python checker is part of pinned Core; no network calls.
 * Failure / missing Python / missing Core fail closed. Not a review attestor.
 */
export function checkArchitectureForWriter({ repoRoot, changeRoot, changeId } = {}) {
  const components = String(changeRoot || "").replaceAll("\\", "/").replace(/\/$/, "").split("/");
  const changeIndex = components.lastIndexOf("changes");
  const derivedId = changeIndex >= 0 && components[changeIndex - 1] === "openspec"
    ? components[changeIndex + 1] : null;
  const id = changeId || derivedId;
  if (!derivedId || !id || id !== derivedId || !/^[a-z0-9][a-z0-9-]{0,127}$/.test(id)) {
    return { ok: false, code: "ARCHITECTURE_STAGE_BLOCKED", detail: "unbound_change_identity" };
  }
  if (!repoRoot) {
    return { ok: false, code: "ARCHITECTURE_STAGE_BLOCKED", detail: "repository_root_missing" };
  }
  const checker = resolve(TOOL_DIR, "../tools/openspec/architecture_stage_gate.py");
  if (!existsSync(checker)) {
    return { ok: false, code: "ARCHITECTURE_STAGE_BLOCKED", detail: "checker_missing" };
  }
  const python = process.env.PYTHON && String(process.env.PYTHON).trim();
  const candidates = python
    ? [{ executable: python, prefix: [] }]
    : process.platform === "win32"
      ? [{ executable: "py", prefix: ["-3"] }, { executable: "python", prefix: [] }]
      : [{ executable: "python3", prefix: [] }, { executable: "python", prefix: [] }];
  for (const candidate of candidates) {
    const run = spawnSync(candidate.executable, [
      ...candidate.prefix, checker, "check", "--root", resolve(repoRoot), "--change", id,
    ], { cwd: resolve(repoRoot), encoding: "utf8", timeout: 12000, windowsHide: true });
    if (run.error?.code === "ENOENT") continue;
    if (run.status === 0) {
      try {
        const payload = JSON.parse(run.stdout || "{}");
        if (payload.status === "READY" && payload.change_id === id) {
          return { ok: true, architecture_sha256: payload.architecture_sha256 };
        }
      } catch { /* fail closed */ }
    }
    return {
      ok: false, code: "ARCHITECTURE_STAGE_BLOCKED",
      detail: String(run.stdout || run.stderr || run.error?.message || "checker_failed").slice(0, 1200),
    };
  }
  return { ok: false, code: "ARCHITECTURE_STAGE_BLOCKED", detail: "python_unavailable" };
}

export function computeWriterLeaseId(changeId, planningDigest) {
  return createHash("sha256")
    .update(`${changeId}:${planningDigest ?? ""}:super-writer-v1`, "utf8")
    .digest("hex")
    .slice(0, 16);
}

export const WRITER_LOCK_SCHEMA = "opsx.apply_change_writer_lock";
export const WRITER_LOCK_SCHEMA_VERSION = 1;

export const HOLDER_MANUAL = "MANUAL_SPLIT_APPLY";
export const HOLDER_SUPER = "SUPER_IMPLEMENT";

/** @deprecated use HOLDER_MANUAL — legacy marker holder name */
export const WRITER_HOLDER_MANUAL = HOLDER_MANUAL;
export const WRITER_HOLDER_SUPER = HOLDER_SUPER;

export function writerLockRelPath(changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/super-implement/WRITER_LOCK.json`;
}

/** Legacy manual marker path (read-only compat; not written by V0 tools). */
export function manualApplyMarkerRelPath(changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return `${cr}/super-implement/MANUAL_APPLY_IN_FLIGHT.json`;
}

function nowIso() {
  return new Date().toISOString();
}

function lockAbs(repoRoot, changeRoot) {
  return resourcePath(repoRoot, writerLockRelPath(changeRoot));
}

export function readWriterLock(repoRoot, changeRoot) {
  const abs = lockAbs(repoRoot, changeRoot);
  if (!existsSync(abs)) return null;
  try {
    return JSON.parse(readFileSync(abs, "utf8"));
  } catch {
    return { schema: WRITER_LOCK_SCHEMA, holder: "CORRUPT", parse_error: true };
  }
}

export function inferChangeRootFromArtifactPath(artifactPath) {
  const norm = String(artifactPath).replace(/\\/g, "/");
  const m = norm.match(/(openspec\/changes\/[^/]+)/);
  return m ? m[1] : null;
}

export function newManualWriterSessionId() {
  return randomBytes(16).toString("hex");
}

export function superRunWriterLeaseId(run) {
  if (run?.writer_lease_id) return run.writer_lease_id;
  return computeWriterLeaseId(
    run?.change_id,
    run?.execution_identity?.planning_digest,
  );
}

function legacyManualMarkerActive(repoRoot, changeRoot) {
  const manualPath = resourcePath(repoRoot, manualApplyMarkerRelPath(changeRoot));
  if (!existsSync(manualPath)) return null;
  try {
    const manual = JSON.parse(readFileSync(manualPath, "utf8"));
    if (manual?.active === true) return manual;
  } catch {
    return { active: true, corrupt: true };
  }
  return null;
}

/**
 * Fail-closed concurrency check (read-only).
 */
export function detectChangeWriterConflict({
  repoRoot,
  changeRoot,
  holder,
  writerLeaseId = null,
} = {}) {
  const legacy = legacyManualMarkerActive(repoRoot, changeRoot);
  if (legacy && holder === HOLDER_SUPER) {
    return {
      ok: false,
      code: "MANUAL_WRITER_IN_FLIGHT",
      recovery_contract: {
        code: "MANUAL_WRITER_IN_FLIGHT",
        next_action:
          "clear legacy MANUAL_APPLY_IN_FLIGHT.json after reconcile or migrate to WRITER_LOCK",
        resume_requires: ["manual_writer_cleared"],
      },
      manual: legacy,
    };
  }
  const lock = readWriterLock(repoRoot, changeRoot);
  if (!lock) return { ok: true };
  if (lock.parse_error || lock.holder === "CORRUPT") {
    return { ok: false, code: "WRITER_LOCK_CORRUPT", lock };
  }
  if (lock.holder === HOLDER_SUPER && holder === HOLDER_MANUAL) {
    return { ok: false, code: "MANUAL_BLOCKED_BY_SUPER", lock };
  }
  if (lock.holder === HOLDER_MANUAL && holder === HOLDER_SUPER) {
    return { ok: false, code: "SUPER_BLOCKED_BY_MANUAL", lock };
  }
  if (
    writerLeaseId &&
    lock.writer_lease_id === writerLeaseId &&
    lock.holder === holder
  ) {
    return { ok: true, lock, idempotent: true };
  }
  if (holder === HOLDER_SUPER && lock.holder === HOLDER_SUPER && writerLeaseId) {
    if (lock.writer_lease_id !== writerLeaseId) {
      return { ok: false, code: "SUPER_WRITER_LOCK_CONFLICT", lock };
    }
    return { ok: true, lock, idempotent: true };
  }
  if (holder === HOLDER_MANUAL && lock.holder === HOLDER_MANUAL && writerLeaseId) {
    if (lock.writer_lease_id !== writerLeaseId) {
      return { ok: false, code: "MANUAL_WRITER_LOCK_HELD", lock };
    }
    return { ok: true, lock, idempotent: true };
  }
  if (lock.holder && lock.holder !== holder) {
    return { ok: false, code: "FOREIGN_WRITER_LOCK", lock };
  }
  return { ok: false, code: "WRITER_LOCK_HELD", lock };
}

function writeLockExclusive(abs, payload) {
  const body = `${JSON.stringify(payload, null, 2)}\n`;
  mkdirSync(dirname(abs), { recursive: true });
  try {
    writeFileSync(abs, body, { flag: "wx" });
    return { ok: true, created: true };
  } catch (err) {
    if (err?.code !== "EEXIST") throw err;
    return { ok: false, created: false };
  }
}

/**
 * Acquire manual split-APPLY writer lock (short or session-scoped lease).
 */
function assertMutationAuthorityForWriter(repoRoot, entrypointId) {
  const auth = enforceMutationAuthority(repoRoot, entrypointId);
  if (!auth.ok) {
    return mutationAuthorityBlockedResponse(auth);
  }
  return { ok: true };
}

export function acquireManualChangeWriterLock({
  repoRoot,
  changeRoot,
  changeId,
  planningDigest = null,
  writerSessionId = null,
  primitive = null,
} = {}) {
  const adoption = assertMutationAuthorityForWriter(
    repoRoot,
    "node.apply_change_writer.acquire",
  );
  if (!adoption.ok) {
    return adoption;
  }
  const architecture = checkArchitectureForWriter({ repoRoot, changeRoot, changeId });
  if (!architecture.ok) return architecture;
  const session = writerSessionId || newManualWriterSessionId();
  const pre = detectChangeWriterConflict({
    repoRoot,
    changeRoot,
    holder: HOLDER_MANUAL,
    writerLeaseId: session,
  });
  if (!pre.ok) return pre;
  if (pre.idempotent) {
    return { ok: true, writer_session_id: session, lock: pre.lock, idempotent: true };
  }
  const abs = lockAbs(repoRoot, changeRoot);
  const payload = {
    schema: WRITER_LOCK_SCHEMA,
    schema_version: WRITER_LOCK_SCHEMA_VERSION,
    holder: HOLDER_MANUAL,
    writer_lease_id: session,
    change_id: changeId,
    change_root: posixPath(changeRoot),
    planning_digest: planningDigest,
    primitive: primitive || null,
    acquired_at: nowIso(),
  };
  const created = writeLockExclusive(abs, payload);
  if (created.ok) {
    return { ok: true, writer_session_id: session, lock: payload };
  }
  const winner = readWriterLock(repoRoot, changeRoot);
  if (
    winner?.holder === HOLDER_MANUAL &&
    winner.writer_lease_id === session
  ) {
    return { ok: true, writer_session_id: session, lock: winner, idempotent: true };
  }
  return detectChangeWriterConflict({
    repoRoot,
    changeRoot,
    holder: HOLDER_MANUAL,
    writerLeaseId: session,
  });
}

/**
 * Acquire Super Implement writer lock (RUN-bound lease).
 */
export function acquireSuperChangeWriterLock({
  repoRoot,
  changeRoot,
  run,
} = {}) {
  const adoption = assertMutationAuthorityForWriter(
    repoRoot,
    "node.apply_change_writer.acquire",
  );
  if (!adoption.ok) {
    return adoption;
  }
  const architecture = checkArchitectureForWriter({ repoRoot, changeRoot, changeId: run?.change_id });
  if (!architecture.ok) return architecture;
  const lease = superRunWriterLeaseId(run);
  const pre = detectChangeWriterConflict({
    repoRoot,
    changeRoot,
    holder: HOLDER_SUPER,
    writerLeaseId: lease,
  });
  if (!pre.ok) return pre;
  if (pre.idempotent) {
    return { ok: true, lock: pre.lock, path: writerLockRelPath(changeRoot), idempotent: true };
  }
  const abs = lockAbs(repoRoot, changeRoot);
  const payload = {
    schema: WRITER_LOCK_SCHEMA,
    schema_version: WRITER_LOCK_SCHEMA_VERSION,
    holder: HOLDER_SUPER,
    writer_lease_id: lease,
    run_fingerprint: lease,
    change_id: run.change_id,
    change_root: posixPath(changeRoot),
    planning_digest: run.execution_identity?.planning_digest,
    acquired_at: nowIso(),
  };
  const created = writeLockExclusive(abs, payload);
  if (created.ok) {
    return { ok: true, lock: payload, path: writerLockRelPath(changeRoot) };
  }
  const winner = readWriterLock(repoRoot, changeRoot);
  if (winner?.holder === HOLDER_SUPER && winner.writer_lease_id === lease) {
    return {
      ok: true,
      lock: winner,
      path: writerLockRelPath(changeRoot),
      idempotent: true,
    };
  }
  const again = detectChangeWriterConflict({
    repoRoot,
    changeRoot,
    holder: HOLDER_SUPER,
    writerLeaseId: lease,
  });
  if (!again.ok) return again;
  return { ok: false, code: "SUPER_WRITER_LOCK_HELD", lock: winner };
}

/**
 * Release lock only when caller proves lease ownership (no stealing).
 */
export function releaseChangeWriterLock({
  repoRoot,
  changeRoot,
  writerLeaseId,
  holder,
} = {}) {
  if (!writerLeaseId) {
    return { ok: false, code: "WRITER_RELEASE_LEASE_REQUIRED" };
  }
  const lock = readWriterLock(repoRoot, changeRoot);
  if (!lock) {
    return { ok: true, released: false, reason: "no_lock" };
  }
  if (lock.writer_lease_id !== writerLeaseId) {
    return { ok: false, code: "WRITER_RELEASE_FORBIDDEN", lock };
  }
  const effectiveHolder = holder || lock.holder;
  if (effectiveHolder && lock.holder !== effectiveHolder) {
    return { ok: false, code: "WRITER_RELEASE_HOLDER_MISMATCH", lock };
  }
  const abs = lockAbs(repoRoot, changeRoot);
  const current = readWriterLock(repoRoot, changeRoot);
  if (!current || current.writer_lease_id !== writerLeaseId) {
    return { ok: false, code: "WRITER_RELEASE_STALE", lock: current };
  }
  unlinkSync(abs);
  return { ok: true, released: true };
}

/**
 * Super release when PAUSED and no unresolved in-flight dispatch.
 */
export function releaseSuperChangeWriterLock({
  repoRoot,
  changeRoot,
  run,
} = {}) {
  if (run?.in_flight_dispatch) {
    return {
      ok: false,
      code: "SUPER_RELEASE_DEFERRED_IN_FLIGHT",
      recovery_contract: {
        code: "SUPER_RELEASE_DEFERRED_IN_FLIGHT",
        next_action:
          "reconcile in_flight_dispatch from receipts or executor before releasing Super writer lock",
        resume_requires: ["in_flight_reconciled"],
      },
    };
  }
  const lease = superRunWriterLeaseId(run);
  const lock = readWriterLock(repoRoot, changeRoot);
  if (!lock) {
    return { ok: true, released: false, reason: "no_lock" };
  }
  if (lock.holder !== HOLDER_SUPER) {
    return { ok: false, code: "FOREIGN_WRITER_LOCK", lock };
  }
  if (lock.writer_lease_id !== lease) {
    return { ok: false, code: "SUPER_WRITER_LOCK_CONFLICT", lock };
  }
  return releaseChangeWriterLock({
    repoRoot,
    changeRoot,
    writerLeaseId: lease,
    holder: HOLDER_SUPER,
  });
}

/**
 * Guard for tool-side effect writes (manual primitives).
 *
 * Backward compatibility: when no session is supplied and no writer lock exists,
 * acquire an ephemeral manual lease for a single guarded mutation (released via
 * finishManualWriterMutationGuard). Explicit --writer-session remains supported
 * for multi-hop manual workflows.
 */
export function assertManualWriterLeaseForMutation({
  repoRoot,
  changeRoot,
  writerSessionId,
  changeId = null,
  planningDigest = null,
  primitive = null,
} = {}) {
  const adoption = assertMutationAuthorityForWriter(
    repoRoot,
    "node.apply_change_writer.mutation_guard",
  );
  if (!adoption.ok) {
    return adoption;
  }
  const architecture = checkArchitectureForWriter({ repoRoot, changeRoot, changeId });
  if (!architecture.ok) return architecture;
  const explicitSession =
    writerSessionId || process.env.OPSX_CHANGE_WRITER_SESSION || null;
  const existingLock = readWriterLock(repoRoot, changeRoot);
  let session = explicitSession;
  let compatEphemeral = false;
  if (!session) {
    if (existingLock) {
      return {
        ok: false,
        code: "MANUAL_WRITER_SESSION_REQUIRED",
        recovery_contract: {
          code: "MANUAL_WRITER_SESSION_REQUIRED",
          next_action:
            "writer lock already held — pass --writer-session / OPSX_CHANGE_WRITER_SESSION matching the lock, or reconcile after Super/manual completion",
          resume_requires: ["manual_writer_session_matches_lock"],
        },
        lock: existingLock,
      };
    }
    session = newManualWriterSessionId();
    compatEphemeral = true;
  }
  if (
    !existingLock ||
    existingLock.holder !== HOLDER_MANUAL ||
    existingLock.writer_lease_id !== session
  ) {
    const acquired = acquireManualChangeWriterLock({
      repoRoot,
      changeRoot,
      changeId,
      planningDigest,
      writerSessionId: session,
      primitive,
    });
    if (!acquired.ok) return acquired;
  }
  return {
    ok: true,
    writer_session_id: session,
    lock: readWriterLock(repoRoot, changeRoot),
    compat_ephemeral: compatEphemeral,
    release_after: compatEphemeral,
  };
}

/**
 * Release ephemeral compat lease after a single guarded write completes.
 */
export function finishManualWriterMutationGuard({
  repoRoot,
  changeRoot,
  guard,
} = {}) {
  if (!guard?.ok || !guard.release_after || !guard.writer_session_id) {
    return { ok: true, skipped: true };
  }
  return releaseChangeWriterLock({
    repoRoot,
    changeRoot,
    writerLeaseId: guard.writer_session_id,
    holder: HOLDER_MANUAL,
  });
}

export function changeRootWriterStatus({ repoRoot, changeRoot } = {}) {
  return {
    lock: readWriterLock(repoRoot, changeRoot),
    legacy_manual_marker: legacyManualMarkerActive(repoRoot, changeRoot),
    lock_path: writerLockRelPath(changeRoot),
  };
}

function parseCli(argv) {
  const args = [...argv];
  const out = {
    command: null,
    repoRoot: DEFAULT_ROOT,
    changeRoot: null,
    session: null,
    changeId: null,
  };
  const repoFlag = args.indexOf("--repo");
  if (repoFlag >= 0) {
    out.repoRoot = args[repoFlag + 1];
    args.splice(repoFlag, 2);
  }
  const cr = args.indexOf("--change-root");
  if (cr >= 0) {
    out.changeRoot = args[cr + 1];
    args.splice(cr, 2);
  }
  const sess = args.indexOf("--writer-session");
  if (sess >= 0) {
    out.session = args[sess + 1];
    args.splice(sess, 2);
  }
  const cid = args.indexOf("--change-id");
  if (cid >= 0) {
    out.changeId = args[cid + 1];
    args.splice(cid, 2);
  }
  out.command = args[0];
  return out;
}

const isMain =
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const cli = parseCli(process.argv.slice(2));
  if (!cli.changeRoot) {
    process.stdout.write(JSON.stringify({ ok: false, code: "CHANGE_ROOT_REQUIRED" }) + "\n");
    process.exit(1);
  }
  if (cli.command === "acquire") {
    const r = acquireManualChangeWriterLock({
      repoRoot: cli.repoRoot,
      changeRoot: cli.changeRoot,
      changeId: cli.changeId || cli.changeRoot.split("/").pop(),
      writerSessionId: cli.session,
    });
    process.stdout.write(JSON.stringify(r, null, 2) + "\n");
    process.exit(r.ok ? 0 : 1);
  }
  if (cli.command === "release") {
    const r = releaseChangeWriterLock({
      repoRoot: cli.repoRoot,
      changeRoot: cli.changeRoot,
      writerLeaseId: cli.session,
      holder: HOLDER_MANUAL,
    });
    process.stdout.write(JSON.stringify(r, null, 2) + "\n");
    process.exit(r.ok ? 0 : 1);
  }
  if (cli.command === "status") {
    process.stdout.write(
      JSON.stringify(changeRootWriterStatus({ repoRoot: cli.repoRoot, changeRoot: cli.changeRoot }), null, 2) + "\n",
    );
    process.exit(0);
  }
  process.stdout.write(JSON.stringify({ ok: false, code: "UNKNOWN_COMMAND" }) + "\n");
  process.exit(1);
}
