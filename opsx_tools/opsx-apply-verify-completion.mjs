import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Split-APPLY task-completion projection, planned test-artifact mapping,
 * and /opsx-verify mode=impl gap classification.
 *
 * Planning `tasks.md` checkboxes are frozen identity, not implementation
 * authority. CURRENT GREEN covering slices are the completion SoT.
 * Chat is not durable state.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-verify-completion.mjs --change-root <rel>
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  classifyGreenEvidenceFreshness,
  loadBoundedVerifyBindingFromDisk,
  looksLikeTestPath,
  posixPath,
  selectNextPipelineStage,
  selectNextGreenableSlice,
  loadChangeReceiptMaps,
} from "./opsx-apply-pipeline-identity.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import {
  FRESH_EVIDENCE_PASS,
  GENERATION_PUBLISHED,
  RECOVERY_PHASE_IMPL_OK,
  RECOVERY_PHASE_SUCCESSOR_PLAN,
  evaluateRecoveryCompletionConsumer,
  isRecoveryGenerationCurrent,
  loadPersistedRecoveryAttempt,
  recommendPostImplementRecoverySuccessorPlan,
} from "./opsx-post-implement-test-contract-recovery.mjs";
import {
  buildCohortRecoveryContextFromLedger,
  revalidatePersistedCohortRecoveryAttempt,
} from "./opsx-post-implement-recovery-cohort.mjs";

const RED_PROVEN_CLASSIFICATION = "INTENDED_BEHAVIOR_MISSING";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const GAP_PRODUCT_IMPLEMENTATION = "PRODUCT_IMPLEMENTATION_GAP";
export const GAP_PROCESS_COMPLETION_PROJECTION = "PROCESS_COMPLETION_PROJECTION_GAP";
export const GAP_TEST_COVERAGE_ARTIFACT = "TEST_COVERAGE_ARTIFACT_GAP";
export const GAP_STALE_EVIDENCE = "STALE_EVIDENCE";

export const TASK_COMPLETE = "COMPLETE";
export const TASK_INCOMPLETE = "INCOMPLETE";
export const TASK_STALE = "STALE_EVIDENCE";

export const COVERAGE_RECONCILED = "COVERAGE_RECONCILED";
export const TASK_COMPLETION_POLICY = "SPLIT_APPLY_CURRENT_GREEN";

const RECOVERY_PHASE_RANK = Object.freeze({
  CLASSIFICATION: 0,
  AUTHORITY: 1,
  NEW_GENERATION: 2,
  FRESH_EVIDENCE: 3,
  IMPL_OK: 4,
  SUCCESSOR_PLAN: 5,
});

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function isTestArtifactPath(path) {
  const p = posixPath(path).split(/\s+/)[0];
  if (!p) return false;
  if (!looksLikeTestPath(p)) return false;
  return /\.(test\.(mjs|js|cjs)|py)$/.test(p) || /(^|\/)test_[^/]+\.py$/.test(p);
}

export function extractTestArtifactPaths(raw) {
  const out = [];
  for (const part of String(raw || "").split(/[;,]/)) {
    const token = part.trim().match(/^(\S+\.(?:test\.(?:mjs|js|cjs)|py))/);
    if (token && isTestArtifactPath(token[1])) out.push(posixPath(token[1]));
  }
  return [...new Set(out)];
}

/**
 * Parse scenario-coverage plan.md required_test_artifacts and optional
 * equivalent_evidence_allowed. Exact filename is required unless the plan
 * block explicitly allows equivalence.
 */
export function parseScenarioPlanTestObligations(planText) {
  const text = String(planText || "");
  const scenarios = [];
  const headingRe =
    /###\s+\d+\.\s+`Scenario:\s*([^`]+)`/g;
  const matches = [...text.matchAll(headingRe)];
  for (let i = 0; i < matches.length; i += 1) {
    const title = String(matches[i][1] || "").trim();
    const start = matches[i].index;
    const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
    const block = text.slice(start, end);
    const artRow = block.match(
      /\|\s*required_test_artifacts\s*\|\s*([^|\n]+)\|/i,
    );
    const eqRow = block.match(
      /\|\s*equivalent_evidence_allowed\s*\|\s*([^|\n]+)\|/i,
    );
    const eqRaw = eqRow ? String(eqRow[1]).trim().toLowerCase() : "";
    const equivalentAllowed =
      eqRaw === "yes" ||
      eqRaw === "true" ||
      /\bequivalent\b/.test(eqRaw);
    scenarios.push({
      title,
      required_test_artifacts: artRow
        ? extractTestArtifactPaths(artRow[1])
        : [],
      equivalent_evidence_allowed: equivalentAllowed,
    });
  }
  return scenarios;
}

function citedScenarioTitle(ref) {
  const raw = String(ref || "");
  const hash = raw.match(/scenario-coverage\/plan\.md#(.+)$/i);
  if (!hash) return "";
  return String(hash[1])
    .replace(/^Scenario:\s*/i, "")
    .trim();
}

function titlesMatch(a, b) {
  const na = String(a || "")
    .replace(/^Scenario:\s*/i, "")
    .trim()
    .toLowerCase();
  const nb = String(b || "")
    .replace(/^Scenario:\s*/i, "")
    .trim()
    .toLowerCase();
  return Boolean(na) && na === nb;
}

/**
 * Test artifacts mapped to one slice:
 * 1. test files in `red_test_targets_or_seams`
 * 2. scenario-plan required artifacts cited by this slice that no other
 *    slice lists in its red_test_targets (exclusive obligation)
 *
 * Future-slice files cited by a shared scenario are not this slice's RED.
 */
export function collectSlicePlannedTestArtifacts({
  design,
  slice,
  scenarioPlanText = "",
} = {}) {
  if (!isObject(slice)) return [];
  const fromSeams = (slice.red_test_targets_or_seams || [])
    .map((p) => posixPath(String(p).split(/\s+/)[0]))
    .filter((p) => isTestArtifactPath(p));
  const scenarios = parseScenarioPlanTestObligations(scenarioPlanText);
  const cited = (slice.requirement_refs || [])
    .map(citedScenarioTitle)
    .filter(Boolean);
  const exclusive = [];
  const otherTargets = new Set();
  for (const other of design?.slices || []) {
    if (other.slice_id === slice.slice_id) continue;
    for (const p of other.red_test_targets_or_seams || []) {
      const path = posixPath(String(p).split(/\s+/)[0]);
      if (isTestArtifactPath(path)) otherTargets.add(path);
    }
  }
  for (const title of cited) {
    const sc = scenarios.find((s) => titlesMatch(s.title, title));
    if (!sc) continue;
    for (const art of sc.required_test_artifacts) {
      if (fromSeams.includes(art)) continue;
      if (!otherTargets.has(art)) exclusive.push(art);
    }
  }
  return [...new Set([...fromSeams, ...exclusive])].sort();
}

export function scenarioAllowsEquivalentEvidence({
  scenarioPlanText = "",
  artifactPath,
} = {}) {
  const p = posixPath(artifactPath);
  for (const sc of parseScenarioPlanTestObligations(scenarioPlanText)) {
    if (sc.required_test_artifacts.includes(p) && sc.equivalent_evidence_allowed) {
      return true;
    }
  }
  return false;
}

export function redMissingPlannedTestArtifacts({
  test_paths = [],
  design,
  slice,
  scenarioPlanText = "",
} = {}) {
  const required = collectSlicePlannedTestArtifacts({
    design,
    slice,
    scenarioPlanText,
  });
  const have = new Set((test_paths || []).map((p) => posixPath(p)));
  return required.filter((p) => !have.has(p));
}

function coveringSlices(design, taskId) {
  return (design?.slices || []).filter((s) =>
    (s.source_task_ids || []).includes(taskId),
  );
}

function greenStatusForSlice(sliceId, greenBySlice, upstreamBySlice) {
  const green = greenBySlice?.[sliceId];
  const upstream = upstreamBySlice?.[sliceId] || {};
  const freshness = classifyGreenEvidenceFreshness(green, upstream);
  if (freshness.current) return "GREEN_PROVEN";
  if (green?.status === "GREEN_PROVEN") return "STALE_GREEN";
  return "NOT_GREEN";
}

/**
 * Derived task completion. Does not read or mutate tasks.md checkboxes.
 * Stale GREEN does not count.
 */
export function projectSplitApplyTaskCompletion({
  design,
  greenBySlice = {},
  upstreamBySlice = {},
  durableProjectionPresent = false,
} = {}) {
  const tasks = [];
  const seen = new Set();
  for (const slice of design?.slices || []) {
    for (const taskId of slice.source_task_ids || []) {
      if (seen.has(taskId)) continue;
      seen.add(taskId);
      const covering = coveringSlices(design, taskId);
      const current_green_evidence = covering.map((s) => ({
        slice_id: s.slice_id,
        status: greenStatusForSlice(s.slice_id, greenBySlice, upstreamBySlice),
      }));
      let completion_status = TASK_COMPLETE;
      if (current_green_evidence.length === 0) {
        completion_status = TASK_INCOMPLETE;
      } else if (current_green_evidence.some((e) => e.status === "NOT_GREEN")) {
        completion_status = TASK_INCOMPLETE;
      } else if (current_green_evidence.some((e) => e.status === "STALE_GREEN")) {
        completion_status = TASK_STALE;
      }
      tasks.push({
        task_id: taskId,
        covering_slice_ids: covering.map((s) => s.slice_id),
        current_green_evidence,
        completion_status,
      });
    }
  }
  const allSlicesCurrent = (design?.slice_order?.nodes || []).every(
    (id) => greenStatusForSlice(id, greenBySlice, upstreamBySlice) === "GREEN_PROVEN",
  );
  const apply_complete =
    allSlicesCurrent &&
    tasks.length > 0 &&
    tasks.every((t) => t.completion_status === TASK_COMPLETE);
  return {
    policy: TASK_COMPLETION_POLICY,
    checkbox_authority: "none",
    planning_tasks_frozen: true,
    apply_complete,
    durable_projection_present: Boolean(durableProjectionPresent),
    tasks,
  };
}

/**
 * R3: coverage-reconcile authority — structural validate + live re-exec.
 */
export function evaluateCoverageReconcileAuthority({ artifact, repoRoot } = {}) {
  const structural = validateCoverageReconcileArtifact(artifact);
  if (!structural.ok) {
    return { ok: false, errors: structural.errors, phase: "structural" };
  }
  const errors = [];
  const execs = artifact.executions || [];
  for (const ex of execs) {
    const cmd = ex.command || ex.test_command;
    if (!cmd) {
      errors.push({ code: "R3_HISTORICAL", message: "execution missing command" });
      continue;
    }
    const live = runFrozenRedCommand(cmd, { cwd: repoRoot, timeoutMs: 120000 });
    if (ex.classification === "POST_GREEN_PASS" || ex.classification === "ALREADY_SATISFIED") {
      if (live.exit_code !== 0) {
        errors.push({
          code: "R3_HISTORICAL",
          message: "POST_GREEN_PASS / pass classification requires live exit 0",
        });
      }
    }
    if (ex.classification === RED_PROVEN_CLASSIFICATION && live.exit_code === 0) {
      errors.push({
        code: "R3_HISTORICAL",
        message: "cannot claim INTENDED_BEHAVIOR_MISSING with live exit 0",
      });
    }
    if (ex.exit_code !== undefined && ex.exit_code !== live.exit_code) {
      errors.push({
        code: "R3_HISTORICAL",
        message: "reconcile exit_code disagrees with live execution",
      });
    }
  }
  return { ok: errors.length === 0, errors, phase: "authority" };
}

/**
 * Честный coverage-reconcile: structural flags + authority re-exec (R3).
 */
export function isHonestCoverageReconcile(rec, artifactPath, repoRoot = process.cwd()) {
  if (!isObject(rec) || rec.status !== COVERAGE_RECONCILED) return false;
  if (rec.historical_red_fabricated === true) return false;
  if (rec.original_red_mutated === true) return false;
  const paths = (rec.test_paths || []).map((p) => posixPath(p));
  if (!paths.includes(posixPath(artifactPath))) return false;
  const structural = validateCoverageReconcileArtifact(rec);
  if (!structural.ok) return false;
  const auth = evaluateCoverageReconcileAuthority({ artifact: rec, repoRoot });
  return auth.ok;
}

/**
 * Classify impl-verify gaps for a split-APPLY change.
 * Unchecked tasks.md never produces a gap by itself.
 */
export function classifySplitApplyImplGaps({
  design,
  taskProjection,
  scenarioPlanText = "",
  redBySlice = {},
  coverageReconcileBySlice = {},
  equivalentEvidenceByArtifact = {},
} = {}) {
  const gaps = [];
  for (const t of taskProjection?.tasks || []) {
    if (t.completion_status === TASK_INCOMPLETE) {
      gaps.push({
        class: GAP_PRODUCT_IMPLEMENTATION,
        task_id: t.task_id,
        covering_slice_ids: t.covering_slice_ids,
        detail: "covering slice is not CURRENT GREEN_PROVEN",
      });
    }
    if (t.completion_status === TASK_STALE) {
      gaps.push({
        class: GAP_STALE_EVIDENCE,
        task_id: t.task_id,
        covering_slice_ids: t.covering_slice_ids,
        detail: "covering GREEN is STALE_GREEN",
      });
    }
  }
  if (
    taskProjection?.apply_complete &&
    taskProjection.durable_projection_present === false
  ) {
    gaps.push({
      class: GAP_PROCESS_COMPLETION_PROJECTION,
      detail: "APPLY_COMPLETE from CURRENT GREEN; durable TASK-COMPLETION.json absent",
    });
  }
  for (const slice of design?.slices || []) {
    const required = collectSlicePlannedTestArtifacts({
      design,
      slice,
      scenarioPlanText,
    });
    const redPaths = new Set(
      (redBySlice[slice.slice_id]?.test_paths || []).map((p) => posixPath(p)),
    );
    const reconcile = coverageReconcileBySlice[slice.slice_id];
    for (const art of required) {
      if (redPaths.has(art)) continue;
      if (isHonestCoverageReconcile(reconcile, art)) continue;
      if (
        scenarioAllowsEquivalentEvidence({
          scenarioPlanText,
          artifactPath: art,
        }) &&
        equivalentEvidenceByArtifact[art]
      ) {
        continue;
      }
      gaps.push({
        class: GAP_TEST_COVERAGE_ARTIFACT,
        slice_id: slice.slice_id,
        artifact: art,
        detail:
          "planned exact test artifact required and not in frozen RED or honest coverage-reconcile",
      });
    }
  }
  const blocking = gaps.filter(
    (g) => g.class !== GAP_PROCESS_COMPLETION_PROJECTION,
  );
  const marker = blocking.length ? "IMPL_GAPS" : "IMPL_OK";
  return { gaps, blocking, marker };
}

function recoveryAttemptsDir(repoRoot, changeRoot) {
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  return resourcePath(repoRoot, cr, "opsx", "post-implement-recovery");
}

/**
 * Выбрать активную recovery-попытку по durable LEDGER.json (без мутации receipts).
 */
export function discoverRecoveryAttemptId({
  repoRoot,
  changeRoot,
  changeId = null,
} = {}) {
  const root = recoveryAttemptsDir(repoRoot, changeRoot);
  if (!existsSync(root)) {
    return null;
  }
  let best = null;
  for (const name of readdirSync(root, { withFileTypes: true })) {
    if (!name.isDirectory()) continue;
    const attemptId = name.name;
    const ledgerPath = resourcePath(root, attemptId, "LEDGER.json");
    if (!existsSync(ledgerPath)) continue;
    let ledger;
    try {
      ledger = JSON.parse(readFileSync(ledgerPath, "utf8"));
    } catch {
      continue;
    }
    if (changeId && ledger.change_id && ledger.change_id !== changeId) {
      continue;
    }
    const phaseRank = RECOVERY_PHASE_RANK[ledger.phase] ?? -1;
    const score =
      (ledger.terminal === true ? 1_000_000 : 0) +
      phaseRank * 10_000 +
      (ledger.impl_ok === true ? 1_000 : 0);
    if (!best || score > best.score) {
      best = { attemptId, score, ledger };
    }
  }
  return best?.attemptId || null;
}

/**
 * Persisted recovery attempt + generation lineage state for completion wiring.
 */
export function loadRecoveryCompletionContextFromDisk({
  repoRoot,
  changeRoot,
  recoveryAttemptId = null,
  changeId = null,
} = {}) {
  const attemptId =
    recoveryAttemptId ||
    discoverRecoveryAttemptId({ repoRoot, changeRoot, changeId });
  if (!attemptId) {
    return {
      ok: false,
      code: "RECOVERY_ATTEMPT_ABSENT",
      recovery_attempt_id: null,
      generation_current: false,
    };
  }
  const loaded = loadPersistedRecoveryAttempt({
    repoRoot,
    changeRoot,
    recoveryAttemptId: attemptId,
  });
  if (!loaded.ok) {
    return {
      ok: false,
      code: loaded.code,
      recovery_attempt_id: attemptId,
      generation_current: false,
    };
  }
  const freshEvidence =
    loaded.freshEvidence ||
    loaded.ledger?.fresh_evidence_record ||
    loaded.ledger?.fresh_evidence ||
    null;
  const generationCurrent = isRecoveryGenerationCurrent({
    ledger: loaded.ledger,
    generation: loaded.generation,
    freshEvidence,
  });
  return {
    ok: true,
    recovery_attempt_id: attemptId,
    ledger: loaded.ledger,
    generation: loaded.generation,
    fresh_evidence: freshEvidence,
    generation_current: generationCurrent,
    recovered_slice_id: loaded.ledger?.slice_id || null,
    generation_lineage_id: loaded.ledger?.generation_lineage_id || null,
  };
}

export { isRecoveryGenerationCurrent };

function gapTouchesSlice(gap, sliceId) {
  if (!sliceId) return false;
  if (gap.slice_id === sliceId) return true;
  return (gap.covering_slice_ids || []).includes(sliceId);
}

/**
 * STALE_EVIDENCE по recovered slice не блокирует recovery completion consumer,
 * когда generation current и fresh evidence уже на диске.
 */
export function filterBlockingForRecoveryConsumer({
  blocking = [],
  recoveryContext = null,
} = {}) {
  if (!recoveryContext?.generation_current) {
    return blocking;
  }
  const sliceId = recoveryContext.recovered_slice_id;
  return (blocking || []).filter(
    (g) => !(g.class === GAP_STALE_EVIDENCE && gapTouchesSlice(g, sliceId)),
  );
}

function durableTaskCompletionPresence(repoRoot, changeRoot) {
  const projectionPath = resolve(
    repoRoot,
    posixPath(changeRoot),
    "apply-complete/TASK-COMPLETION.json",
  );
  if (!existsSync(projectionPath)) {
    return { durable_projection_present: false, durable_apply_complete_claimed: false };
  }
  let durable_apply_complete_claimed = false;
  try {
    const doc = JSON.parse(readFileSync(projectionPath, "utf8"));
    durable_apply_complete_claimed = doc?.apply_complete === true;
  } catch {
    durable_apply_complete_claimed = false;
  }
  return {
    durable_projection_present: true,
    durable_apply_complete_claimed,
  };
}

export function recommendSplitApplyImplNextAction({
  design,
  blocking = [],
  changeId = "",
  changeRoot = null,
  scaffoldBySlice = {},
  redBySlice = {},
  implementBySlice = {},
  greenBySlice = {},
  upstreamBySlice = {},
  repoRoot = DEFAULT_ROOT,
  recoveryLedger = null,
  recoveryAttemptId = null,
  recoveryContext = null,
  taskProjection = null,
} = {}) {
  const id = changeId || design?.change_id || "<change>";
  if (!isObject(design)) {
    return {
      command: `/opsx-apply ${id}`,
      legacy_apply_required: true,
      reason: "no split-APPLY DESIGN",
    };
  }
  const product = blocking.filter((g) => g.class === GAP_PRODUCT_IMPLEMENTATION);
  let stale = blocking.filter((g) => g.class === GAP_STALE_EVIDENCE);
  const coverage = blocking.filter((g) => g.class === GAP_TEST_COVERAGE_ARTIFACT);
  const attemptId =
    recoveryAttemptId ||
    recoveryContext?.recovery_attempt_id ||
    recoveryLedger?.recovery_attempt_id ||
    null;
  let resolvedRecovery = recoveryContext;
  if (
    (!resolvedRecovery || resolvedRecovery.generation_current !== true) &&
    attemptId &&
    changeRoot &&
    repoRoot
  ) {
    const loaded = loadRecoveryCompletionContextFromDisk({
      repoRoot,
      changeRoot,
      recoveryAttemptId: attemptId,
      changeId: design?.change_id,
    });
    if (loaded.ok) {
      resolvedRecovery = loaded;
    }
  }
  const ledger = recoveryLedger || resolvedRecovery?.ledger || null;
  const generationCurrent =
    resolvedRecovery?.generation_current === true ||
    isRecoveryGenerationCurrent({
      ledger,
      generation: resolvedRecovery?.generation,
      freshEvidence:
        resolvedRecovery?.fresh_evidence ||
        ledger?.fresh_evidence_record ||
        ledger?.fresh_evidence,
    });
  if (generationCurrent) {
    stale = filterBlockingForRecoveryConsumer({
      blocking: stale,
      recoveryContext: {
        generation_current: true,
        recovered_slice_id:
          resolvedRecovery?.recovered_slice_id || ledger?.slice_id || null,
      },
    });
  }
  if (attemptId && changeRoot) {
    const consumer = evaluateRecoveryCompletionConsumer({
      design,
      repoRoot,
      changeRoot,
      recoveryAttemptId: attemptId,
      callerTaskProjection: taskProjection,
      scaffoldBySlice,
      redBySlice,
      implementBySlice,
      greenBySlice,
      upstreamBySlice,
    });
    if (!consumer.ok) {
      const recoveryCode = consumer.code || "RECOVERY_CONSUMER_FAILED";
      return {
        command: null,
        legacy_apply_required: false,
        reason: recoveryCode,
        recovery_pause: true,
        recovery_code: recoveryCode,
        recovery_consumer_failed: true,
      };
    }
    const lineageCurrent = isRecoveryGenerationCurrent({
      ledger: consumer.ledger,
      generation: consumer.generation,
      freshEvidence: consumer.fresh_evidence,
    });
    if (!lineageCurrent) {
      return {
        command: null,
        legacy_apply_required: false,
        reason: "RECOVERY_CONSUMER_FAILED",
        recovery_pause: true,
        recovery_code: "RECOVERY_GENERATION_NOT_CURRENT",
        recovery_consumer_failed: true,
      };
    }
    const successor = consumer.successor_plan;
    if (
      successor?.ok &&
      successor.phase === RECOVERY_PHASE_SUCCESSOR_PLAN &&
      successor.published_disk_supersession === true
    ) {
      return {
        command: successor.command,
        legacy_apply_required: false,
        reason: "IMPL_OK",
        recovery_successor: true,
        generation_lineage_id: successor.generation_lineage_id,
        bounded_verify_mechanism: successor.mechanism,
        native_invocation: successor.native_invocation,
        impl_ok_recomputed: true,
      };
    }
    return {
      command: null,
      legacy_apply_required: false,
      reason: successor?.code || "RECOVERY_SUCCESSOR_PUBLICATION_FAILED",
      recovery_pause: true,
      recovery_code: successor?.code || "RECOVERY_SUCCESSOR_PUBLICATION_FAILED",
      recovery_consumer_failed: true,
    };
  }
  if (stale.length) {
    const slice = stale[0].covering_slice_ids?.[0] || stale[0].slice_id;
    const greenPick = selectNextGreenableSlice({
      design,
      scaffoldBySlice,
      redBySlice,
      implementBySlice,
      greenBySlice,
      repoRoot,
    });
    if (
      !greenPick.ok &&
      (greenPick.disposition === "PAUSE" ||
        greenPick.code === "TEST_CONTRACT_REBASELINE_REQUIRED" ||
        greenPick.code === "FROZEN_TEST_PATH_MISSING" ||
        greenPick.code === "IDENTITY_ALGORITHM_UNRESOLVED")
    ) {
      return {
        command: null,
        slice_id: greenPick.slice_id || slice || null,
        legacy_apply_required: false,
        reason: GAP_STALE_EVIDENCE,
        recovery_pause: true,
        recovery_code: greenPick.code,
        disposition: greenPick.disposition,
      };
    }
    if (!greenPick.ok && greenPick.code === "GREEN_REFRESH_REQUIRED") {
      return {
        command: greenPick.next_command || `/opsx-apply-green change=${id}`,
        slice_id: greenPick.slice_id || slice || null,
        legacy_apply_required: false,
        reason: GAP_STALE_EVIDENCE,
      };
    }
    return {
      command: `/opsx-apply-green change=${id}`,
      slice_id: slice || null,
      legacy_apply_required: false,
      reason: GAP_STALE_EVIDENCE,
    };
  }
  if (coverage.length) {
    const sliceId = coverage[0].slice_id;
    const greenCurrent = greenBySlice[sliceId]?.status === "GREEN_PROVEN";
    if (greenCurrent) {
      return {
        command: `/opsx-apply-red change=${id} mode=coverage-reconcile slice=${sliceId}`,
        slice_id: sliceId,
        legacy_apply_required: false,
        reason: GAP_TEST_COVERAGE_ARTIFACT,
      };
    }
    return {
      command: `/opsx-apply-red change=${id}`,
      slice_id: sliceId,
      legacy_apply_required: false,
      reason: GAP_TEST_COVERAGE_ARTIFACT,
    };
  }
  if (product.length) {
    const stage = selectNextPipelineStage({
      design,
      scaffoldBySlice,
      redBySlice,
      implementBySlice,
      greenBySlice,
      repoRoot,
    });
    if (stage.next_stage === "RECOVERY_PAUSED") {
      return {
        command: null,
        slice_id: stage.next_slice || null,
        next_stage: stage.next_stage,
        legacy_apply_required: false,
        reason: GAP_PRODUCT_IMPLEMENTATION,
        recovery_pause: true,
        recovery_code: stage.recovery_code || stage.selector_parity?.code,
      };
    }
    const hop =
      stage.next_stage === "APPLY_SCAFFOLD"
        ? "/opsx-apply-scaffold"
        : stage.next_stage === "APPLY_RED"
          ? "/opsx-apply-red"
          : stage.next_stage === "APPLY_IMPLEMENT"
            ? "/opsx-apply-implement"
            : stage.next_stage === "APPLY_GREEN"
              ? "/opsx-apply-green"
              : "/opsx-apply-green";
    return {
      command: `${hop} change=${id}`,
      slice_id: stage.next_slice || null,
      next_stage: stage.next_stage,
      legacy_apply_required: false,
      reason: GAP_PRODUCT_IMPLEMENTATION,
    };
  }
  if (
    attemptId &&
    ledger?.successor_plan?.ok &&
    ledger?.phase === RECOVERY_PHASE_SUCCESSOR_PLAN
  ) {
    return {
      command: null,
      legacy_apply_required: false,
      reason: "RECOVERY_CONSUMER_FAILED",
      recovery_pause: true,
      recovery_code: "RECOVERY_CONSUMER_FAILED",
      recovery_consumer_failed: true,
    };
  }
  const successor = recommendPostImplementRecoverySuccessorPlan({
    design,
    changeId: id,
    changeRoot,
    repoRoot,
    recoveryLedger: attemptId ? null : recoveryLedger,
    taskProjection,
    callerTaskProjection: taskProjection,
    redBySlice,
    greenBySlice,
    upstreamBySlice,
  });
  if (
    successor.ok &&
    successor.phase === RECOVERY_PHASE_SUCCESSOR_PLAN &&
    successor.published_disk_supersession === true
  ) {
    return {
      command: successor.command,
      legacy_apply_required: false,
      reason: "IMPL_OK",
      recovery_successor: true,
      generation_lineage_id: successor.generation_lineage_id,
      bounded_verify_mechanism: successor.mechanism,
      native_invocation: successor.native_invocation,
      impl_ok_recomputed: successor.impl_ok_recomputed === true,
    };
  }
  return {
    command: `/opsx-verify mode=archive ${id}`,
    legacy_apply_required: false,
    reason: "IMPL_OK",
  };
}

export function validateCoverageReconcileArtifact(artifact) {
  const errors = [];
  if (!isObject(artifact)) {
    return { ok: false, errors: [{ code: "type", message: "object required" }] };
  }
  if (artifact.schema !== "opsx.apply_coverage_reconcile") {
    errors.push({ code: "schema", message: "schema must be opsx.apply_coverage_reconcile" });
  }
  if (artifact.status !== COVERAGE_RECONCILED) {
    errors.push({ code: "status", message: "status must be COVERAGE_RECONCILED" });
  }
  if (artifact.historical_red_fabricated !== false) {
    errors.push({
      code: "historical_red_fabricated",
      message: "must be false — do not backdate RED",
    });
  }
  if (artifact.original_red_mutated !== false) {
    errors.push({
      code: "original_red_mutated",
      message: "must not rewrite original RED.json",
    });
  }
  if (artifact.discovered_after !== "GREEN_PROVEN") {
    errors.push({
      code: "discovered_after",
      message: "post-GREEN discovery must record discovered_after=GREEN_PROVEN",
    });
  }
  if (!Array.isArray(artifact.test_paths) || artifact.test_paths.length === 0) {
    errors.push({ code: "test_paths", message: "reconcile requires test_paths" });
  }
  const execs = artifact.executions || [];
  if (!Array.isArray(execs) || execs.length === 0) {
    errors.push({ code: "executions", message: "reconcile requires executions" });
  }
  for (const ex of execs) {
    if (ex.classification === "INTENDED_BEHAVIOR_MISSING" && ex.exit_code !== 0) {
      errors.push({
        code: "fabricated_red",
        message: "do not claim historical RED for a post-GREEN test",
      });
    }
  }
  return { ok: errors.length === 0, errors };
}

function buildUpstreamBySlice(maps) {
  const binding = maps.boundedVerifyBinding ?? null;
  const bindingBySlice = maps.boundedVerifyBindingBySlice || {};
  const out = {};
  for (const id of Object.keys(maps.greenBySlice || {})) {
    out[id] = {
      design: maps.design,
      scaffold: maps.scaffoldBySlice[id],
      red: maps.redBySlice[id],
      implement: maps.implementBySlice[id],
      designArtifactSha256: maps.designArtifactSha256,
      scaffoldArtifactSha256: maps.scaffoldArtifactSha256BySlice?.[id],
      redArtifactSha256: maps.redArtifactSha256BySlice?.[id],
      implementArtifactSha256: maps.implementArtifactSha256BySlice?.[id],
      frozenTestContentIdentity: maps.frozenTestContentBySlice?.[id],
      boundedVerifyBinding: bindingBySlice[id] ?? binding ?? undefined,
    };
  }
  for (const id of Object.keys(maps.implementBySlice || {})) {
    if (!out[id]) {
      out[id] = {
        design: maps.design,
        scaffold: maps.scaffoldBySlice[id],
        red: maps.redBySlice[id],
        implement: maps.implementBySlice[id],
        designArtifactSha256: maps.designArtifactSha256,
        scaffoldArtifactSha256: maps.scaffoldArtifactSha256BySlice?.[id],
        redArtifactSha256: maps.redArtifactSha256BySlice?.[id],
        implementArtifactSha256: maps.implementArtifactSha256BySlice?.[id],
        frozenTestContentIdentity: maps.frozenTestContentBySlice?.[id],
        boundedVerifyBinding: bindingBySlice[id] ?? binding ?? undefined,
      };
    }
  }
  return out;
}

function loadCoverageReconcileBySlice(repoRoot, changeRoot, sliceIds) {
  const out = {};
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  for (const id of sliceIds || []) {
    const rel = `${cr}/apply-red/${id}/COVERAGE-RECONCILE.json`;
    const abs = resourcePath(repoRoot, rel);
    if (existsSync(abs)) {
      out[id] = JSON.parse(readFileSync(abs, "utf8"));
    }
  }
  return out;
}

export function evaluateSplitApplyVerifyCompletionFromDisk({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  recoveryAttemptId = null,
} = {}) {
  const absDesign = resourcePath(repoRoot, posixPath(changeRoot), "apply-design/APPLY-DESIGN.json");
  if (!existsSync(absDesign)) {
    return {
      split_apply: false,
      marker: null,
      legacy_apply_required: true,
    };
  }
  const design = JSON.parse(readFileSync(absDesign, "utf8"));
  const sliceIds = design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id);
  const maps = loadChangeReceiptMaps(repoRoot, changeRoot, sliceIds);
  const boundedVerifyBinding = loadBoundedVerifyBindingFromDisk(repoRoot, changeRoot);
  const upstreamBySlice = buildUpstreamBySlice({
    ...maps,
    boundedVerifyBinding,
  });
  const durablePresence = durableTaskCompletionPresence(repoRoot, changeRoot);
  const taskProjection = projectSplitApplyTaskCompletion({
    design: maps.design || design,
    greenBySlice: maps.greenBySlice,
    upstreamBySlice,
    durableProjectionPresent: durablePresence.durable_projection_present,
  });
  const recoveryContext = loadRecoveryCompletionContextFromDisk({
    repoRoot,
    changeRoot,
    changeId: design.change_id,
    recoveryAttemptId,
  });
  const planPath = resolve(
    repoRoot,
    posixPath(changeRoot),
    "scenario-coverage/plan.md",
  );
  const scenarioPlanText = existsSync(planPath)
    ? readFileSync(planPath, "utf8")
    : "";
  const coverageReconcileBySlice = loadCoverageReconcileBySlice(
    repoRoot,
    changeRoot,
    sliceIds,
  );
  const classified = classifySplitApplyImplGaps({
    design: maps.design || design,
    taskProjection,
    scenarioPlanText,
    redBySlice: maps.redBySlice,
    coverageReconcileBySlice,
  });
  const blocking = filterBlockingForRecoveryConsumer({
    blocking: classified.blocking,
    recoveryContext,
  });
  const marker = blocking.length ? "IMPL_GAPS" : "IMPL_OK";
  const next = recommendSplitApplyImplNextAction({
    design: maps.design || design,
    blocking,
    changeId: design.change_id,
    changeRoot,
    repoRoot,
    scaffoldBySlice: maps.scaffoldBySlice,
    redBySlice: maps.redBySlice,
    implementBySlice: maps.implementBySlice,
    greenBySlice: maps.greenBySlice,
    upstreamBySlice,
    recoveryContext,
    recoveryLedger: recoveryContext.ok ? recoveryContext.ledger : null,
    recoveryAttemptId: recoveryContext.recovery_attempt_id,
    taskProjection,
  });
  const durable_apply_complete_mismatch =
    durablePresence.durable_apply_complete_claimed === true &&
    taskProjection.apply_complete !== true;
  return {
    split_apply: true,
    change_id: design.change_id,
    task_projection: taskProjection,
    gaps: classified.gaps,
    blocking,
    marker,
    recovery: recoveryContext.ok
      ? {
          recovery_attempt_id: recoveryContext.recovery_attempt_id,
          generation_current: recoveryContext.generation_current,
          generation_lineage_id: recoveryContext.generation_lineage_id,
          recovered_slice_id: recoveryContext.recovered_slice_id,
          phase: recoveryContext.ledger?.phase || null,
        }
      : {
          recovery_attempt_id: recoveryContext.recovery_attempt_id,
          generation_current: false,
          code: recoveryContext.code || null,
        },
    durable_projection_present: durablePresence.durable_projection_present,
    durable_apply_complete_mismatch,
    next_action: next,
    legacy_apply_required: next.legacy_apply_required,
  };
}

/**
 * Completion CLI with optional recovery consumer (--recovery-attempt-id).
 */
export function evaluateApplyVerifyCompletionCli({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  recoveryAttemptId = null,
  callerApplyComplete = null,
} = {}) {
  const base = evaluateSplitApplyVerifyCompletionFromDisk({
    repoRoot,
    changeRoot,
  });
  if (!recoveryAttemptId) {
    return { ...base, ok: base.marker === "IMPL_OK" };
  }
  const designPath = resourcePath(repoRoot, posixPath(changeRoot), "apply-design/APPLY-DESIGN.json");
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const sliceIds =
    design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id).filter(Boolean);
  const maps = loadChangeReceiptMaps(repoRoot, changeRoot, sliceIds);
  const callerTaskProjection =
    callerApplyComplete === true || callerApplyComplete === "true"
      ? { apply_complete: true, durable_projection_present: true }
      : null;
  const persisted = loadPersistedRecoveryAttempt({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
  });
  if (!persisted.ok) {
    return {
      ok: false,
      code: persisted.code,
      recovery_consumer: { invoked: true },
      recovery_consumer_failed: true,
      next_action: {
        reason: persisted.code,
        impl_ok_recomputed: false,
        recovery_successor: false,
        command: null,
        recovery_pause: true,
      },
      split_apply: base.split_apply,
      change_id: base.change_id,
    };
  }
  const cohortRevalidate = revalidatePersistedCohortRecoveryAttempt({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    ledger: persisted.ledger,
    redBySlice: maps.redBySlice,
    implementBySlice: maps.implementBySlice,
    greenBySlice: maps.greenBySlice,
  });
  if (!cohortRevalidate.ok) {
    return {
      ok: false,
      code: cohortRevalidate.code,
      recovery_consumer: { invoked: true },
      recovery_consumer_failed: true,
      next_action: {
        reason: cohortRevalidate.code,
        impl_ok_recomputed: false,
        recovery_successor: false,
        command: null,
        recovery_pause: true,
      },
      split_apply: base.split_apply,
      change_id: base.change_id,
    };
  }
  const cohortRecovery = buildCohortRecoveryContextFromLedger({
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    ledger: persisted.ledger,
  });
  const consumer = evaluateRecoveryCompletionConsumer({
    design: maps.design || design,
    repoRoot,
    changeRoot,
    recoveryAttemptId,
    callerTaskProjection,
    scaffoldBySlice: maps.scaffoldBySlice,
    redBySlice: maps.redBySlice,
    implementBySlice: maps.implementBySlice,
    greenBySlice: maps.greenBySlice,
    upstreamBySlice: maps.upstreamBySlice,
    cohortRecovery,
  });
  const recovery_consumer = { invoked: true };
  if (!consumer.ok) {
    const recoveryCode = consumer.code || "RECOVERY_CONSUMER_FAILED";
    return {
      ok: false,
      code: recoveryCode,
      recovery_consumer,
      recovery_consumer_failed: true,
      next_action: {
        reason: recoveryCode,
        impl_ok_recomputed: false,
        recovery_successor: false,
        command: null,
        recovery_pause: true,
      },
      split_apply: base.split_apply,
      change_id: base.change_id,
    };
  }
  const lineageCurrent = isRecoveryGenerationCurrent({
    ledger: consumer.ledger,
    generation: consumer.generation,
    freshEvidence: consumer.fresh_evidence,
  });
  if (!lineageCurrent) {
    return {
      ok: false,
      code: "RECOVERY_GENERATION_NOT_CURRENT",
      recovery_consumer,
      recovery_consumer_failed: true,
      next_action: {
        reason: "RECOVERY_CONSUMER_FAILED",
        impl_ok_recomputed: false,
        recovery_successor: false,
        command: null,
        recovery_pause: true,
      },
      split_apply: base.split_apply,
      change_id: base.change_id,
    };
  }
  const successor = consumer.successor_plan;
  if (successor?.published_disk_supersession !== true || successor?.ok !== true) {
    return {
      ok: false,
      code: successor?.code || "RECOVERY_SUCCESSOR_PUBLICATION_FAILED",
      recovery_consumer,
      recovery_consumer_failed: true,
      next_action: {
        reason: "RECOVERY_SUCCESSOR_PUBLICATION_FAILED",
        impl_ok_recomputed: false,
        recovery_successor: false,
        command: null,
        recovery_pause: true,
      },
      split_apply: base.split_apply,
      change_id: base.change_id,
    };
  }
  const next_action = {
    reason: "IMPL_OK",
    impl_ok_recomputed: successor.impl_ok_recomputed === true,
    recovery_successor: true,
    command: successor.command || null,
    generation_lineage_id: successor.generation_lineage_id || null,
  };
  return {
    ok: true,
    recovery_consumer,
    next_action,
    split_apply: base.split_apply,
    change_id: base.change_id,
    marker: "IMPL_OK",
  };
}

function main(argv) {
  const rootFlag = argv.indexOf("--change-root");
  const changeRoot = rootFlag >= 0 ? argv[rootFlag + 1] : argv[0];
  const attemptFlag = argv.indexOf("--recovery-attempt-id");
  const recoveryAttemptId = attemptFlag >= 0 ? argv[attemptFlag + 1] : null;
  const callerFlag = argv.indexOf("--caller-apply-complete");
  const callerApplyComplete = callerFlag >= 0 ? argv[callerFlag + 1] : null;
  if (!changeRoot) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "missing_change_root" }] }) + "\n",
    );
    process.exit(1);
  }
  if (recoveryAttemptId) {
    const result = evaluateApplyVerifyCompletionCli({
      repoRoot: DEFAULT_ROOT,
      changeRoot,
      recoveryAttemptId,
      callerApplyComplete,
    });
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exit(result.ok ? 0 : 1);
  }
  const result = evaluateSplitApplyVerifyCompletionFromDisk({
    repoRoot: DEFAULT_ROOT,
    changeRoot,
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exit(0);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
