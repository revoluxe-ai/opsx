import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Post-IMPLEMENT test-contract recovery completion (generation-bound, fail-closed).
 *
 * Executable spine: CLASSIFICATION → AUTHORITY → NEW GENERATION → FRESH EVIDENCE
 * → IMPL_OK → SUCCESSOR PLAN. PATH A pauses with Owner decision packets; PATH B
 * requires hermetic signed Owner decisions before allocating recovery generations.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { spawnSync } from "node:child_process";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ALREADY_SATISFIED,
  classifyGreenEvidenceFreshness,
  classifyPostImplementGreenRecovery,
  collectPostImplementRecoveryBlocked,
  collectImplementProductionPaths,
  computeLiveFrozenTestContentIdentity,
  implementFrozenTestIdentityMatchesRedBytes,
  computeLiveProductSourceInputIdentity,
  legacyFrozenTestContentIdentityFromRed,
  loadBoundedVerifyBindingFromDisk,
  posixPath,
  postImplementRecoveryBlocksOperativeRedRoute,
  resolveRecoveryBlockedGreenPick,
  selectNextGreenableSlice,
  selectNextPipelineStage,
  frozenRedTestCommandsAllPass,
} from "./opsx-apply-pipeline-identity.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import {
  GAP_STALE_EVIDENCE,
  classifySplitApplyImplGaps,
  projectSplitApplyTaskCompletion,
  recommendSplitApplyImplNextAction,
} from "./opsx-apply-verify-completion.mjs";
import { selectSuperImplementTransition } from "./opsx-super-implement.mjs";

export const RECOVERY_OWNER_DECISION_SCHEMA =
  "opsx.post_implement_test_contract_recovery_owner_decision";
export const RECOVERY_LEDGER_SCHEMA = "opsx.post_implement_test_contract_recovery_ledger";
export const RECOVERY_GENERATION_SCHEMA =
  "opsx.post_implement_test_contract_recovery_generation";
export const RECOVERY_FRESH_EVIDENCE_SCHEMA =
  "opsx.post_implement_test_contract_recovery_fresh_evidence";

export const FRESH_EVIDENCE_PASS = "EVIDENCE_PASS";
export const FRESH_EVIDENCE_FAIL = "EVIDENCE_FAIL";
export const GENERATION_PUBLISHED = "PUBLISHED";

export const RECOVERY_PHASE_CLASSIFICATION = "CLASSIFICATION";
export const RECOVERY_PHASE_AUTHORITY = "AUTHORITY";
export const RECOVERY_PHASE_NEW_GENERATION = "NEW_GENERATION";
export const RECOVERY_PHASE_FRESH_EVIDENCE = "FRESH_EVIDENCE";
export const RECOVERY_PHASE_IMPL_OK = "IMPL_OK";
export const RECOVERY_PHASE_SUCCESSOR_PLAN = "SUCCESSOR_PLAN";

export const RECOVERY_PATH_A = "PATH_A";
export const RECOVERY_PATH_B = "PATH_B";
export const RECOVERY_PATH_NATIVE_REFRESH = "NATIVE_GREEN_REFRESH";
export const RECOVERY_PATH_OPERATIVE_RE_RED = "OPERATIVE_RE_RED";

/** Native publish did not commit a PUBLISHED successor (skipped or no disk carrier). */
export const RECOVERY_SUCCESSOR_PUBLICATION_SKIPPED = "RECOVERY_SUCCESSOR_PUBLICATION_SKIPPED";
/** Native publish ran but disk proof of SUCCESSOR_PLAN_CREATED is missing or rejected. */
export const RECOVERY_SUCCESSOR_PUBLICATION_FAILED = "RECOVERY_SUCCESSOR_PUBLICATION_FAILED";

const PUBLICATION_STATE_PUBLISHED = "PUBLISHED";
const PUBLICATION_CODE_CREATED = "SUCCESSOR_PLAN_CREATED";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_REPO_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const BOUNDED_VERIFY_PUBLISH_SUCCESSOR_CLI = join(
  TOOL_DIR,
  "opsx-bounded-verify-publish-successor-cli.py",
);

const PAUSE_CODES_PATH_A = new Set([
  "FROZEN_TEST_PATH_MISSING",
  "IDENTITY_ALGORITHM_UNRESOLVED",
  "TEST_CONTRACT_REBASELINE_REQUIRED",
]);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((e) => stableStringify(e)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`)
    .join(",")}}`;
}

function sha256Hex(input) {
  return createHash("sha256").update(input, "utf8").digest("hex");
}

function greenReceiptAbsPath(repoRoot, changeRoot, sliceId) {
  return resourcePath(repoRoot, posixPath(changeRoot || ""), "apply-green", sliceId, "GREEN.json");
}

/**
 * Bind source revision and on-disk GREEN bytes into fresh evidence.
 * Later completion rejects drift of either anchor.
 */
export function anchorRecoveryEvidenceBindings(
  freshEvidence,
  { red, repoRoot, changeRoot, sliceId } = {},
) {
  if (!freshEvidence || typeof freshEvidence !== "object") return freshEvidence;
  const anchored = { ...freshEvidence };
  if (red?.source_revision) {
    anchored.bound_source_revision = String(red.source_revision);
  }
  if (repoRoot && changeRoot && sliceId) {
    const greenPath = greenReceiptAbsPath(repoRoot, changeRoot, sliceId);
    if (existsSync(greenPath)) {
      anchored.bound_green_receipt_sha256 = sha256Hex(readFileSync(greenPath, "utf8"));
    }
  }
  return anchored;
}

function canonicalFreshEvidenceForCompare(evidence) {
  if (!isObject(evidence)) return "";
  const { recorded_at, recovery_attempt_id, ...rest } = evidence;
  return stableStringify(rest);
}

/**
 * Deterministic execution revision for a recovery generation (test bytes + commands).
 */
export function computeRecoveryExecutionRevision({ red, implement, repoRoot } = {}) {
  const live = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red?.test_paths,
    fixture_paths: red?.fixture_paths,
  });
  const productionPaths = collectImplementProductionPaths(implement);
  const liveProduct = computeLiveProductSourceInputIdentity(repoRoot, {
    production_paths: productionPaths,
  });
  const body = {
    kind: "post_implement_recovery_execution_revision",
    red_apply_design_identity: red?.apply_design_identity || null,
    red_test_content_identity: red?.test_content_identity || null,
    implement_frozen_test_content_identity:
      implement?.frozen_test_content_identity ||
      implement?.frozen_test_identity_after ||
      implement?.red_identity?.test_content_identity ||
      null,
    test_paths: [...(red?.test_paths || [])].map((p) => posixPath(p)).sort(),
    fixture_paths: [...(red?.fixture_paths || [])].map((p) => posixPath(p)).sort(),
    live_test_content_identity: live.ok ? live.identity : null,
    production_paths: productionPaths,
    live_product_source_input_identity: liveProduct.ok ? liveProduct.identity : null,
  };
  return { execution_revision: sha256Hex(stableStringify(body)), body };
}

/**
 * Fail-closed: CLI --verify-command must match frozen manifest slice entry when present.
 */
export function assertRecoveryVerifyCommandAuthorized({
  verifyCommand,
  manifestSliceEntry = null,
  red,
  implement,
  repoRoot,
} = {}) {
  if (!verifyCommand) {
    return { ok: true, skipped: true };
  }
  if (manifestSliceEntry?.verify_commands?.length) {
    if (!manifestSliceEntry.verify_commands.includes(verifyCommand)) {
      return { ok: false, code: "RECOVERY_VERIFY_COMMAND_MANIFEST_DRIFT" };
    }
  }
  const revision = computeRecoveryExecutionRevision({ red, implement, repoRoot });
  return {
    ok: true,
    execution_revision: revision.execution_revision,
    manifest_bound: Boolean(manifestSliceEntry?.verify_commands?.length),
  };
}

/**
 * Operative verification gate: identity-only receipts cannot satisfy IMPL_OK.
 */
export function assertRecoveryVerificationGateAccepted(freshEvidence) {
  if (!freshEvidence || freshEvidence.status !== FRESH_EVIDENCE_PASS) {
    return { ok: false, code: "RECOVERY_EVIDENCE_NOT_PASS" };
  }
  const commandExecutions = (freshEvidence.executions || []).filter(
    (entry) => entry.kind === "command_execution" && entry.exit_code === 0,
  );
  if (commandExecutions.length > 0 && freshEvidence.verification_gate_accepted === true) {
    return { ok: true, command_executions: commandExecutions };
  }
  if (
    freshEvidence.kind === "native_identity_refresh" &&
    freshEvidence.verification_gate_accepted === true
  ) {
    return { ok: true, via: "native_identity_refresh" };
  }
  if (freshEvidence.verification_gate_accepted !== true) {
    return { ok: false, code: "RECOVERY_VERIFICATION_GATE_NOT_ACCEPTED" };
  }
  if (commandExecutions.length === 0) {
    return { ok: false, code: "RECOVERY_IDENTITY_ONLY_NOT_OPERATIVE_GATE" };
  }
  return { ok: true, command_executions: commandExecutions };
}

/**
 * Generation + execution-revision binding for persisted fresh evidence (fail-closed).
 */
export function validateGenerationBoundRecoveryFreshEvidence({
  freshEvidence,
  recoveryLedger,
  generation = null,
} = {}) {
  const gate = assertRecoveryVerificationGateAccepted(freshEvidence);
  if (!gate.ok) {
    return gate;
  }
  if (!recoveryLedger?.generation_lineage_id) {
    return { ok: false, code: "RECOVERY_GENERATION_UNBOUND" };
  }
  if (freshEvidence.generation_lineage_id !== recoveryLedger.generation_lineage_id) {
    return { ok: false, code: "RECOVERY_EVIDENCE_LINEAGE_MISMATCH" };
  }
  if (
    generation &&
    generation.generation_lineage_id !== recoveryLedger.generation_lineage_id
  ) {
    return { ok: false, code: "RECOVERY_PUBLISHED_GENERATION_MISMATCH" };
  }
  const ledgerRev = recoveryLedger.execution_revision || null;
  const evidenceRev = freshEvidence.execution_revision || null;
  const generationRev = generation?.lineage_body?.execution_revision || null;
  if (!ledgerRev || !evidenceRev || ledgerRev !== evidenceRev) {
    return { ok: false, code: "RECOVERY_EXECUTION_REVISION_UNBOUND" };
  }
  if (generationRev && generationRev !== evidenceRev) {
    return { ok: false, code: "RECOVERY_GENERATION_EXECUTION_REVISION_MISMATCH" };
  }
  return { ok: true, verification_gate_accepted: true };
}

/**
 * Published recovery generation is current only with generation-bound fresh evidence
 * on disk (ledger.impl_ok alone is not evidence).
 */
export function isRecoveryGenerationCurrent({
  ledger,
  generation,
  freshEvidence = null,
} = {}) {
  if (!ledger?.generation_lineage_id) {
    return false;
  }
  if (
    !generation ||
    generation.state !== GENERATION_PUBLISHED ||
    generation.generation_lineage_id !== ledger.generation_lineage_id
  ) {
    return false;
  }
  if (!freshEvidence) {
    return false;
  }
  const bound = validateGenerationBoundRecoveryFreshEvidence({
    freshEvidence,
    recoveryLedger: ledger,
    generation,
  });
  return bound.ok === true;
}

/**
 * Recovered slice is CURRENT GREEN only when freshness holds or operative gate
 * re-proves live bytes under the published generation (no stale-gap bypass).
 */
export function isRecoveredSliceGenerationBoundGreenCurrent({
  sliceId,
  green,
  upstream = {},
  red,
  implement,
  repoRoot,
  recoveryLedger,
  freshEvidence,
} = {}) {
  if (!sliceId || !recoveryLedger || !freshEvidence) {
    return { ok: false, code: "RECOVERY_GREEN_BINDING_CONTEXT_MISSING" };
  }
  const bound = validateGenerationBoundRecoveryFreshEvidence({
    freshEvidence,
    recoveryLedger,
  });
  if (!bound.ok) {
    return bound;
  }
  const freshness = classifyGreenEvidenceFreshness(green, {
    ...upstream,
    design: upstream.design,
    scaffold: upstream.scaffold,
    red: upstream.red || red,
    implement: upstream.implement || implement,
    repoRoot,
  });
  if (freshness.current) {
    return { ok: true, via: "current_green_receipt" };
  }
  if (
    freshEvidence.kind === "native_identity_refresh" &&
    freshEvidence.verification_gate_accepted === true
  ) {
    const identityExec = (freshEvidence.executions || []).find(
      (entry) => entry.kind === "identity_check",
    );
    if (
      identityExec?.legacy_digest &&
      identityExec?.implement_freeze &&
      identityExec.legacy_digest === identityExec.implement_freeze
    ) {
      return { ok: true, via: "native_identity_refresh_generation_bound" };
    }
    return { ok: false, code: "RECOVERY_NATIVE_REFRESH_IDENTITY_MISMATCH" };
  }
  const live = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red?.test_paths,
    fixture_paths: red?.fixture_paths,
  });
  const post = freshEvidence.post_execution_test_content_identity;
  if (!live.ok || !post || live.identity !== post) {
    return { ok: false, code: "RECOVERY_POST_EXECUTION_IDENTITY_DRIFT" };
  }
  return { ok: true, via: "operative_gate_generation_bound" };
}

function atomicWriteJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
  renameSync(tmp, path);
}

/**
 * Durable recovery attempt directory (interruption-safe checkpoint root).
 */
export function recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId) {
  const cr = posixPath(changeRoot || "").replace(/\/$/, "");
  return resourcePath(repoRoot, cr, "opsx", "post-implement-recovery", recoveryAttemptId);
}

export function recoveryLedgerPath(repoRoot, changeRoot, recoveryAttemptId) {
  return join(recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId), "LEDGER.json");
}

export function recoveryGenerationPath(repoRoot, changeRoot, recoveryAttemptId) {
  return join(recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId), "GENERATION.json");
}

export function recoveryFreshEvidencePath(repoRoot, changeRoot, recoveryAttemptId) {
  return join(
    recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId),
    "FRESH-EVIDENCE.json",
  );
}

export function recoverySuccessorPlanPath(repoRoot, changeRoot, recoveryAttemptId) {
  return join(recoveryAttemptDir(repoRoot, changeRoot, recoveryAttemptId), "SUCCESSOR-PLAN.json");
}

/**
 * Durable LEDGER.json for recovery attempts (pause + terminal checkpoints).
 */
export function persistRecoveryLedger({ repoRoot, changeRoot, recoveryAttemptId, ledger } = {}) {
  if (!changeRoot || !recoveryAttemptId || !ledger) {
    return { ok: false, code: "RECOVERY_LEDGER_PERSISTENCE_INCOMPLETE" };
  }
  const ledgerPath = recoveryLedgerPath(repoRoot, changeRoot, recoveryAttemptId);
  if (existsSync(ledgerPath)) {
    let existing;
    try {
      existing = JSON.parse(readFileSync(ledgerPath, "utf8"));
    } catch {
      return { ok: false, code: "RECOVERY_LEDGER_CORRUPT" };
    }
    if (
      existing.terminal === true &&
      ledger.terminal === true &&
      existing.generation_lineage_id === ledger.generation_lineage_id &&
      existing.phase === ledger.phase
    ) {
      return { ok: true, idempotent: true, path: ledgerPath, ledger: existing };
    }
  }
  atomicWriteJson(ledgerPath, ledger);
  return { ok: true, path: ledgerPath, ledger };
}

/**
 * Invoke Python bounded_verify_supersession.publish_successor_plan when a FROZEN plan exists.
 */
export function publishBoundedVerifySuccessorPlanFromDisk({
  changeId,
  repoRoot = DEFAULT_REPO_ROOT,
  changeRoot = null,
} = {}) {
  if (!changeId) {
    return { ok: false, code: "CHANGE_ID_MISSING" };
  }
  const root = changeRoot ? resourcePath(repoRoot, posixPath(changeRoot)) : repoRoot;
  const python = process.env.PYTHON || "python3";
  const run = spawnSync(python, [BOUNDED_VERIFY_PUBLISH_SUCCESSOR_CLI], {
    input: JSON.stringify({ change_id: changeId, root: String(root) }),
    encoding: "utf8",
    cwd: repoRoot,
  });
  if (run.status !== 0 || !run.stdout?.trim()) {
    return {
      ok: false,
      code: "BOUNDED_VERIFY_PUBLISH_SUCCESSOR_FAILED",
      stderr: run.stderr || "",
      stdout: run.stdout || "",
    };
  }
  try {
    const body = JSON.parse(run.stdout.trim());
    if (body.skipped) {
      return { ok: true, skipped: true, reason: body.reason || "PLAN_NOT_FOUND" };
    }
    if (!body.ok) {
      return { ok: false, code: body.code || "PUBLISH_SUCCESSOR_REJECTED", detail: body };
    }
    return { ok: true, skipped: false, supersession: body };
  } catch {
    return { ok: false, code: "BOUNDED_VERIFY_PUBLISH_SUCCESSOR_PARSE_FAILED" };
  }
}

function boundedVerifyChangeDir(repoRoot, changeRoot, changeId) {
  const root = changeRoot ? resourcePath(repoRoot, posixPath(changeRoot)) : resolve(repoRoot);
  return resourcePath(root, "openspec", "changes", String(changeId), "bounded-verify");
}

/**
 * Disk proof that publish_successor_plan archived the predecessor and wrote
 * plan, ledger, and GENERATION. A command string is not proof.
 */
function readNativePublishedSupersession({ changeId, repoRoot, changeRoot } = {}) {
  if (!changeId) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  const bv = boundedVerifyChangeDir(repoRoot, changeRoot, changeId);
  if (!existsSync(bv)) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_SKIPPED };
  }
  const generationPath = join(bv, "GENERATION.json");
  const planPath = join(bv, "plan.json");
  const ledgerPath = join(bv, "ledger.json");
  if (!existsSync(generationPath) || !existsSync(planPath) || !existsSync(ledgerPath)) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  let generation;
  let plan;
  try {
    generation = JSON.parse(readFileSync(generationPath, "utf8"));
    plan = JSON.parse(readFileSync(planPath, "utf8"));
  } catch {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  const oldPlanId = generation?.supersedes_plan_id;
  if (typeof oldPlanId !== "string" || !oldPlanId) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  const archivePlan = join(bv, "archive", oldPlanId, "plan.json");
  if (!existsSync(archivePlan)) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  if (!plan?.plan_id || String(generation.plan_id || "") !== String(plan.plan_id)) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  return {
    ok: true,
    publication_state: PUBLICATION_STATE_PUBLISHED,
    publication_code: PUBLICATION_CODE_CREATED,
    disk_proof: {
      predecessor_plan_id: oldPlanId,
      carrier_plan_id: String(plan.plan_id),
      generation: generation.generation,
      predecessor_archive_plan_path: archivePlan,
    },
  };
}

function receiptIsCreatedPublication(body) {
  if (!isObject(body) || body.skipped === true || body.ok === false) return false;
  if (body.publication_state !== PUBLICATION_STATE_PUBLISHED) return false;
  if (body.publication_code !== PUBLICATION_CODE_CREATED) return false;
  const archive = body.disk_proof?.predecessor_archive_plan_path;
  return typeof archive === "string" && archive.length > 0 && existsSync(archive);
}

function receiptMatchesDisk(receipt, disk) {
  if (!receiptIsCreatedPublication(receipt) || !disk?.ok) return false;
  const claimed = resolve(receipt.disk_proof.predecessor_archive_plan_path);
  const live = resolve(disk.disk_proof.predecessor_archive_plan_path);
  if (claimed !== live) return false;
  if (String(receipt.disk_proof.predecessor_plan_id || "") !== String(disk.disk_proof.predecessor_plan_id)) {
    return false;
  }
  if (String(receipt.disk_proof.carrier_plan_id || "") !== String(disk.disk_proof.carrier_plan_id)) {
    return false;
  }
  return true;
}

/**
 * Require native PUBLISHED / SUCCESSOR_PLAN_CREATED proof for this recovery generation.
 * SUCCESSOR-PLAN.json command text and a cached successor_plan.ok are not proof.
 */
function assertCurrentGenerationSuccessorPublished({
  changeId,
  design,
  repoRoot,
  changeRoot,
  recoveryLedger,
  planBody = null,
} = {}) {
  const id = changeId || design?.change_id || null;
  const disk = readNativePublishedSupersession({
    changeId: id,
    repoRoot,
    changeRoot,
  });
  if (!disk.ok) return disk;
  const attemptId = recoveryLedger?.recovery_attempt_id || null;
  const lineage = recoveryLedger?.generation_lineage_id || null;
  if (!attemptId || !lineage || !changeRoot) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  let stored = planBody;
  if (!stored) {
    const planPath = recoverySuccessorPlanPath(repoRoot, changeRoot, attemptId);
    if (!existsSync(planPath)) {
      return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
    }
    try {
      stored = JSON.parse(readFileSync(planPath, "utf8"));
    } catch {
      return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
    }
  }
  if (stored.generation_lineage_id !== lineage) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED };
  }
  if (!receiptMatchesDisk(stored.supersession_proof, disk)) {
    return { ok: false, code: RECOVERY_SUCCESSOR_PUBLICATION_FAILED, disk };
  }
  return { ok: true, disk, supersession_proof: stored.supersession_proof };
}

/**
 * Materialize SUCCESSOR-PLAN.json only after native PUBLISHED supersession.
 * Skipped publish, failed publish, and a receipt without an on-disk predecessor
 * archive are not publication success.
 */
export function persistRecoverySuccessorPlan({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  successorMechanism,
  design,
} = {}) {
  if (!changeRoot || !recoveryAttemptId || !successorMechanism?.ok) {
    return { ok: false, code: "SUCCESSOR_PLAN_PERSISTENCE_INCOMPLETE" };
  }
  const planPath = recoverySuccessorPlanPath(repoRoot, changeRoot, recoveryAttemptId);
  const changeId = design?.change_id || null;
  if (existsSync(planPath)) {
    try {
      const existing = JSON.parse(readFileSync(planPath, "utf8"));
      if (existing.generation_lineage_id !== successorMechanism.generation_lineage_id) {
        return { ok: false, code: "SUCCESSOR_PLAN_LINEAGE_CONFLICT" };
      }
      const proven = assertCurrentGenerationSuccessorPublished({
        changeId,
        design,
        repoRoot,
        changeRoot,
        recoveryLedger: {
          recovery_attempt_id: recoveryAttemptId,
          generation_lineage_id: successorMechanism.generation_lineage_id,
        },
        planBody: existing,
      });
      if (!proven.ok) {
        return { ok: false, code: proven.code || RECOVERY_SUCCESSOR_PUBLICATION_FAILED, path: planPath };
      }
      return {
        ok: true,
        idempotent: true,
        path: planPath,
        successor_plan: existing,
        supersession: { ok: true, skipped: false, supersession: existing.supersession_proof },
      };
    } catch {
      return { ok: false, code: "SUCCESSOR_PLAN_CORRUPT" };
    }
  }
  const supersession = publishBoundedVerifySuccessorPlanFromDisk({
    changeId,
    repoRoot,
    changeRoot,
  });
  const disk = readNativePublishedSupersession({ changeId, repoRoot, changeRoot });
  const cliBody = supersession.supersession;
  if (supersession.skipped || !receiptMatchesDisk(cliBody, disk)) {
    return {
      ok: false,
      code: supersession.skipped
        ? RECOVERY_SUCCESSOR_PUBLICATION_SKIPPED
        : RECOVERY_SUCCESSOR_PUBLICATION_FAILED,
      supersession,
      cause: supersession.code || supersession.reason || disk.code || null,
    };
  }
  const body = {
    ...successorMechanism,
    generation_lineage_id: successorMechanism.generation_lineage_id,
    bounded_verify_supersession: supersession,
    supersession_proof: cliBody,
    published_at: new Date().toISOString(),
  };
  atomicWriteJson(planPath, body);
  return { ok: true, path: planPath, successor_plan: body, supersession };
}

/**
 * Load persisted recovery attempt artifacts (ledger + generation + fresh evidence).
 */
export function loadPersistedRecoveryAttempt({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
} = {}) {
  if (!recoveryAttemptId || !changeRoot) {
    return { ok: false, code: "RECOVERY_PERSISTENCE_CONTEXT_MISSING" };
  }
  const ledgerPath = recoveryLedgerPath(repoRoot, changeRoot, recoveryAttemptId);
  if (!existsSync(ledgerPath)) {
    return { ok: false, code: "RECOVERY_LEDGER_MISSING" };
  }
  let ledger;
  try {
    ledger = JSON.parse(readFileSync(ledgerPath, "utf8"));
  } catch {
    return { ok: false, code: "RECOVERY_LEDGER_CORRUPT" };
  }
  let generation = null;
  const genPath = recoveryGenerationPath(repoRoot, changeRoot, recoveryAttemptId);
  if (existsSync(genPath)) {
    try {
      generation = JSON.parse(readFileSync(genPath, "utf8"));
    } catch {
      return { ok: false, code: "RECOVERY_GENERATION_CORRUPT" };
    }
  }
  let freshEvidence = null;
  const evPath = recoveryFreshEvidencePath(repoRoot, changeRoot, recoveryAttemptId);
  if (existsSync(evPath)) {
    try {
      freshEvidence = JSON.parse(readFileSync(evPath, "utf8"));
    } catch {
      return { ok: false, code: "RECOVERY_FRESH_EVIDENCE_CORRUPT" };
    }
  }
  return { ok: true, ledger, generation, freshEvidence };
}

/**
 * Atomic generation publication (idempotent on matching lineage).
 */
export function publishRecoveryGeneration({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  generationLineageId,
  lineageBody,
  ledger,
} = {}) {
  if (!changeRoot || !recoveryAttemptId || !generationLineageId) {
    return { ok: false, code: "RECOVERY_PUBLICATION_INCOMPLETE" };
  }
  const genPath = recoveryGenerationPath(repoRoot, changeRoot, recoveryAttemptId);
  if (existsSync(genPath)) {
    const existing = JSON.parse(readFileSync(genPath, "utf8"));
    if (existing.generation_lineage_id === generationLineageId) {
      const existingBody = stableStringify(existing.lineage_body || {});
      const incomingBody = stableStringify(lineageBody || {});
      if (existingBody !== incomingBody) {
        return { ok: false, code: "RECOVERY_GENERATION_BODY_COLLISION" };
      }
      return { ok: true, idempotent: true, generation: existing, path: genPath };
    }
    return { ok: false, code: "RECOVERY_GENERATION_LINEAGE_CONFLICT" };
  }
  const publication = {
    schema: RECOVERY_GENERATION_SCHEMA,
    schema_version: 1,
    state: GENERATION_PUBLISHED,
    generation_lineage_id: generationLineageId,
    lineage_body: lineageBody,
    recovery_attempt_id: recoveryAttemptId,
    published_at: new Date().toISOString(),
  };
  atomicWriteJson(genPath, publication);
  if (ledger) {
    atomicWriteJson(recoveryLedgerPath(repoRoot, changeRoot, recoveryAttemptId), ledger);
  }
  return { ok: true, generation: publication, path: genPath };
}

/**
 * Persist fresh-evidence receipt after native execution (atomic).
 */
export function persistRecoveryFreshEvidence({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  freshEvidence,
  ledger,
} = {}) {
  if (!changeRoot || !recoveryAttemptId || !freshEvidence) {
    return { ok: false, code: "RECOVERY_EVIDENCE_PERSISTENCE_INCOMPLETE" };
  }
  const evPath = recoveryFreshEvidencePath(repoRoot, changeRoot, recoveryAttemptId);
  const body = {
    schema: RECOVERY_FRESH_EVIDENCE_SCHEMA,
    schema_version: 1,
    ...freshEvidence,
    recovery_attempt_id: recoveryAttemptId,
    recorded_at: new Date().toISOString(),
  };
  if (existsSync(evPath)) {
    let existing;
    try {
      existing = JSON.parse(readFileSync(evPath, "utf8"));
    } catch {
      return { ok: false, code: "RECOVERY_FRESH_EVIDENCE_CORRUPT" };
    }
    if (canonicalFreshEvidenceForCompare(existing) !== canonicalFreshEvidenceForCompare(body)) {
      return { ok: false, code: "RECOVERY_FRESH_EVIDENCE_IMMUTABLE" };
    }
    return { ok: true, idempotent: true, path: evPath, fresh_evidence: existing };
  }
  atomicWriteJson(evPath, body);
  if (ledger) {
    atomicWriteJson(recoveryLedgerPath(repoRoot, changeRoot, recoveryAttemptId), ledger);
  }
  return { ok: true, path: evPath, fresh_evidence: body };
}

/**
 * Native adoption / identity verification execution for recovery (not Owner decision alone).
 */
export function executeRecoveryFreshEvidence({
  recoveryPath,
  red,
  implement,
  generationLineageId,
  executionRevision = null,
  boundSliceId = null,
  repoRoot,
  runCommand = runFrozenRedCommand,
  timeoutMs = 120000,
  ownerDecisionValidated = false,
  identityAlgorithmDriftOnly = false,
} = {}) {
  if (!isObject(red) || !isObject(implement)) {
    return {
      status: FRESH_EVIDENCE_FAIL,
      code: "RECOVERY_EVIDENCE_PRECONDITIONS_MISSING",
      generation_lineage_id: generationLineageId,
      executions: [],
    };
  }
  const freeze =
    implement.frozen_test_content_identity ||
    implement.frozen_test_identity_after ||
    implement.red_identity?.test_content_identity;
  if (recoveryPath === RECOVERY_PATH_NATIVE_REFRESH) {
    const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
    const live = computeLiveFrozenTestContentIdentity(repoRoot, {
      test_paths: red.test_paths,
      fixture_paths: red.fixture_paths,
    });
    const pass =
      legacy.ok &&
      legacy.digest &&
      freeze &&
      legacy.digest === freeze &&
      live.ok &&
      (live.identity === freeze ||
        live.identity === legacy.digest ||
        identityAlgorithmDriftOnly === true);
    return {
      status: pass ? FRESH_EVIDENCE_PASS : FRESH_EVIDENCE_FAIL,
      kind: "native_identity_refresh",
      generation_lineage_id: generationLineageId,
      execution_revision: executionRevision,
      bound_slice_id: boundSliceId,
      verification_gate_accepted: pass,
      identity_only: true,
      executions: [
        {
          kind: "identity_check",
          legacy_digest: legacy.digest || null,
          live_identity: live.ok ? live.identity : null,
          implement_freeze: freeze || null,
        },
      ],
    };
  }
  if (
    recoveryPath === RECOVERY_PATH_B &&
    Array.isArray(red.test_commands) &&
    red.test_commands.length > 0
  ) {
    const executions = [];
    for (const cmd of red.test_commands) {
      const live = runCommand(cmd, { cwd: repoRoot, timeoutMs });
      const exitCode = live.exit_code ?? (live.ok === false ? 1 : 0);
      executions.push({
        kind: "command_execution",
        command: cmd,
        exit_code: exitCode,
        execution_mechanism: live.execution_mechanism || "native",
      });
      if (exitCode !== 0) {
        return {
          status: FRESH_EVIDENCE_FAIL,
          kind: "live_pass_rebaseline_command_execution",
          generation_lineage_id: generationLineageId,
          executions,
        };
      }
    }
    return {
      status: FRESH_EVIDENCE_PASS,
      kind: "live_pass_rebaseline_command_execution",
      generation_lineage_id: generationLineageId,
      execution_revision: executionRevision,
      bound_slice_id: boundSliceId,
      verification_gate_accepted: true,
      executions,
    };
  }
  const commands = [
    ...(red.recovery_adoption_verification_commands || []),
    ...(red.post_implement_verification_commands || []),
  ].filter(Boolean);
  const executions = [];
  if (commands.length === 0) {
    const live = computeLiveFrozenTestContentIdentity(repoRoot, {
      test_paths: red.test_paths,
      fixture_paths: red.fixture_paths,
    });
    const legacy = legacyFrozenTestContentIdentityFromRed(red, repoRoot);
    let pass = live.ok && freeze && live.identity === freeze;
    if (ownerDecisionValidated && recoveryPath === RECOVERY_PATH_B) {
      pass = legacy.ok && legacy.digest === freeze;
    }
    return {
      status: pass ? FRESH_EVIDENCE_PASS : FRESH_EVIDENCE_FAIL,
      kind: "adoption_identity_verification",
      generation_lineage_id: generationLineageId,
      execution_revision: executionRevision,
      bound_slice_id: boundSliceId,
      verification_gate_accepted: false,
      identity_only: true,
      executions: [
        {
          kind: "identity_check",
          live_identity: live.ok ? live.identity : null,
          implement_freeze: freeze || null,
        },
      ],
    };
  }
  for (const cmd of commands) {
    const live = runCommand(cmd, { cwd: repoRoot, timeoutMs });
    const exitCode = live.exit_code ?? (live.ok === false ? 1 : 0);
    executions.push({
      kind: "command_execution",
      command: cmd,
      exit_code: exitCode,
      execution_mechanism: live.execution_mechanism || "native",
    });
    if (exitCode !== 0) {
      return {
        status: FRESH_EVIDENCE_FAIL,
        kind: "adoption_command_execution",
        generation_lineage_id: generationLineageId,
        executions,
      };
    }
  }
  const postLive = computeLiveFrozenTestContentIdentity(repoRoot, {
    test_paths: red.test_paths,
    fixture_paths: red.fixture_paths,
  });
  return {
    status: FRESH_EVIDENCE_PASS,
    kind: "adoption_command_execution",
    generation_lineage_id: generationLineageId,
    execution_revision: executionRevision,
    bound_slice_id: boundSliceId,
    verification_gate_accepted: true,
    identity_only: false,
    post_execution_test_content_identity: postLive.ok ? postLive.identity : null,
    executions,
  };
}

/**
 * Recompute IMPL_OK from persisted evidence; reject caller-supplied apply_complete.
 */
export function recomputeRecoveryImplOk({
  design,
  greenBySlice = {},
  upstreamBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  freshEvidence,
  recoveryLedger,
  generation = null,
  repoRoot = null,
  changeRoot = null,
  callerTaskProjection = null,
  recoveredSliceId = null,
  rejectForgedCallerApplyComplete = true,
  cohortRecovery = null,
} = {}) {
  const generationBound = validateGenerationBoundRecoveryFreshEvidence({
    freshEvidence,
    recoveryLedger,
    generation,
  });
  if (!generationBound.ok) {
    return generationBound;
  }
  const sliceId = recoveredSliceId || recoveryLedger?.slice_id || null;
  const red = sliceId ? redBySlice?.[sliceId] : null;
  if (
    freshEvidence.bound_source_revision &&
    red?.source_revision &&
    String(red.source_revision) !== String(freshEvidence.bound_source_revision)
  ) {
    return { ok: false, code: "RECOVERY_SOURCE_REVISION_DRIFT" };
  }
  if (freshEvidence.bound_green_receipt_sha256 && repoRoot && changeRoot && sliceId) {
    const greenPath = greenReceiptAbsPath(repoRoot, changeRoot, sliceId);
    if (!existsSync(greenPath)) {
      return { ok: false, code: "RECOVERY_GREEN_RECEIPT_MISSING" };
    }
    const liveGreen = sha256Hex(readFileSync(greenPath, "utf8"));
    if (liveGreen !== freshEvidence.bound_green_receipt_sha256) {
      return { ok: false, code: "RECOVERY_GREEN_RECEIPT_DIVERGED" };
    }
  }
  if (sliceId && redBySlice?.[sliceId] && implementBySlice?.[sliceId] && repoRoot) {
    const { execution_revision: liveRevision } = computeRecoveryExecutionRevision({
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      repoRoot,
    });
    if (
      recoveryLedger?.execution_revision &&
      liveRevision &&
      liveRevision !== recoveryLedger.execution_revision
    ) {
      return { ok: false, code: "RECOVERY_PREDECESSOR_EXECUTION_REVISION_DRIFT" };
    }
  }
  const recomputed = projectSplitApplyTaskCompletion({
    design,
    greenBySlice,
    upstreamBySlice,
    durableProjectionPresent: false,
  });
  if (
    rejectForgedCallerApplyComplete &&
    callerTaskProjection?.apply_complete === true &&
    recomputed.apply_complete !== true
  ) {
    return {
      ok: false,
      code: "CALLER_APPLY_COMPLETE_REJECTED",
      task_projection: recomputed,
    };
  }
  const gaps = classifySplitApplyImplGaps({
    design,
    taskProjection: recomputed,
    redBySlice,
  });
  const blocking = (gaps.blocking || []).filter((g) => {
    if (g.class !== GAP_STALE_EVIDENCE) {
      return true;
    }
    const covering = g.covering_slice_ids || (g.slice_id ? [g.slice_id] : []);
    if (cohortRecovery?.manifest_digest) {
      return covering.some((sid) => {
        const receipt = cohortRecovery.slice_receipts?.[sid];
        if (cohortRecovery.unaffected_slice_ids?.includes(sid)) {
          return cohortRecovery.unaffected_freshness?.[sid]?.ok !== true;
        }
        if (cohortRecovery.affected_slice_ids?.includes(sid)) {
          return receipt?.fresh_evidence_status !== FRESH_EVIDENCE_PASS;
        }
        return true;
      });
    }
    if (
      sliceId &&
      covering.includes(sliceId)
    ) {
      const bound = isRecoveredSliceGenerationBoundGreenCurrent({
        sliceId,
        green: greenBySlice?.[sliceId],
        upstream: upstreamBySlice?.[sliceId] || {},
        red: redBySlice?.[sliceId],
        implement: implementBySlice?.[sliceId],
        repoRoot,
        recoveryLedger,
        freshEvidence,
      });
      return !bound.ok;
    }
    return true;
  });
  if (blocking.length > 0) {
    return {
      ok: false,
      code: "IMPL_GAPS_REMAIN",
      gaps: { ...gaps, blocking },
      task_projection: recomputed,
    };
  }
  return { ok: true, impl_ok: true, task_projection: recomputed, gaps: { ...gaps, blocking } };
}

/**
 * Bounded-verify successor with native consumer wiring (not command string alone).
 * IMPL_OK authorization requires on-disk PUBLISHED supersession for this generation.
 * Pass requirePublishedDiskProof false only to draft the mechanism before persist.
 */
export function buildBoundedVerifySuccessorMechanism({
  design,
  changeId,
  changeRoot,
  repoRoot,
  recoveryLedger,
  freshEvidence,
  generation = null,
  taskProjection,
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
  callerTaskProjection = null,
  requirePublishedDiskProof = true,
  cohortRecovery = null,
} = {}) {
  const impl = recomputeRecoveryImplOk({
    design,
    greenBySlice,
    upstreamBySlice,
    redBySlice,
    implementBySlice,
    freshEvidence,
    recoveryLedger,
    generation,
    repoRoot,
    changeRoot,
    callerTaskProjection,
    recoveredSliceId: recoveryLedger?.slice_id,
    cohortRecovery,
  });
  if (!impl.ok) {
    return { ok: false, code: impl.code, detail: impl };
  }
  let publication = null;
  if (requirePublishedDiskProof !== false) {
    publication = assertCurrentGenerationSuccessorPublished({
      changeId,
      design,
      repoRoot,
      changeRoot,
      recoveryLedger,
    });
    if (!publication.ok) {
      return { ok: false, code: publication.code, detail: publication };
    }
  }
  const id = changeId || design?.change_id || "<change>";
  const cr =
    changeRoot ||
    (design?.change_id ? `openspec/changes/${design.change_id}` : null);
  const binding = cr ? loadBoundedVerifyBindingFromDisk(repoRoot, cr) : null;
  return {
    ok: true,
    phase: RECOVERY_PHASE_SUCCESSOR_PLAN,
    mechanism: "bounded_verify_native_consumer",
    command: `/opsx-verify mode=bounded change=${id}`,
    native_invocation: cr
      ? {
          tool: "opsx_tools/opsx-bounded-verify-green-freshness.mjs",
          argv: ["--change-root", cr],
        }
      : null,
    bounded_verify_binding: binding,
    generation_lineage_id: recoveryLedger?.generation_lineage_id,
    plan_fingerprint_alias: false,
    impl_ok_recomputed: true,
    published_disk_supersession: publication?.ok === true,
    task_projection: impl.task_projection,
  };
}

/**
 * Completion consumer: load disk evidence, recompute IMPL_OK, plan bounded successor.
 */
export function evaluateRecoveryCompletionConsumer({
  design,
  repoRoot,
  changeRoot,
  recoveryAttemptId,
  callerTaskProjection = null,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
  cohortRecovery = null,
} = {}) {
  const loaded = loadPersistedRecoveryAttempt({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
  });
  if (!loaded.ok) {
    return { ok: false, code: loaded.code };
  }
  if (!loaded.freshEvidence) {
    return { ok: false, code: "RECOVERY_FRESH_EVIDENCE_MISSING", ledger: loaded.ledger };
  }
  const freshEvidence = loaded.freshEvidence;
  const successor = buildBoundedVerifySuccessorMechanism({
    design,
    changeId: design?.change_id,
    changeRoot,
    repoRoot,
    recoveryLedger: loaded.ledger,
    freshEvidence,
    generation: loaded.generation,
    taskProjection: callerTaskProjection,
    redBySlice,
    implementBySlice,
    greenBySlice,
    upstreamBySlice,
    callerTaskProjection,
    cohortRecovery,
  });
  if (!successor.ok || successor.published_disk_supersession !== true) {
    return {
      ok: false,
      code: successor.code || RECOVERY_SUCCESSOR_PUBLICATION_FAILED,
      ledger: loaded.ledger,
      fresh_evidence: freshEvidence,
      detail: successor.detail,
    };
  }
  return {
    ok: true,
    impl_ok: true,
    ledger: loaded.ledger,
    generation: loaded.generation,
    fresh_evidence: freshEvidence,
    successor_plan: successor,
  };
}

function finalizeRecoveryLedgerPhases({
  design,
  nextLedger,
  freshEvidence,
  generation = null,
  changeRoot,
  repoRoot,
  recoveryAttemptId,
  callerTaskProjection,
  redBySlice,
  implementBySlice,
  greenBySlice,
  upstreamBySlice,
  publishSuccessor = true,
} = {}) {
  if (freshEvidence.status !== FRESH_EVIDENCE_PASS) {
    nextLedger.phase = RECOVERY_PHASE_FRESH_EVIDENCE;
    nextLedger.fresh_evidence_record = freshEvidence;
    return {
      ok: false,
      code: "RECOVERY_EVIDENCE_FAILED",
      ledger: nextLedger,
      fresh_evidence: freshEvidence,
    };
  }
  if (publishSuccessor === false) {
    nextLedger.phase = RECOVERY_PHASE_FRESH_EVIDENCE;
    nextLedger.fresh_evidence_record = freshEvidence;
    return { ok: true, ledger: nextLedger, fresh_evidence: freshEvidence, slice_evidence_only: true };
  }
  const impl = recomputeRecoveryImplOk({
    design,
    greenBySlice,
    upstreamBySlice,
    redBySlice,
    implementBySlice,
    freshEvidence,
    recoveryLedger: nextLedger,
    generation,
    repoRoot,
    callerTaskProjection: null,
    recoveredSliceId: nextLedger.slice_id,
    rejectForgedCallerApplyComplete: false,
  });
  if (!impl.ok) {
    nextLedger.phase = RECOVERY_PHASE_FRESH_EVIDENCE;
    nextLedger.fresh_evidence_record = freshEvidence;
    nextLedger.impl_ok = false;
    return { ok: false, code: impl.code, ledger: nextLedger, fresh_evidence: freshEvidence };
  }
  nextLedger.fresh_evidence_record = freshEvidence;
  nextLedger.terminal = false;
  nextLedger.impl_ok = false;
  const failSuccessorPublication = (code) => {
    nextLedger.impl_ok = false;
    nextLedger.terminal = false;
    if (nextLedger.phase === RECOVERY_PHASE_IMPL_OK) {
      nextLedger.phase = RECOVERY_PHASE_FRESH_EVIDENCE;
    }
    return { ok: false, code, ledger: nextLedger, fresh_evidence: freshEvidence };
  };
  const successorDraft = buildBoundedVerifySuccessorMechanism({
    design,
    changeRoot,
    repoRoot,
    recoveryLedger: nextLedger,
    freshEvidence,
    generation,
    taskProjection: impl.task_projection,
    redBySlice,
    implementBySlice,
    greenBySlice,
    upstreamBySlice,
    callerTaskProjection: null,
    requirePublishedDiskProof: false,
  });
  if (!successorDraft.ok) {
    return failSuccessorPublication(successorDraft.code);
  }
  if (!changeRoot || !recoveryAttemptId) {
    return failSuccessorPublication(RECOVERY_SUCCESSOR_PUBLICATION_SKIPPED);
  }
  const published = persistRecoverySuccessorPlan({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    successorMechanism: successorDraft,
    design,
  });
  if (!published.ok) {
    return failSuccessorPublication(published.code);
  }
  const successor = buildBoundedVerifySuccessorMechanism({
    design,
    changeRoot,
    repoRoot,
    recoveryLedger: nextLedger,
    freshEvidence,
    generation,
    taskProjection: impl.task_projection,
    redBySlice,
    implementBySlice,
    greenBySlice,
    upstreamBySlice,
    callerTaskProjection: null,
  });
  if (!successor.ok || successor.published_disk_supersession !== true) {
    return failSuccessorPublication(successor.code || RECOVERY_SUCCESSOR_PUBLICATION_FAILED);
  }
  nextLedger.phase = RECOVERY_PHASE_SUCCESSOR_PLAN;
  nextLedger.impl_ok = true;
  nextLedger.impl_ok_recomputed = true;
  nextLedger.successor_plan = successor;
  nextLedger.terminal = true;
  nextLedger.successor_plan_file = published.path;
  nextLedger.bounded_verify_supersession = published.supersession;
  if (changeRoot && recoveryAttemptId) {
    persistRecoveryFreshEvidence({
      repoRoot,
      changeRoot,
      recoveryAttemptId,
      freshEvidence,
      ledger: nextLedger,
    });
  }
  return { ok: true, ledger: nextLedger, fresh_evidence: freshEvidence };
}

/**
 * Idempotent replay of a terminal recovery attempt (disk checkpoint only).
 */
export function replayPostImplementRecovery({
  repoRoot,
  changeRoot,
  recoveryAttemptId,
} = {}) {
  const loaded = loadPersistedRecoveryAttempt({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
  });
  if (!loaded.ok) {
    return { ok: false, code: loaded.code };
  }
  if (loaded.ledger?.terminal === true) {
    return { ok: true, idempotent: true, ledger: loaded.ledger };
  }
  return { ok: false, code: "RECOVERY_NOT_TERMINAL", ledger: loaded.ledger };
}

/**
 * Invoke bounded-verify native successor CLI from persisted SUCCESSOR-PLAN.json.
 */
export function invokePostImplementRecoverySuccessor({
  repoRoot = DEFAULT_REPO_ROOT,
  changeRoot,
  recoveryAttemptId,
  forceChangeRoot = null,
} = {}) {
  const planPath = recoverySuccessorPlanPath(repoRoot, changeRoot, recoveryAttemptId);
  if (!existsSync(planPath)) {
    return { ok: false, code: "SUCCESSOR_PLAN_MISSING" };
  }
  let successor;
  try {
    successor = JSON.parse(readFileSync(planPath, "utf8"));
  } catch {
    return { ok: false, code: "SUCCESSOR_PLAN_CORRUPT" };
  }
  const invocation = successor.native_invocation;
  if (!invocation?.tool || !Array.isArray(invocation.argv)) {
    return { ok: false, code: "SUCCESSOR_NATIVE_INVOCATION_MISSING" };
  }
  const effectiveRoot = forceChangeRoot || changeRoot;
  const argv = invocation.argv.map((arg) =>
    arg === "--change-root" ? arg : String(arg),
  );
  const crIdx = argv.indexOf("--change-root");
  if (crIdx >= 0 && argv[crIdx + 1]) {
    argv[crIdx + 1] = effectiveRoot;
  } else {
    argv.push("--change-root", effectiveRoot);
  }
  const toolAbs = resourcePath(repoRoot, invocation.tool);
  if (!existsSync(toolAbs)) {
    return { ok: false, code: "SUCCESSOR_NATIVE_TOOL_MISSING", tool: invocation.tool };
  }
  const designPath = resourcePath(repoRoot, posixPath(effectiveRoot), "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    return { ok: false, code: "SUCCESSOR_CHANGE_ROOT_MISSING" };
  }
  const run = spawnSync(process.execPath, [toolAbs, ...argv], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  if (run.status !== 0) {
    return {
      ok: false,
      code: "SUCCESSOR_NATIVE_INVOCATION_FAILED",
      exit_code: run.status,
      stdout: run.stdout,
      stderr: run.stderr,
    };
  }
  let body = null;
  try {
    body = JSON.parse((run.stdout || "").trim());
  } catch {
    body = { raw_stdout: run.stdout };
  }
  return { ok: true, native_result: body, change_root: effectiveRoot };
}

/**
 * Recovery path from native classification (does not substitute plan_fingerprint).
 */
export function classifyPostImplementRecoveryPath(
  recovery,
  { red = null, implement = null, repoRoot = DEFAULT_REPO_ROOT } = {},
) {
  if (!recovery) return { path: null, authorized_generation: false };
  if (recovery.disposition === "GREEN_REFRESH_AUTHORIZED") {
    return { path: RECOVERY_PATH_NATIVE_REFRESH, authorized_generation: false };
  }
  if (recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") {
    if (
      postImplementRecoveryBlocksOperativeRedRoute({
        red,
        implement,
        recovery,
        repoRoot,
      })
    ) {
      return { path: RECOVERY_PATH_B, authorized_generation: false };
    }
    return { path: RECOVERY_PATH_OPERATIVE_RE_RED, authorized_generation: false };
  }
  if (PAUSE_CODES_PATH_A.has(recovery.code)) {
    const path =
      recovery.code === "IDENTITY_ALGORITHM_UNRESOLVED" ||
      recovery.code === "TEST_CONTRACT_REBASELINE_REQUIRED"
        ? RECOVERY_PATH_B
        : RECOVERY_PATH_A;
    return { path, authorized_generation: false };
  }
  return { path: null, authorized_generation: false };
}

/**
 * Semantic equivalence gate: auto when algorithm drift is provable; else Owner packet.
 */
export function evaluateSemanticEquivalenceGate({
  recovery,
  red,
  implement,
  repoRoot,
} = {}) {
  if (!recovery) {
    return { outcome: "NOT_APPLICABLE", owner_decision_required: false };
  }
  if (
    recovery.disposition === "GREEN_REFRESH_AUTHORIZED" &&
    recovery.identity_algorithm_drift_only === true
  ) {
    return {
      outcome: "AUTO_PROVEN",
      owner_decision_required: false,
      detail: "legacy_bytes_match_implement_freeze_canonical_algorithm_drifts_only",
    };
  }
  if (recovery.code === "IDENTITY_ALGORITHM_UNRESOLVED") {
    return {
      outcome: "OWNER_DECISION_REQUIRED",
      owner_decision_required: true,
      detail: "receipt_test_content_identity_unaligned_with_frozen_bytes",
      identity_algorithm_drift_only: recovery.identity_algorithm_drift_only === true,
    };
  }
  if (recovery.code === "FROZEN_TEST_PATH_MISSING") {
    return {
      outcome: "OWNER_CONTRACT_REALIGN_REQUIRED",
      owner_decision_required: true,
      detail: "frozen_test_path_missing_on_disk",
    };
  }
  if (recovery.code === "TEST_CONTRACT_REBASELINE_REQUIRED") {
    if (red && implement && repoRoot) {
      const frozen = implementFrozenTestIdentityMatchesRedBytes(red, implement, repoRoot);
      if (frozen.ok) {
        return {
          outcome: "AUTO_PROVEN",
          owner_decision_required: false,
          detail: "frozen_bytes_match_accepted_contract_design_future_slice_repair",
        };
      }
    }
    return {
      outcome: "DESIGN_FUTURE_SLICE_REPAIR",
      owner_decision_required: false,
      detail: "rebaseline_via_design_future_slice_repair_not_owner_packet",
    };
  }
  if (recovery.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") {
    if (red && frozenRedTestCommandsAllPass(red, repoRoot)) {
      return {
        outcome: "AUTO_PROVEN",
        owner_decision_required: false,
        detail: "live_frozen_commands_pass_operative_re_red_blocked",
      };
    }
    return {
      outcome: "OPERATIVE_RE_RED_REQUIRED",
      owner_decision_required: false,
      detail: "confirmed_byte_mutation_requires_operative_red",
    };
  }
  return { outcome: "NOT_APPLICABLE", owner_decision_required: false };
}

/**
 * Generation lineage id (explicitly not a plan_id / plan_fingerprint alias).
 */
export function computeRecoveryGenerationLineage({
  changeId,
  sliceId,
  recoveryCode,
  planFingerprint = null,
  priorGenerationLineageId = null,
  ownerDecisionId = null,
  executionRevision = null,
  recoveryManifestDigest = null,
  designSupersessionId = null,
  inputIdentities = null,
} = {}) {
  const lineageCore = {
    kind: "post_implement_test_contract_recovery_generation",
    change_id: String(changeId || ""),
    slice_id: String(sliceId || ""),
    recovery_code: String(recoveryCode || ""),
    prior_generation_lineage_id: priorGenerationLineageId || null,
    owner_decision_id: ownerDecisionId || null,
    execution_revision: executionRevision || null,
    recovery_manifest_digest: recoveryManifestDigest || null,
    design_supersession_id: designSupersessionId || null,
    input_identities: inputIdentities || null,
  };
  const body = { ...lineageCore };
  const generation_lineage_id = sha256Hex(stableStringify(lineageCore));
  const plan_alias = planFingerprint
    ? generation_lineage_id === String(planFingerprint)
    : false;
  return {
    generation_lineage_id,
    plan_fingerprint_alias: plan_alias,
    body,
  };
}

/**
 * Канонический lineage для slice: owner decision + execution revision + input identities.
 */
export function computeRecoveryGenerationLineageForSlice({
  changeId,
  sliceId,
  recoveryCode,
  ownerDecisionId,
  red,
  implement,
  repoRoot,
  recoveryManifestDigest = null,
  designSupersessionId = null,
  planFingerprint = null,
} = {}) {
  const { execution_revision: executionRevision } = computeRecoveryExecutionRevision({
    red,
    implement,
    repoRoot,
  });
  return computeRecoveryGenerationLineage({
    changeId,
    sliceId,
    recoveryCode,
    planFingerprint,
    ownerDecisionId,
    executionRevision,
    recoveryManifestDigest,
    designSupersessionId,
    inputIdentities: {
      red_test_content_identity: red?.test_content_identity || null,
      implement_frozen_test_content_identity:
        implement?.frozen_test_content_identity ||
        implement?.frozen_test_identity_after ||
        null,
    },
  });
}

/**
 * Owner-readable decision packet for PATH A / ambiguous PATH B (no generation minted).
 */
export function buildOwnerDecisionPacket({
  changeId,
  sliceId,
  recovery,
  semanticGate,
  generationLineage = null,
  recoveryPath = null,
} = {}) {
  const pathInfo = recoveryPath || classifyPostImplementRecoveryPath(recovery);
  return {
    schema: "opsx.post_implement_test_contract_recovery_owner_packet",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    recovery_code: recovery?.code || null,
    recovery_path: pathInfo.path,
    semantic_equivalence: semanticGate,
    generation_lineage_id: generationLineage?.generation_lineage_id || null,
    unauthorized_without_owner_signature: true,
    recommended_owner_action:
      pathInfo.path === RECOVERY_PATH_A
        ? "/opsx-update or explicit test-contract realign before verify"
        : "sign opsx.post_implement_test_contract_recovery_owner_decision",
    resume_requires: ["owner_decision_packet_acknowledged"],
  };
}

export function computeRecoveryOwnerDecisionSignatureBody(decision) {
  const body = { ...decision };
  delete body.signature_hmac_sha256;
  return stableStringify(body);
}

export function signPostImplementRecoveryOwnerDecision(decision, secret) {
  const payload = computeRecoveryOwnerDecisionSignatureBody(decision);
  return createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}

/**
 * Validate hermetic Owner decision (PATH B generation authority).
 */
export function validatePostImplementRecoveryOwnerDecision(
  decision,
  {
    secret,
    changeId,
    sliceId,
    recoveryCode,
    generationLineageId,
    recoveryPath = null,
    nowMs = Date.now(),
  } = {},
) {
  if (!isObject(decision)) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_CORRUPT" };
  }
  if (decision.schema !== RECOVERY_OWNER_DECISION_SCHEMA || decision.schema_version !== 1) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_SCHEMA_INVALID" };
  }
  if (decision.adoption_not_operative_red !== true) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_ADOPTION_FLAG_REQUIRED" };
  }
  if (!secret || secret.length < 16) {
    return { ok: false, code: "OWNER_AUTHORITY_HMAC_SECRET_MISSING" };
  }
  if (changeId && decision.bound_change_id !== changeId) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_CHANGE_MISMATCH" };
  }
  if (sliceId && decision.bound_slice_id !== sliceId) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_SLICE_MISMATCH" };
  }
  if (recoveryCode && decision.bound_recovery_code !== recoveryCode) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_CODE_MISMATCH" };
  }
  if (
    generationLineageId &&
    decision.bound_generation_lineage_id !== generationLineageId
  ) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_LINEAGE_MISMATCH" };
  }
  if (recoveryPath && decision.recovery_path && decision.recovery_path !== recoveryPath) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_PATH_MISMATCH" };
  }
  const expected = signPostImplementRecoveryOwnerDecision(decision, secret);
  const got = String(decision.signature_hmac_sha256 || "");
  if (got.length !== expected.length) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_SIGNATURE_INVALID" };
  }
  if (!timingSafeEqual(Buffer.from(got, "utf8"), Buffer.from(expected, "utf8"))) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_SIGNATURE_INVALID" };
  }
  const expiresAt = Date.parse(decision.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) {
    return { ok: false, code: "RECOVERY_OWNER_DECISION_EXPIRED" };
  }
  return { ok: true, decision_id: decision.decision_id };
}

/**
 * Fail closed when recovery would route operative RED but observation adoption is required.
 */
export function recoveryRouteUsesAdoptionNotOperativeRed({ red, recovery, ownerDecision } = {}) {
  if (!isObject(red) || red.status !== ALREADY_SATISFIED) return { ok: true };
  if (recovery?.disposition === "FROZEN_TEST_MUTATED_REQUIRES_RE_RED") {
    return {
      ok: false,
      code: "ADOPTION_NOT_OPERATIVE_RED",
      detail: "ALREADY_SATISFIED observation cannot authorize operative /opsx-apply-red",
    };
  }
  if (ownerDecision && ownerDecision.adoption_not_operative_red !== true) {
    return { ok: false, code: "ADOPTION_NOT_OPERATIVE_RED" };
  }
  return { ok: true };
}

function classifySliceRecoveryContext({
  design,
  scaffoldBySlice,
  redBySlice,
  implementBySlice,
  greenBySlice,
  repoRoot,
}) {
  const sliceIds = design?.slice_order?.nodes || [];
  const blocked = collectPostImplementRecoveryBlocked({
    sliceIds,
    redBySlice,
    implementBySlice,
    repoRoot,
  });
  const stage = selectNextPipelineStage({
    design,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    designReview: { status: "APPLY_DESIGN_REVIEW_PASS" },
    repoRoot,
  });
  const greenPick = selectNextGreenableSlice({
    design,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    repoRoot,
  });
  const pick =
    greenPick.slice_id && !greenPick.ok
      ? greenPick
      : resolveRecoveryBlockedGreenPick(blocked, {
          design,
          redBySlice,
          implementBySlice,
          repoRoot,
        });
  const sliceId = greenPick.slice_id || pick.slice_id || null;
  const recovery =
    greenPick.recovery ||
    pick.recovery ||
    (sliceId
      ? classifyPostImplementGreenRecovery({
          red: redBySlice[sliceId],
          implement: implementBySlice[sliceId],
          green: greenBySlice[sliceId],
          repoRoot,
        })
      : null);
  return { blocked, pick: greenPick.ok ? greenPick : pick, stage, greenPick, recovery };
}

/**
 * Bounded-verify successor after recovery reaches IMPL_OK (native consumer).
 */
export function recommendPostImplementRecoverySuccessorPlan({
  design,
  changeId,
  changeRoot = null,
  repoRoot = null,
  recoveryLedger,
  taskProjection = null,
  callerTaskProjection = null,
  redBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
} = {}) {
  if (recoveryLedger?.successor_plan?.ok) {
    const proven = assertCurrentGenerationSuccessorPublished({
      changeId,
      design,
      repoRoot,
      changeRoot,
      recoveryLedger,
    });
    if (!proven.ok) {
      return { ok: false, code: proven.code };
    }
    return {
      ...recoveryLedger.successor_plan,
      published_disk_supersession: true,
    };
  }
  if (
    !recoveryLedger ||
    (recoveryLedger.phase !== RECOVERY_PHASE_IMPL_OK &&
      recoveryLedger.phase !== RECOVERY_PHASE_SUCCESSOR_PLAN)
  ) {
    return { ok: false, code: "RECOVERY_NOT_IMPL_OK" };
  }
  const freshEvidence =
    recoveryLedger.fresh_evidence_record || recoveryLedger.fresh_evidence;
  if (!freshEvidence || freshEvidence.status !== FRESH_EVIDENCE_PASS) {
    return { ok: false, code: "RECOVERY_EVIDENCE_NOT_PASS" };
  }
  return buildBoundedVerifySuccessorMechanism({
    design,
    changeId,
    changeRoot,
    repoRoot,
    recoveryLedger,
    freshEvidence,
    taskProjection,
    redBySlice,
    greenBySlice,
    upstreamBySlice,
    callerTaskProjection: callerTaskProjection || taskProjection,
  });
}

/**
 * Idempotent recovery state advance (ledger is the durable checkpoint).
 */
export function advancePostImplementRecovery({
  design,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
  repoRoot,
  changeRoot = null,
  ledger = null,
  ownerDecision = null,
  ownerDecisionSecret = null,
  planFingerprint = null,
  recoveryAttemptId = null,
  taskProjection = null,
  runCommand = runFrozenRedCommand,
  verificationCommandsOverride = null,
  recoveryManifestDigest = null,
  designSupersessionId = null,
  publishSuccessor = true,
} = {}) {
  const attemptId =
    recoveryAttemptId ||
    ledger?.recovery_attempt_id ||
    sha256Hex(
      `${design?.change_id || ""}:${ledger?.slice_id || "none"}:${Date.now()}`,
    );

  if (changeRoot) {
    const persisted = loadPersistedRecoveryAttempt({
      repoRoot,
      changeRoot,
      recoveryAttemptId: attemptId,
    });
    if (persisted.ok && persisted.ledger?.terminal === true) {
      return { ok: true, idempotent: true, ledger: persisted.ledger };
    }
  }

  if (ledger?.terminal === true && ledger.recovery_attempt_id === attemptId) {
    return { ok: true, idempotent: true, ledger };
  }

  const ctx = classifySliceRecoveryContext({
    design,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    repoRoot,
  });
  const sliceId = ctx.pick.slice_id || ctx.greenPick.slice_id || null;
  const recovery = ctx.recovery;
  const pathInfo = classifyPostImplementRecoveryPath(recovery, {
    red: sliceId ? redBySlice[sliceId] : null,
    implement: sliceId ? implementBySlice[sliceId] : null,
    repoRoot,
  });
  const semanticGate = evaluateSemanticEquivalenceGate({
    recovery,
    red: sliceId ? redBySlice[sliceId] : null,
    implement: sliceId ? implementBySlice[sliceId] : null,
    repoRoot,
  });

  let phase = RECOVERY_PHASE_CLASSIFICATION;
  let nextLedger = {
    schema: RECOVERY_LEDGER_SCHEMA,
    schema_version: 1,
    recovery_attempt_id: attemptId,
    change_id: design?.change_id,
    slice_id: sliceId,
    recovery_code: recovery?.code || ctx.pick.code,
    recovery_path: pathInfo.path,
    phase,
    generation_lineage_id: null,
    execution_revision: null,
    owner_decision_id: null,
    terminal: false,
    selector_parity: {
      pipeline_stage: ctx.stage.next_stage,
      pipeline_recovery_code: ctx.stage.recovery_code,
      green_code: ctx.greenPick.code,
      match:
        ctx.stage.recovery_code === ctx.greenPick.code &&
        ctx.stage.next_slice === ctx.greenPick.slice_id,
    },
  };

  if (pathInfo.path === RECOVERY_PATH_NATIVE_REFRESH && semanticGate.outcome === "AUTO_PROVEN") {
    const { execution_revision: executionRevision } = computeRecoveryExecutionRevision({
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      repoRoot,
    });
    const lineage = computeRecoveryGenerationLineage({
      changeId: design?.change_id,
      sliceId,
      recoveryCode: recovery?.code,
      planFingerprint,
      executionRevision,
    });
    nextLedger = {
      ...nextLedger,
      phase: RECOVERY_PHASE_NEW_GENERATION,
      generation_lineage_id: lineage.generation_lineage_id,
      execution_revision: executionRevision,
      plan_fingerprint_alias: lineage.plan_fingerprint_alias,
    };
    let publishedGeneration = null;
    if (changeRoot) {
      const pub = publishRecoveryGeneration({
        repoRoot,
        changeRoot,
        recoveryAttemptId: attemptId,
        generationLineageId: lineage.generation_lineage_id,
        lineageBody: lineage.body,
        ledger: nextLedger,
      });
      if (!pub.ok) {
        return { ok: false, code: pub.code, ledger: nextLedger };
      }
      publishedGeneration = pub.generation;
    }
    const redForEvidence =
      verificationCommandsOverride?.length && sliceId
        ? {
            ...redBySlice[sliceId],
            recovery_adoption_verification_commands: verificationCommandsOverride,
            post_implement_verification_commands: [],
          }
        : redBySlice[sliceId];
    const freshEvidence = anchorRecoveryEvidenceBindings(
      executeRecoveryFreshEvidence({
      recoveryPath: RECOVERY_PATH_NATIVE_REFRESH,
      red: redForEvidence,
      implement: implementBySlice[sliceId],
      generationLineageId: lineage.generation_lineage_id,
      executionRevision,
      boundSliceId: sliceId,
      repoRoot,
      runCommand,
      identityAlgorithmDriftOnly: recovery?.identity_algorithm_drift_only === true,
    }),
      { red: redForEvidence, repoRoot, changeRoot, sliceId },
    );
    const finalized = finalizeRecoveryLedgerPhases({
      design,
      nextLedger,
      freshEvidence,
      generation: publishedGeneration,
      changeRoot,
      repoRoot,
      recoveryAttemptId: attemptId,
      callerTaskProjection: taskProjection,
      redBySlice,
      implementBySlice,
      greenBySlice,
      upstreamBySlice,
      publishSuccessor,
    });
    return {
      ...finalized,
      owner_decision_packet: null,
      unauthorized_generation: false,
    };
  }

  if (pathInfo.path === RECOVERY_PATH_B && semanticGate.outcome === "AUTO_PROVEN") {
    const { execution_revision: executionRevision } = computeRecoveryExecutionRevision({
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      repoRoot,
    });
    const lineage = computeRecoveryGenerationLineage({
      changeId: design?.change_id,
      sliceId,
      recoveryCode: recovery?.code,
      planFingerprint,
      executionRevision,
    });
    nextLedger = {
      ...nextLedger,
      phase: RECOVERY_PHASE_NEW_GENERATION,
      generation_lineage_id: lineage.generation_lineage_id,
      execution_revision: executionRevision,
      plan_fingerprint_alias: lineage.plan_fingerprint_alias,
      unauthorized_generation: false,
    };
    let publishedGeneration = null;
    if (changeRoot) {
      const pub = publishRecoveryGeneration({
        repoRoot,
        changeRoot,
        recoveryAttemptId: attemptId,
        generationLineageId: lineage.generation_lineage_id,
        lineageBody: lineage.body,
        ledger: nextLedger,
      });
      if (!pub.ok) {
        return { ok: false, code: pub.code, ledger: nextLedger };
      }
      publishedGeneration = pub.generation;
    }
    const redForEvidence = redBySlice[sliceId];
    const freshEvidence = anchorRecoveryEvidenceBindings(
      executeRecoveryFreshEvidence({
        recoveryPath: RECOVERY_PATH_B,
        red: redForEvidence,
        implement: implementBySlice[sliceId],
        generationLineageId: lineage.generation_lineage_id,
        executionRevision,
        boundSliceId: sliceId,
        repoRoot,
        runCommand,
      }),
      { red: redForEvidence, repoRoot, changeRoot, sliceId },
    );
    const finalized = finalizeRecoveryLedgerPhases({
      design,
      nextLedger,
      freshEvidence,
      generation: publishedGeneration,
      changeRoot,
      repoRoot,
      recoveryAttemptId: attemptId,
      callerTaskProjection: taskProjection,
      redBySlice,
      implementBySlice,
      greenBySlice,
      upstreamBySlice,
      publishSuccessor,
    });
    return {
      ...finalized,
      owner_decision_packet: null,
      unauthorized_generation: false,
    };
  }

  if (pathInfo.path === RECOVERY_PATH_A && !ownerDecision) {
    phase = RECOVERY_PHASE_AUTHORITY;
    const packet = buildOwnerDecisionPacket({
      changeId: design?.change_id,
      sliceId,
      recovery,
      semanticGate,
      recoveryPath: pathInfo,
    });
    nextLedger = {
      ...nextLedger,
      phase,
      generation_lineage_id: null,
      owner_decision_packet: packet,
      unauthorized_generation: true,
    };
    return {
      ok: false,
      code: "RECOVERY_AUTHORITY_PENDING",
      ledger: nextLedger,
      owner_decision_packet: packet,
      unauthorized_generation: true,
    };
  }

  if (pathInfo.path === RECOVERY_PATH_B && semanticGate.owner_decision_required) {
    phase = RECOVERY_PHASE_AUTHORITY;
    const lineage = computeRecoveryGenerationLineage({
      changeId: design?.change_id,
      sliceId,
      recoveryCode: recovery?.code,
      planFingerprint,
    });
    const packet = buildOwnerDecisionPacket({
      changeId: design?.change_id,
      sliceId,
      recovery,
      semanticGate,
      generationLineage: lineage,
      recoveryPath: pathInfo,
    });
    nextLedger = {
      ...nextLedger,
      phase,
      generation_lineage_id: null,
      owner_decision_packet: packet,
      unauthorized_generation: true,
    };
    if (!ownerDecision) {
      return {
        ok: false,
        code: "RECOVERY_AUTHORITY_PENDING",
        ledger: nextLedger,
        owner_decision_packet: packet,
        unauthorized_generation: true,
      };
    }
  }

  if (ownerDecision) {
    const { execution_revision: executionRevision } = computeRecoveryExecutionRevision({
      red: redBySlice[sliceId],
      implement: implementBySlice[sliceId],
      repoRoot,
    });
    const lineage = computeRecoveryGenerationLineage({
      changeId: design?.change_id,
      sliceId,
      recoveryCode: recovery?.code,
      planFingerprint,
      ownerDecisionId: ownerDecision.decision_id,
      executionRevision,
      recoveryManifestDigest,
      designSupersessionId:
        designSupersessionId ||
        (recoveryManifestDigest ? `DESIGN_SUPERSEDED:${recoveryManifestDigest}` : null),
      inputIdentities: {
        red_test_content_identity: redBySlice[sliceId]?.test_content_identity || null,
        implement_frozen_test_content_identity:
          implementBySlice[sliceId]?.frozen_test_content_identity ||
          implementBySlice[sliceId]?.frozen_test_identity_after ||
          null,
      },
    });
    const validated = validatePostImplementRecoveryOwnerDecision(ownerDecision, {
      secret: ownerDecisionSecret,
      changeId: design?.change_id,
      sliceId,
      recoveryCode: recovery?.code,
      generationLineageId: lineage.generation_lineage_id,
      recoveryPath: pathInfo.path,
    });
    if (!validated.ok) {
      return { ok: false, code: validated.code, ledger: nextLedger };
    }
    const adoption = recoveryRouteUsesAdoptionNotOperativeRed({
      red: redBySlice[sliceId],
      recovery,
      ownerDecision,
    });
    if (!adoption.ok) {
      return { ok: false, code: adoption.code, ledger: nextLedger };
    }
    nextLedger = {
      ...nextLedger,
      phase: RECOVERY_PHASE_NEW_GENERATION,
      generation_lineage_id: lineage.generation_lineage_id,
      execution_revision: executionRevision,
      plan_fingerprint_alias: lineage.plan_fingerprint_alias,
      owner_decision_id: ownerDecision.decision_id,
      unauthorized_generation: false,
    };
    let publishedGeneration = null;
    if (changeRoot) {
      const pub = publishRecoveryGeneration({
        repoRoot,
        changeRoot,
        recoveryAttemptId: attemptId,
        generationLineageId: lineage.generation_lineage_id,
        lineageBody: lineage.body,
        ledger: nextLedger,
      });
      if (!pub.ok) {
        return { ok: false, code: pub.code, ledger: nextLedger };
      }
      publishedGeneration = pub.generation;
    }
    const redForEvidence =
      verificationCommandsOverride?.length && sliceId
        ? {
            ...redBySlice[sliceId],
            recovery_adoption_verification_commands: verificationCommandsOverride,
            post_implement_verification_commands: [],
          }
        : redBySlice[sliceId];
    const freshEvidence = anchorRecoveryEvidenceBindings(
      executeRecoveryFreshEvidence({
      recoveryPath: pathInfo.path,
      red: redForEvidence,
      implement: implementBySlice[sliceId],
      generationLineageId: lineage.generation_lineage_id,
      executionRevision,
      boundSliceId: sliceId,
      repoRoot,
      runCommand,
      ownerDecisionValidated: true,
    }),
      { red: redForEvidence, repoRoot, changeRoot, sliceId },
    );
    const finalized = finalizeRecoveryLedgerPhases({
      design,
      nextLedger,
      freshEvidence,
      generation: publishedGeneration,
      changeRoot,
      repoRoot,
      recoveryAttemptId: attemptId,
      callerTaskProjection: taskProjection,
      redBySlice,
      implementBySlice,
      greenBySlice,
      upstreamBySlice,
      publishSuccessor,
    });
    return { ...finalized, owner_decision_packet: null };
  }

  if (pathInfo.path === RECOVERY_PATH_OPERATIVE_RE_RED) {
    return {
      ok: false,
      code: "OPERATIVE_RE_RED_ROUTE",
      ledger: nextLedger,
      next_command: ctx.pick.next_command,
    };
  }

  return { ok: false, code: "RECOVERY_CLASSIFICATION_UNRESOLVED", ledger: nextLedger };
}

/**
 * Native consumer bundle: completion + super-implement + selector parity.
 */
export function evaluatePostImplementRecoveryConsumers(ctx) {
  const {
    design,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    repoRoot,
    blocking = [],
  } = ctx;
  const completion = recommendSplitApplyImplNextAction({
    design,
    changeId: design?.change_id,
    blocking,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    repoRoot,
  });
  const superTransition = selectSuperImplementTransition({
    design,
    maps: {
      scaffoldBySlice,
      redBySlice,
      implementBySlice,
      greenBySlice,
    },
    designReview: { status: "APPLY_DESIGN_REVIEW_PASS" },
    repoRoot,
  });
  const recoveryCtx = classifySliceRecoveryContext({
    design,
    scaffoldBySlice,
    redBySlice,
    implementBySlice,
    greenBySlice,
    repoRoot,
  });
  return {
    completion,
    super_transition: superTransition,
    selector_parity: recoveryCtx.stage.recovery_code === recoveryCtx.greenPick.code,
  };
}
