/**
 * Native archive/complete gate integration for open Break Glass debt (P0-6).
 */
import { findOpenIncidents } from "./opsx-break-glass-ledger.mjs";

const TERMINAL_SUCCESS_STAGES = new Set([
  "APPLY_COMPLETE",
  "ARCHIVE_READY",
  "VERIFY_PASS",
  "INTEGRATED",
]);

/** Stages that may proceed while Break Glass incident + OPSX-BG-001 remain OPEN. */
const OPEN_INCIDENT_ALLOWED_STAGES = new Set([
  "EXCEPTION_APPLY_CONTINUE",
  "APPLY_IMPLEMENT",
  "PROCESS_GATE_BYPASSED",
  "CONTINUE_WITH_EXCEPTION",
  "APPLY_COMPLETE_WITH_EXCEPTION",
  "DEVELOPMENT_COMPLETE_WITH_EXCEPTION",
  "AUTHORIZED_DEVELOPMENT_CONTINUES",
]);

export function evaluateBreakGlassArchiveDebt({
  repoRoot,
  changeId,
  targetStage = "ARCHIVE",
} = {}) {
  if (OPEN_INCIDENT_ALLOWED_STAGES.has(targetStage)) {
    const open = findOpenIncidents(repoRoot, { changeId });
    return {
      ok: true,
      open_incident_ids: open.map((i) => i.incident_id),
      incident_debt_acknowledged: open.length > 0,
    };
  }
  const open = findOpenIncidents(repoRoot, { changeId });
  if (!open.length) return { ok: true };
  return {
    ok: false,
    code: "REVALIDATION_REQUIRED",
    reason: "OPEN_BREAK_GLASS_INCIDENT",
    blocked_stage: targetStage,
    open_incident_ids: open.map((i) => i.incident_id),
  };
}

export function blockTerminalSuccessIfBreakGlassDebt({ repoRoot, changeId, nextStage }) {
  if (!TERMINAL_SUCCESS_STAGES.has(nextStage)) {
    return { ok: true };
  }
  return evaluateBreakGlassArchiveDebt({ repoRoot, changeId, targetStage: nextStage });
}
