import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Thin CLI for post-IMPLEMENT test-contract recovery (advance / replay / invoke-successor).
 *
 * Delegates to opsx-post-implement-test-contract-recovery.mjs runtime; persists LEDGER on pause.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadChangeReceiptMaps } from "./opsx-apply-pipeline-identity.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  RECOVERY_PHASE_FRESH_EVIDENCE,
  RECOVERY_PHASE_IMPL_OK,
  RECOVERY_PHASE_SUCCESSOR_PLAN,
  advancePostImplementRecovery,
  assertRecoveryVerifyCommandAuthorized,
  invokePostImplementRecoverySuccessor,
  persistRecoveryLedger,
  replayPostImplementRecovery,
} from "./opsx-post-implement-test-contract-recovery.mjs";
import {
  advancePostImplementRecoveryCohort,
  assertCallerVerifyCommandsAuthorizedByAdoption,
  deriveRecoveryCohortVerifyCommandsBySlice,
  PILOT_AFFECTED_SLICE_IDS,
} from "./opsx-post-implement-recovery-cohort.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const OWNER_HMAC_ENV = "OPSX_POST_IMPLEMENT_RECOVERY_OWNER_HMAC_SECRET";

const PAUSE_CODES = new Set([
  "RECOVERY_AUTHORITY_PENDING",
  "RECOVERY_EVIDENCE_FAILED",
  "RECOVERY_CLASSIFICATION_UNRESOLVED",
  "OPERATIVE_RE_RED_ROUTE",
]);

function parseArgs(argv) {
  const out = {
    command: null,
    changeRoot: null,
    recoveryAttemptId: null,
    ownerDecisionPath: null,
    verifyCommand: null,
    forceChangeRoot: null,
    ownerDecisionsDir: null,
  };
  if (argv[0] && !argv[0].startsWith("-")) {
    out.command = argv[0];
  }
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--change-root" && argv[i + 1]) out.changeRoot = argv[++i];
    else if (a === "--recovery-attempt-id" && argv[i + 1]) out.recoveryAttemptId = argv[++i];
    else if (a === "--owner-decision-path" && argv[i + 1]) out.ownerDecisionPath = argv[++i];
    else if (a === "--verify-command" && argv[i + 1]) out.verifyCommand = argv[++i];
    else if (a === "--force-change-root" && argv[i + 1]) out.forceChangeRoot = argv[++i];
    else if (a === "--owner-decisions-dir" && argv[i + 1]) out.ownerDecisionsDir = argv[++i];
  }
  return out;
}

function loadRecoveryMaps(repoRoot, changeRoot) {
  const designPath = resourcePath(repoRoot, changeRoot, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    return { ok: false, code: "APPLY_DESIGN_MISSING" };
  }
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const sliceIds =
    design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id).filter(Boolean);
  const maps = loadChangeReceiptMaps(repoRoot, changeRoot, sliceIds);
  return {
    ok: true,
    design: maps.design || design,
    scaffoldBySlice: maps.scaffoldBySlice,
    redBySlice: maps.redBySlice,
    implementBySlice: maps.implementBySlice,
    greenBySlice: maps.greenBySlice,
    upstreamBySlice: maps.upstreamBySlice || {},
  };
}

function applyVerifyCommandOverride(redBySlice, sliceId, verifyCommand) {
  if (!verifyCommand || !sliceId || !redBySlice[sliceId]) {
    return redBySlice;
  }
  return {
    ...redBySlice,
    [sliceId]: {
      ...redBySlice[sliceId],
      recovery_adoption_verification_commands: [verifyCommand],
      post_implement_verification_commands: [],
    },
  };
}

function ledgerReadsAsTerminalImplOkWithoutAdvanceSuccess(ledger, advanceOk) {
  if (advanceOk === true || !ledger) {
    return false;
  }
  if (ledger.terminal === true && ledger.phase === RECOVERY_PHASE_SUCCESSOR_PLAN) {
    return false;
  }
  return ledger.impl_ok === true && ledger.phase === RECOVERY_PHASE_IMPL_OK;
}

function ledgerForAdvancePersistence(advanced) {
  const ledger = advanced.ledger;
  if (!ledger || advanced.ok === true) {
    return ledger;
  }
  if (!ledgerReadsAsTerminalImplOkWithoutAdvanceSuccess(ledger, advanced.ok)) {
    return ledger;
  }
  return {
    ...ledger,
    impl_ok: false,
    terminal: false,
    phase: RECOVERY_PHASE_FRESH_EVIDENCE,
  };
}

function formatAdvanceBody(result) {
  const ledger = result.ledger || null;
  const pause =
    result.pause === true ||
    (result.ok === false && PAUSE_CODES.has(result.code)) ||
    result.unauthorized_generation === true;
  return {
    ok: result.ok === true,
    pause,
    code: result.code || null,
    idempotent: result.idempotent === true,
    recovery_path: ledger?.recovery_path || null,
    ledger_phase: ledger?.phase || null,
    ledger,
    fresh_evidence: result.fresh_evidence || ledger?.fresh_evidence_record || null,
    owner_decision_packet: result.owner_decision_packet || ledger?.owner_decision_packet || null,
  };
}

function loadOwnerDecisionsBySlice(dirRel) {
  const absDir = resolve(DEFAULT_ROOT, dirRel);
  if (!existsSync(absDir)) {
    return {};
  }
  const out = {};
  for (const name of readdirSync(absDir)) {
    if (!name.endsWith(".json")) continue;
    const decision = JSON.parse(readFileSync(resolve(absDir, name), "utf8"));
    if (decision.bound_slice_id) {
      out[decision.bound_slice_id] = decision;
    }
  }
  return out;
}

function runAdvance(args) {
  const ctx = loadRecoveryMaps(DEFAULT_ROOT, args.changeRoot);
  if (!ctx.ok) {
    return { body: { ok: false, code: ctx.code }, exitCode: 1 };
  }
  let ownerDecision = null;
  if (args.ownerDecisionPath) {
    const abs = resolve(DEFAULT_ROOT, args.ownerDecisionPath);
    ownerDecision = JSON.parse(readFileSync(abs, "utf8"));
  }
  const sliceId =
    ownerDecision?.bound_slice_id ||
    ctx.design?.slice_order?.nodes?.find((id) => ctx.redBySlice[id]) ||
    null;
  if (args.verifyCommand && sliceId) {
    const auth = assertRecoveryVerifyCommandAuthorized({
      verifyCommand: args.verifyCommand,
      red: ctx.redBySlice[sliceId],
      implement: ctx.implementBySlice[sliceId],
      repoRoot: DEFAULT_ROOT,
    });
    if (!auth.ok) {
      return { body: { ok: false, code: auth.code }, exitCode: 1 };
    }
  }
  const advanced = advancePostImplementRecovery({
    design: ctx.design,
    scaffoldBySlice: ctx.scaffoldBySlice,
    redBySlice: ctx.redBySlice,
    implementBySlice: ctx.implementBySlice,
    greenBySlice: ctx.greenBySlice,
    upstreamBySlice: ctx.upstreamBySlice,
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    recoveryAttemptId: args.recoveryAttemptId,
    ownerDecision,
    ownerDecisionSecret: process.env[OWNER_HMAC_ENV] || null,
    verificationCommandsOverride: args.verifyCommand ? [args.verifyCommand] : null,
  });
  const ledgerToPersist = ledgerForAdvancePersistence(advanced);
  if (ledgerToPersist && args.changeRoot && args.recoveryAttemptId) {
    persistRecoveryLedger({
      repoRoot: DEFAULT_ROOT,
      changeRoot: args.changeRoot,
      recoveryAttemptId: args.recoveryAttemptId,
      ledger: ledgerToPersist,
    });
  }
  const body = formatAdvanceBody(advanced);
  let exitCode = 0;
  if (!body.ok) {
    exitCode = body.pause ? 2 : 1;
  }
  if (body.ledger_phase === RECOVERY_PHASE_SUCCESSOR_PLAN) {
    exitCode = 0;
  }
  return { body, exitCode };
}

function runAdvanceCohort(args) {
  const ctx = loadRecoveryMaps(DEFAULT_ROOT, args.changeRoot);
  if (!ctx.ok) {
    return { body: { ok: false, code: ctx.code }, exitCode: 1 };
  }
  const ownerDecisionsBySlice = args.ownerDecisionsDir
    ? loadOwnerDecisionsBySlice(args.ownerDecisionsDir)
    : {};
  const affectedSliceIds = PILOT_AFFECTED_SLICE_IDS.filter((id) =>
    (ctx.design.slice_order?.nodes || []).includes(id),
  );
  const derived = deriveRecoveryCohortVerifyCommandsBySlice({
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    affectedSliceIds,
    redBySlice: ctx.redBySlice,
  });
  if (!derived.ok) {
    return { body: { ok: false, code: derived.code }, exitCode: 1 };
  }
  const verifyCommandsBySlice = { ...derived.verify_commands_by_slice };
  if (args.verifyCommand) {
    for (const sliceId of affectedSliceIds) {
      verifyCommandsBySlice[sliceId] = [args.verifyCommand];
    }
    const auth = assertCallerVerifyCommandsAuthorizedByAdoption({
      repoRoot: DEFAULT_ROOT,
      changeRoot: args.changeRoot,
      verifyCommandsBySlice,
      affectedSliceIds,
      redBySlice: ctx.redBySlice,
    });
    if (!auth.ok) {
      return { body: { ok: false, code: auth.code }, exitCode: 1 };
    }
  }
  const advanced = advancePostImplementRecoveryCohort({
    design: ctx.design,
    scaffoldBySlice: ctx.scaffoldBySlice,
    redBySlice: ctx.redBySlice,
    implementBySlice: ctx.implementBySlice,
    greenBySlice: ctx.greenBySlice,
    upstreamBySlice: ctx.upstreamBySlice,
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    recoveryAttemptId: args.recoveryAttemptId,
    ownerDecisionsBySlice,
    ownerDecisionSecret: process.env[OWNER_HMAC_ENV] || null,
    verifyCommandsBySlice,
  });
  if (advanced.cohort_ledger && args.changeRoot && args.recoveryAttemptId) {
    persistRecoveryLedger({
      repoRoot: DEFAULT_ROOT,
      changeRoot: args.changeRoot,
      recoveryAttemptId: args.recoveryAttemptId,
      ledger: advanced.cohort_ledger,
    });
  }
  const pause = advanced.ok === false && advanced.code === "RECOVERY_AUTHORITY_PENDING";
  const body = {
    ok: advanced.ok === true,
    pause,
    code: advanced.code || null,
    manifest_id: advanced.manifest_id || null,
    slice_receipts: advanced.slice_receipts || null,
    cohort_ledger: advanced.cohort_ledger || null,
  };
  let exitCode = advanced.ok ? 0 : pause ? 2 : 1;
  return { body, exitCode };
}

function runReplay(args) {
  const replay = replayPostImplementRecovery({
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    recoveryAttemptId: args.recoveryAttemptId,
  });
  const body = {
    ok: replay.ok === true,
    idempotent: replay.idempotent === true,
    code: replay.code || null,
    ledger_phase: replay.ledger?.phase || null,
    ledger: replay.ledger || null,
  };
  return { body, exitCode: replay.ok ? 0 : 1 };
}

function runInvokeSuccessor(args) {
  const invoke = invokePostImplementRecoverySuccessor({
    repoRoot: DEFAULT_ROOT,
    changeRoot: args.changeRoot,
    recoveryAttemptId: args.recoveryAttemptId,
    forceChangeRoot: args.forceChangeRoot,
  });
  const body = {
    ok: invoke.ok === true,
    code: invoke.code || null,
    native_result: invoke.native_result || null,
  };
  return { body, exitCode: invoke.ok ? 0 : 1 };
}

function mintRecoveryAttemptId(changeRoot) {
  const stamp = Date.now().toString(36);
  const digest = createHash("sha256")
    .update(`${changeRoot}:${stamp}:${process.pid}`)
    .digest("hex")
    .slice(0, 12);
  return `chat-${stamp}-${digest}`;
}

function main(argv) {
  const args = parseArgs(argv);
  if (
    !args.command ||
    !args.changeRoot ||
    !["advance", "advance-cohort", "replay", "invoke-successor"].includes(args.command)
  ) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        code: "usage",
        message:
          "advance|advance-cohort|replay|invoke-successor --change-root <rel> [--recovery-attempt-id <id>]",
      }) + "\n",
    );
    process.exit(1);
  }
  if (
    !args.recoveryAttemptId &&
    (args.command === "advance" || args.command === "advance-cohort")
  ) {
    args.recoveryAttemptId = mintRecoveryAttemptId(args.changeRoot);
  }
  if (!args.recoveryAttemptId) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        code: "usage",
        message: "replay|invoke-successor require --recovery-attempt-id <id>",
      }) + "\n",
    );
    process.exit(1);
  }
  let result;
  if (args.command === "advance") {
    result = runAdvance(args);
  } else if (args.command === "advance-cohort") {
    result = runAdvanceCohort(args);
  } else if (args.command === "replay") {
    result = runReplay(args);
  } else {
    result = runInvokeSuccessor(args);
  }
  process.stdout.write(`${JSON.stringify(result.body, null, 2)}\n`);
  process.exit(result.exitCode);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
