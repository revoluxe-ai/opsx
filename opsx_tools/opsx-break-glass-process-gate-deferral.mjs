import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Synthetic process-gate deferral receipts for Owner break-glass exception route.
 *
 * Records original FAIL truth, OWNER_OVERRIDE provenance, and open verification debt.
 * Does not mint ordinary GREEN, VERIFY_PASS, or product PASS.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  classifyGreenEvidenceFreshness,
  loadBoundedVerifyBindingFromDisk,
  loadCurrentSliceArtifactIdentities,
} from "./opsx-apply-pipeline-identity.mjs";
import { executionIdentityDigest } from "./opsx-break-glass-execution-identity.mjs";
const GATE_ANY = "ANY";
const FAILURE_UNKNOWN = "UNKNOWN";
const BLOCKER_SCOPE_ANY = "ANY_OPSX_BLOCKER";

const PRODUCT_OUTCOME_DENY = new Set([
  "INTENDED_BEHAVIOR_MISSING",
  "PRODUCT_TEST_FAILED",
  "UNRESOLVED_PRODUCT_DEFECT",
]);
import { OWNER_OVERRIDDEN, STATE_CONTINUE_WITH_EXCEPTION } from "./opsx-break-glass.mjs";
import { findOpenIncidents } from "./opsx-break-glass-ledger.mjs";
import { readDebtLedger } from "./opsx-break-glass-debt-journal.mjs";
import { guardedWriteText } from "./opsx-break-glass-write-guard.mjs";

function loadGrantSnapshotFromDebtJournal(repoRoot, grantId) {
  const events = readDebtLedger(repoRoot);
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i];
    if (ev.grant_id === grantId && ev.grant_snapshot) {
      return { ok: true, grant: ev.grant_snapshot };
    }
  }
  return { ok: false, code: "GRANT_SNAPSHOT_NOT_IN_DEBT_JOURNAL" };
}

export const PROCESS_GATE_DEFERRAL_SCHEMA = "opsx.break_glass_process_gate_deferral";
export const PROCESS_GATE_DEFERRAL_VERSION = 1;
export const PROVENANCE_OWNER_OVERRIDE = "OWNER_OVERRIDE";

export const GATE_SPLIT_APPLY_GREEN_FRESHNESS = "SPLIT_APPLY_GREEN_FRESHNESS";
export const GATE_QUALITY_TYPECHECK = "QUALITY_TYPECHECK";
export const GATE_PIPELINE_SELECTOR = "PIPELINE_SELECTOR";
export const GATE_POST_IMPLEMENT_RECOVERY = "POST_IMPLEMENT_RECOVERY_SELECTOR";
export const GATE_BOUNDED_VERIFY_COMPOSE = "BOUNDED_VERIFY_COMPOSE";

/** Registered process gates honor ANY_OPSX_BLOCKER grants without per-gate re-authorization. */
export const REGISTERED_PROCESS_GATES = new Set([
  GATE_ANY,
  GATE_SPLIT_APPLY_GREEN_FRESHNESS,
  GATE_QUALITY_TYPECHECK,
  GATE_PIPELINE_SELECTOR,
  GATE_POST_IMPLEMENT_RECOVERY,
  GATE_BOUNDED_VERIFY_COMPOSE,
  "APPLY_RED_OPERATIVE",
]);

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export function processGateDeferralRelPath(changeRoot) {
  return join(changeRoot, "apply-process-exception/PROCESS-GATE-DEFERRAL.json");
}

function readJsonIfExists(absPath) {
  if (!existsSync(absPath)) return null;
  try {
    return JSON.parse(readFileSync(absPath, "utf8"));
  } catch {
    return null;
  }
}

export function isRegisteredProcessGate(gateId) {
  const g = String(gateId || "").trim();
  return Boolean(g) && REGISTERED_PROCESS_GATES.has(g);
}

/**
 * Extended gate match for universal V2 grants (ANY_OPSX_BLOCKER).
 */
export function processGateMatchesOwnerOverride({
  incident,
  grant,
  blockedGate,
  failureCode = null,
} = {}) {
  if (!incident || incident.exception_state !== STATE_CONTINUE_WITH_EXCEPTION) {
    return false;
  }
  const incGate = String(incident.failed_gate || "");
  const reqGate = String(blockedGate || "");
  if (!reqGate) return false;
  let gateOk =
    incGate === GATE_ANY || reqGate === GATE_ANY || (incGate && incGate === reqGate);
  if (!gateOk && grant?.blocker_scope === BLOCKER_SCOPE_ANY && isRegisteredProcessGate(reqGate)) {
    gateOk = true;
  }
  if (!gateOk) return false;
  const reqCode = failureCode ? String(failureCode).trim() : null;
  if (!reqCode || reqCode === FAILURE_UNKNOWN) return true;
  if (PRODUCT_OUTCOME_DENY.has(reqCode)) return false;
  const incCode = incident.failure_code ? String(incident.failure_code) : null;
  if (!incCode || incCode === FAILURE_UNKNOWN) return true;
  if (grant?.blocker_scope === BLOCKER_SCOPE_ANY) return true;
  return incCode === reqCode;
}

export function collectNativeGreenFreshnessReport(repoRoot, changeRoot) {
  const designPath = resourcePath(repoRoot, changeRoot, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    return { ok: true, split_apply: false, policy: "NOT_APPLICABLE", slices: [] };
  }
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const boundedVerifyBinding = loadBoundedVerifyBindingFromDisk(repoRoot, changeRoot);
  const sliceIds =
    design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id).filter(Boolean);
  const slices = [];
  for (const sliceId of sliceIds) {
    const ids = loadCurrentSliceArtifactIdentities(repoRoot, changeRoot, sliceId);
    const freshness = classifyGreenEvidenceFreshness(ids.green, {
      design: ids.design,
      scaffold: ids.scaffold,
      red: ids.red,
      implement: ids.implement,
      designArtifactSha256: ids.designArtifactSha256,
      scaffoldArtifactSha256: ids.scaffoldArtifactSha256,
      redArtifactSha256: ids.redArtifactSha256,
      implementArtifactSha256: ids.implementArtifactSha256,
      frozenTestContentIdentity: ids.frozenTestContentIdentity,
      liveFrozenTestContentIdentity: ids.liveFrozenTestContentIdentity,
      boundedVerifyBinding,
      repoRoot,
    });
    slices.push({
      slice_id: sliceId,
      green_status: ids.green?.status ?? null,
      freshness,
    });
  }
  return {
    ok: true,
    split_apply: true,
    policy: "SPLIT_APPLY_CURRENT_GREEN",
    slices,
  };
}

function deferralIdempotencyKey({ grantId, incidentId, revision }) {
  return createHash("sha256")
    .update(`${grantId}:${incidentId}:${revision}:process_gate_deferral_v1`, "utf8")
    .digest("hex");
}

function buildDeferralBody({
  changeRoot,
  changeId,
  grant,
  incident,
  runtimeIdentity,
  freshnessReport = null,
  activationGateResult = null,
}) {
  const identity = runtimeIdentity?.ok ? runtimeIdentity.identity : runtimeIdentity;
  const staleSlices = (freshnessReport?.slices || [])
    .filter((row) => !(row.freshness || {}).current)
    .map((row) => ({
      slice_id: row.slice_id,
      green_status: row.green_status,
      freshness: row.freshness,
      ordinary_status: "FAILED",
      code: row.freshness?.code || "STALE_GREEN_EVIDENCE",
    }));
  const digest = executionIdentityDigest(identity || {});
  return {
    schema: PROCESS_GATE_DEFERRAL_SCHEMA,
    schema_version: PROCESS_GATE_DEFERRAL_VERSION,
    provenance: PROVENANCE_OWNER_OVERRIDE,
    change_id: changeId,
    change_root: changeRoot,
    grant_id: grant.grant_id,
    incident_id: incident.incident_id,
    execution_id: grant.execution_id,
    execution_identity_digest: digest,
    repository_identity: grant.repository_identity,
    gate_check_status: OWNER_OVERRIDDEN,
    allowed_transitions: grant.allowed_transitions || [],
    activation_blocked_gate: incident.failed_gate,
    activation_failure_code: incident.failure_code,
    actual_gate_results: {
      activation: activationGateResult || incident.actual_verification_results || {
        status: "FAILED",
        gate_check_status: OWNER_OVERRIDDEN,
        blocked_gate: incident.failed_gate,
        failure_code: incident.failure_code,
      },
      split_apply_green_freshness: staleSlices.length
        ? {
            status: "FAILED",
            gate_check_status: OWNER_OVERRIDDEN,
            blocked_gate: GATE_SPLIT_APPLY_GREEN_FRESHNESS,
            slices: staleSlices,
            minted_fake_green: false,
          }
        : null,
      quality_typecheck: {
        status: "FAILED",
        gate_check_status: OWNER_OVERRIDDEN,
        blocked_gate: GATE_QUALITY_TYPECHECK,
        failure_code: incident.failure_code === "MYPY_REPO_WIDE_DEBT"
          ? "MYPY_REPO_WIDE_DEBT"
          : "PROCESS_TYPECHECK_DEFERRED",
        minted_fake_pass: false,
        deferred_without_executing: true,
      },
    },
    verification_debt_open: true,
    minted_fake_green: false,
    minted_fake_verify_pass: false,
    idempotency_key: deferralIdempotencyKey({
      grantId: grant.grant_id,
      incidentId: incident.incident_id,
      revision: identity?.revision || grant.repository_identity?.revision,
    }),
    created_at: new Date().toISOString(),
  };
}

export function readProcessGateDeferral(repoRoot, changeRoot) {
  const abs = resourcePath(repoRoot, processGateDeferralRelPath(changeRoot));
  const doc = readJsonIfExists(abs);
  if (!doc || doc.schema !== PROCESS_GATE_DEFERRAL_SCHEMA) {
    return { ok: false, code: "DEFERRAL_ABSENT" };
  }
  return { ok: true, deferral: doc, path: processGateDeferralRelPath(changeRoot) };
}

export function validateProcessGateDeferralAgainstOpenIncident(repoRoot, changeRoot, changeId) {
  const loaded = readProcessGateDeferral(repoRoot, changeRoot);
  if (!loaded.ok) return loaded;
  const deferral = loaded.deferral;
  const open = findOpenIncidents(repoRoot, { changeId });
  const incident = open.find((i) => i.incident_id === deferral.incident_id);
  if (!incident) {
    return { ok: false, code: "INCIDENT_NOT_OPEN", reason: "deferral incident not open" };
  }
  const grantLoad = loadGrantSnapshotFromDebtJournal(repoRoot, deferral.grant_id);
  if (!grantLoad.ok) {
    return { ok: false, code: "GRANT_SNAPSHOT_MISSING" };
  }
  if (deferral.grant_id !== grantLoad.grant.grant_id) {
    return { ok: false, code: "GRANT_MISMATCH" };
  }
  return { ok: true, deferral, incident, grant: grantLoad.grant };
}

/**
 * Materialize or refresh PROCESS-GATE-DEFERRAL.json under change scope (idempotent).
 */
export function materializeProcessGateDeferralReceipt({
  repoRoot,
  changeRoot,
  grant,
  incident,
  runtimeIdentity,
} = {}) {
  if (!grant?.grant_id || !incident?.incident_id) {
    return { ok: false, code: "BINDING_INCOMPLETE" };
  }
  const changeId = grant.change_id;
  const existing = readProcessGateDeferral(repoRoot, changeRoot);
  if (existing.ok && existing.deferral.idempotency_key) {
    const expected = deferralIdempotencyKey({
      grantId: grant.grant_id,
      incidentId: incident.incident_id,
      revision:
        grant.repository_identity?.revision ||
        (runtimeIdentity?.ok ? runtimeIdentity.identity.revision : runtimeIdentity?.revision),
    });
    if (existing.deferral.idempotency_key === expected) {
      return { ok: true, idempotent: true, path: existing.path, deferral: existing.deferral };
    }
  }
  const freshnessReport = collectNativeGreenFreshnessReport(repoRoot, changeRoot);
  const body = buildDeferralBody({
    changeRoot,
    changeId,
    grant,
    incident,
    runtimeIdentity,
    freshnessReport,
  });
  const rel = processGateDeferralRelPath(changeRoot);
  const written = guardedWriteText({
    grant,
    repoRoot,
    relPath: rel,
    content: `${JSON.stringify(body, null, 2)}\n`,
  });
  if (!written.ok) return written;
  return { ok: true, idempotent: false, path: rel, deferral: body };
}

/**
 * Backfill deferral for an already-activated open incident (no new Owner authorization).
 */
export function ensureProcessGateDeferralForChange({
  repoRoot,
  changeId,
  changeRoot = null,
} = {}) {
  const root = repoRoot || DEFAULT_ROOT;
  const cr = changeRoot || `openspec/changes/${changeId}`;
  const open = findOpenIncidents(root, { changeId });
  const incident = open.find((i) => i.exception_state === STATE_CONTINUE_WITH_EXCEPTION);
  if (!incident) {
    return { ok: false, code: "NO_OPEN_CONTINUE_INCIDENT" };
  }
  const grantLoad = loadGrantSnapshotFromDebtJournal(root, incident.grant_id);
  if (!grantLoad.ok) {
    return { ok: false, code: "GRANT_SNAPSHOT_MISSING" };
  }
  const grant = grantLoad.grant;
  const rev = grant.repository_identity?.revision;
  if (incident.revision && rev && String(incident.revision) !== String(rev)) {
    return {
      ok: false,
      code: "EXECUTION_IDENTITY_MISMATCH",
      reason: "incident revision does not match grant.repository_identity.revision",
    };
  }
  return materializeProcessGateDeferralReceipt({
    repoRoot: root,
    changeRoot: cr,
    grant,
    incident,
    runtimeIdentity: {
      change_id: changeId,
      execution_id: grant.execution_id,
      revision: rev,
      branch: grant.repository_identity?.branch,
      blocked_gate: incident.failed_gate,
      failure_code: incident.failure_code,
    },
  });
}

function findOwnerOverrideForProcessGate(repoRoot, changeId, blockedGate, failureCode) {
  const open = findOpenIncidents(repoRoot, { changeId });
  for (const inc of open) {
    if (inc.exception_state !== STATE_CONTINUE_WITH_EXCEPTION) continue;
    const grantLoad = loadGrantSnapshotFromDebtJournal(repoRoot, inc.grant_id);
    const grant = grantLoad.ok ? grantLoad.grant : null;
    if (
      !processGateMatchesOwnerOverride({
        incident: inc,
        grant,
        blockedGate,
        failureCode,
      })
    ) {
      continue;
    }
    return {
      ok: true,
      incident_id: inc.incident_id,
      grant_id: inc.grant_id,
      grant,
      incident: inc,
    };
  }
  return { ok: false, code: "NO_OWNER_OVERRIDE" };
}

export function evaluateSplitApplyFreshnessExceptionRoute({
  repoRoot,
  changeRoot,
  changeId,
} = {}) {
  const report = collectNativeGreenFreshnessReport(repoRoot, changeRoot);
  if (!report.split_apply) {
    return { ok: true, exception_route: false, report };
  }
  const stale = (report.slices || []).filter((row) => !(row.freshness || {}).current);
  if (!stale.length) {
    return { ok: true, exception_route: false, ordinary_route: true, report };
  }
  const firstCode = stale[0]?.freshness?.code || "STALE_GREEN_EVIDENCE";
  const override = findOwnerOverrideForProcessGate(
    repoRoot,
    changeId,
    GATE_SPLIT_APPLY_GREEN_FRESHNESS,
    firstCode,
  );
  if (!override.ok) {
    return {
      ok: false,
      code: "SPLIT_APPLY_STALE",
      message: `slice ${stale[0].slice_id}: ${firstCode}`,
      report,
    };
  }
  const ensured = ensureProcessGateDeferralForChange({
    repoRoot,
    changeId,
    changeRoot,
  });
  if (!ensured.ok) {
    return { ok: false, code: "DEFERRAL_MATERIALIZE_FAILED", detail: ensured };
  }
  const validated = validateProcessGateDeferralAgainstOpenIncident(repoRoot, changeRoot, changeId);
  if (!validated.ok) {
    return { ok: false, code: "DEFERRAL_INVALID", detail: validated };
  }
  return {
    ok: true,
    exception_route: true,
    gate_check_status: OWNER_OVERRIDDEN,
    ordinary_green_freshness_debt: stale.map((row) => ({
      slice_id: row.slice_id,
      freshness: row.freshness,
      current: false,
    })),
    deferral_path: ensured.path,
    report,
  };
}

export function isQualityTypecheckFrontierDeferred(repoRoot, changeId, changeRoot = null) {
  const cr = changeRoot || `openspec/changes/${changeId}`;
  const override = findOwnerOverrideForProcessGate(
    repoRoot,
    changeId,
    GATE_QUALITY_TYPECHECK,
    "MYPY_REPO_WIDE_DEBT",
  );
  if (!override.ok) {
    return { ok: false, deferred: false };
  }
  const ensured = ensureProcessGateDeferralForChange({ repoRoot, changeId, changeRoot: cr });
  if (!ensured.ok) {
    return { ok: false, deferred: false, detail: ensured };
  }
  return { ok: true, deferred: true, gate_check_status: OWNER_OVERRIDDEN };
}

function parseCli(argv) {
  const cmd = argv[0];
  const out = { cmd, changeRoot: null, changeId: null };
  for (let i = 1; i < argv.length; i += 1) {
    if (argv[i] === "--change-root" && argv[i + 1]) out.changeRoot = argv[++i];
    else if (argv[i] === "--change-id" && argv[i + 1]) out.changeId = argv[++i];
  }
  return out;
}

function main(argv) {
  const args = parseCli(argv);
  const repoRoot = DEFAULT_ROOT;
  if (args.cmd === "ensure-deferral") {
    const changeId =
      args.changeId ||
      (args.changeRoot ? args.changeRoot.split("/").pop() : null);
    if (!changeId) {
      process.stdout.write(JSON.stringify({ ok: false, code: "missing_change_id" }) + "\n");
      process.exit(1);
    }
    const result = ensureProcessGateDeferralForChange({
      repoRoot,
      changeId,
      changeRoot: args.changeRoot,
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 2);
  }
  if (args.cmd === "split-apply-freshness") {
    if (!args.changeRoot) {
      process.stdout.write(JSON.stringify({ ok: false, code: "missing_change_root" }) + "\n");
      process.exit(1);
    }
    const changeId = args.changeId || args.changeRoot.split("/").pop();
    const result = evaluateSplitApplyFreshnessExceptionRoute({
      repoRoot,
      changeId,
      changeRoot: args.changeRoot,
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 2);
  }
  if (args.cmd === "quality-deferral") {
    const changeId =
      args.changeId ||
      (args.changeRoot ? args.changeRoot.split("/").pop() : null);
    if (!changeId) {
      process.stdout.write(JSON.stringify({ ok: false, code: "missing_change_id" }) + "\n");
      process.exit(1);
    }
    const result = isQualityTypecheckFrontierDeferred(repoRoot, changeId, args.changeRoot);
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok && result.deferred ? 0 : 2);
  }
  process.stdout.write(JSON.stringify({ ok: false, code: "unknown_command" }) + "\n");
  process.exit(1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main(process.argv.slice(2));
}
