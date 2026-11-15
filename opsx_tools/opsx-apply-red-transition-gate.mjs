import { defaultRepoRootFromToolFile as opsxDefaultProjectRoot } from "./opsx-repo-root.mjs";
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Авторитетный operative gate для RED_PROVEN / ALREADY_SATISFIED (R1–R5).
 *
 * Структурная валидация остаётся в opsx-apply-red.mjs; этот модуль повторно
 * исполняет frozen test_commands, сверяет oracle-маркеры и блокирует
 * worker-authored execution claims.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import {
  ALREADY_SATISFIED,
  RED_PROVEN_CLASSIFICATION,
  RED_VALIDATION_PHASE_HISTORICAL,
  RED_VALIDATION_PHASE_OPERATIVE,
  authoritativeFrozenTestContentIdentityFromRed,
  resolveDeclaredFrozenTestContentIdentity,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  classifyCommandExecutionFromOutput,
  parseFrozenTestCommand,
} from "./opsx-apply-red-frozen-command.mjs";
import { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";
import {
  SCAFFOLD_NOT_IMPLEMENTED,
  classifyRedFailureAfterScaffold,
} from "./opsx-apply-pipeline-identity.mjs";
import { loadLinkedDesign } from "./opsx-bug-learning-apply.mjs";
import {
  evaluateAuthoritativeTestOracle,
  verifyBehaviorObligationIdentity,
} from "./opsx-apply-red-oracle-authority.mjs";
const SPEC_RE = /@opsx-red-spec-value:\s*([^\s*]+)/;
const BEHAVIOR_RE = /@opsx-red-behavior-value:\s*([^\s*]+)/;
const RED_VERIFIER_ID = "opsx-apply-red-transition-gate";
const DEFAULT_REPO_ROOT = opsxDefaultProjectRoot(import.meta.url);

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Deterministic JSON для decision_digest (sorted keys).
 */
export function stableRedDecisionStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => stableRedDecisionStringify(entry)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableRedDecisionStringify(value[key])}`)
    .join(",")}}`;
}

export { computeFrozenTestContentIdentity } from "./opsx-frozen-test-content-identity.mjs";

/**
 * Читает oracle-маркеры из исходника frozen-теста.
 */
export function readRedOracleMarkers(repoRoot, testPath) {
  const abs = resourcePath(repoRoot, testPath);
  if (!existsSync(abs)) {
    return { ok: false, code: "missing_test_path", path: testPath };
  }
  const source = readFileSync(abs, "utf8");
  const spec = source.match(SPEC_RE)?.[1] ?? null;
  const behavior = source.match(BEHAVIOR_RE)?.[1] ?? null;
  return { ok: true, source, spec, behavior };
}

function normalizeOracleToken(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  if (value === "null") return null;
  if (/^-?\d+$/.test(value)) return Number(value);
  if (
    (value.startsWith("'") && value.endsWith("'")) ||
    (value.startsWith('"') && value.endsWith('"'))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

/**
 * R4: sentinel засчитывается только при provenance scaffold/stub, не при throw в тесте.
 */
export function sentinelHasScaffoldProvenance({
  output = "",
  testSource = "",
  intendedSeam = "",
} = {}) {
  const text = String(output || "");
  if (!text.includes(SCAFFOLD_NOT_IMPLEMENTED)) {
    return { qualifies: false, reason: "no_sentinel" };
  }
  const src = String(testSource || "");
  const testThrowsSentinel =
    /throw\s+new\s+Error\s*\(\s*['"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['"]\s*\)/.test(src) ||
    /throw\s+['"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['"]/.test(src) ||
    /raise\s+RuntimeError\s*\(\s*['"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['"]\s*\)/.test(src);
  const importsScaffold =
    /apply-scaffold|fixtures\/scaffold|scaffoldSessionResume|from\s+['"].*scaffold/i.test(src);
  const importsProductScaffoldModule =
    /visual_covers|cover_media_cache|from\s+stroy\.channels\.telegram\.invest_yalta\.visual_covers|from\s+stroy\.consultation\.invest_yalta\.cover_media_cache/.test(
      src,
    ) ||
    (Boolean(intendedSeam) &&
      String(intendedSeam).includes("visual_covers") &&
      /cover_for_ux_state/.test(src)) ||
    (Boolean(intendedSeam) &&
      String(intendedSeam).includes("cover_media_cache") &&
      /get_cached_cover|upsert_cached_cover|delete_cached_cover/.test(src));
  const productRuntimeSentinel =
    /RuntimeError:\s*OPSX_SCAFFOLD_NOT_IMPLEMENTED/.test(text) &&
    /(?:visual_covers\.py|src[\\/]stroy[\\/][\w./\\-]+\.py)/.test(text);
  if (testThrowsSentinel && !importsScaffold && !importsProductScaffoldModule) {
    return { qualifies: false, reason: "test_thrown_sentinel" };
  }
  if (importsProductScaffoldModule && productRuntimeSentinel) {
    return { qualifies: true, reason: "python_product_scaffold_runtime" };
  }
  if (importsScaffold || /installSessionResumeHydrate|apply-scaffold/.test(text)) {
    return { qualifies: true, reason: "scaffold_provenance" };
  }
  return { qualifies: false, reason: "sentinel_without_scaffold_stack" };
}

/**
 * Классификация с R4 поверх stdout/stderr subprocess.
 */
export function classifyCommandExecutionFromOutputWithGate({
  output = "",
  exitCode,
  intendedSeam = "",
  testSource = "",
} = {}) {
  const base = classifyCommandExecutionFromOutput({
    output,
    exitCode,
    intendedSeam,
  });
  if (base.classification === RED_PROVEN_CLASSIFICATION && base.red_qualified) {
    const prov = sentinelHasScaffoldProvenance({ output, testSource, intendedSeam });
    if (
      String(output).includes(SCAFFOLD_NOT_IMPLEMENTED) &&
      !prov.qualifies
    ) {
      return classifyRedFailureAfterScaffold({ importError: true });
    }
  }
  return base;
}

function gateError(code, message, extra = {}) {
  return { code, message, ...extra };
}

/**
 * R5 receipt-bound: сверка задекларированных execution outcomes без live subprocess.
 */
function validateReceiptBoundCommandExecution(status, testPath, declared, errors) {
  if (status === "RED_PROVEN") {
    if (declared.red_qualified !== true) {
      errors.push(gateError("R5_TRANSITION", `receipt not red-qualified: ${testPath}`));
    }
    if (declared.final_classification !== RED_PROVEN_CLASSIFICATION) {
      errors.push(
        gateError(
          "R5_TRANSITION",
          `receipt-bound RED requires ${RED_PROVEN_CLASSIFICATION}`,
        ),
      );
    }
    if (declared.final_exit_code === 0 || declared.final_exit_code == null) {
      errors.push(
        gateError("R2_EXECUTION", "receipt-bound RED_PROVEN requires non-zero receipt exit"),
      );
    }
  }
  if (status === "ALREADY_SATISFIED") {
    if (declared.pass_qualified !== true) {
      errors.push(gateError("R5_TRANSITION", `receipt not pass-qualified: ${testPath}`));
    }
    if (declared.final_exit_code !== 0) {
      errors.push(
        gateError("R5_TRANSITION", "receipt-bound ALREADY_SATISFIED requires pass exit"),
      );
    }
  }
}

/**
 * Operative rewalk: stamp + неизменные байты frozen-тестов → без live pytest.
 *
 * Не ослабляет R1–R5: структурные проверки и receipt-bound outcomes остаются;
 * отключается только ``runFrozenRedCommand`` при совпадении decision_digest.
 */
export function storedOperativeRedTransitionReusable(artifact, repoRoot, identityCheck) {
  const status = artifact?.status;
  if (status !== "RED_PROVEN" && status !== "ALREADY_SATISFIED") {
    return false;
  }
  const stamp = artifact?.operative_red_transition;
  if (!isPlainObject(stamp)) {
    return false;
  }
  if (stamp.verifier_id !== RED_VERIFIER_ID) {
    return false;
  }
  if (stamp.permitted_transition !== status) {
    return false;
  }
  if (!identityCheck?.ok || !identityCheck?.declared) {
    return false;
  }
  const accepted = new Set(identityCheck.accepted_digests || []);
  if (!accepted.has(identityCheck.declared)) {
    return false;
  }
  if (
    stamp.test_content_identity &&
    typeof stamp.test_content_identity === "string" &&
    !accepted.has(stamp.test_content_identity)
  ) {
    return false;
  }
  const digest = computeRedOperativeStampDigest(artifact, { ok: true }, repoRoot);
  if (stamp.decision_digest !== digest) {
    return false;
  }
  return true;
}

/**
 * Receipt-bound behavior_obligation_identity when APPLY-DESIGN is not on disk
 * (historical / bounded stand). Does not recompute obligation from live design.
 */
function receiptBoundBehaviorObligationSatisfied(artifact) {
  const declared = artifact?.behavior_obligation_identity;
  if (!isPlainObject(declared)) return false;
  if (!declared.slice_id || !declared.planning_digest || !declared.obligation_digest) {
    return false;
  }
  if (artifact.slice_id && declared.slice_id !== artifact.slice_id) {
    return false;
  }
  return true;
}

/**
 * R1–R5 для operative RED_PROVEN / ALREADY_SATISFIED.
 */
export function evaluateRedTransitionGate({
  artifact,
  repoRoot,
  historicalRead = false,
  validationPhase = null,
  design = null,
  slice = null,
} = {}) {
  const phase =
    validationPhase ||
    (historicalRead ? RED_VALIDATION_PHASE_HISTORICAL : RED_VALIDATION_PHASE_OPERATIVE);
  const errors = [];
  const status = artifact?.status;
  if (status !== "RED_PROVEN" && status !== "ALREADY_SATISFIED") {
    return { ok: true, errors: [], skipped: ["operative_status"] };
  }

  const linkedDesign = design ?? loadLinkedDesign(repoRoot, artifact);
  const linkedSlice =
    slice ??
    (linkedDesign?.slices || []).find((s) => s.slice_id === artifact.slice_id) ??
    null;
  let obligationVerificationOk = true;
  if (phase === RED_VALIDATION_PHASE_HISTORICAL) {
    obligationVerificationOk = receiptBoundBehaviorObligationSatisfied(artifact);
    if (!obligationVerificationOk) {
      errors.push(
        gateError(
          "R1_ORACLE",
          "historical RED requires receipt-bound behavior_obligation_identity",
        ),
      );
    }
  } else if (linkedDesign && linkedSlice) {
    const obligationCheck = verifyBehaviorObligationIdentity({
      artifact,
      design: linkedDesign,
      slice: linkedSlice,
    });
    if (!obligationCheck.ok) {
      obligationVerificationOk = false;
      errors.push(...(obligationCheck.errors || []));
    }
  } else if (!receiptBoundBehaviorObligationSatisfied(artifact)) {
    obligationVerificationOk = false;
    errors.push(
      gateError(
        "R1_ORACLE",
        "behavior_obligation_identity required for authoritative oracle verification",
      ),
    );
  }

  const cmds = Array.isArray(artifact.test_commands) ? artifact.test_commands : [];
  const execs = Array.isArray(artifact.command_executions)
    ? artifact.command_executions
    : [];
  const identityCheck = resolveDeclaredFrozenTestContentIdentity(artifact, repoRoot);
  if (!identityCheck.accepted_digests.length) {
    errors.push(
      gateError("R2_EXECUTION", "missing frozen test paths on disk for identity check"),
    );
  } else if (identityCheck.declared && !identityCheck.ok) {
    errors.push(
      gateError(
        "R2_EXECUTION",
        "test_content_identity does not match on-disk frozen tests",
        {
          expected: identityCheck.declared,
          accepted: identityCheck.accepted_digests,
          canonical: identityCheck.canonical,
          legacy_all_paths: identityCheck.legacy_all_paths,
          legacy_test_paths_only: identityCheck.legacy_test_paths_only,
        },
      ),
    );
  }

  if (cmds.length !== execs.length) {
    errors.push(
      gateError("R2_EXECUTION", "command_executions length must match test_commands"),
    );
  }

  const stampReuseEligible =
    phase !== RED_VALIDATION_PHASE_HISTORICAL &&
    storedOperativeRedTransitionReusable(artifact, repoRoot, identityCheck);

  let skipLivePytest = false;

  for (let i = 0; i < cmds.length; i += 1) {
    const command = cmds[i];
    const declared = execs[i] || {};
    const parsed = parseFrozenTestCommand(command);
    const testPath = parsed.test_path || declared.test_path || "";
    if (!testPath || !existsSync(resourcePath(repoRoot, testPath))) {
      errors.push(
        gateError("R2_EXECUTION", `frozen test path missing on disk: ${testPath || "?"}`),
      );
      continue;
    }
    const oracle = readRedOracleMarkers(repoRoot, testPath);
    if (!oracle.ok) {
      errors.push(gateError("R1_ORACLE", `cannot read oracle markers: ${testPath}`));
      continue;
    }
    if (oracle.spec === null || oracle.behavior === null) {
      errors.push(
        gateError(
          "R1_ORACLE",
          `test must declare @opsx-red-spec-value and @opsx-red-behavior-value: ${testPath}`,
        ),
      );
      continue;
    }

    // Historical bound reads frozen receipts only; live oracle would re-run pytest.
    let authoritativeOracleOk = true;
    if (phase !== RED_VALIDATION_PHASE_HISTORICAL) {
      if (linkedDesign && linkedSlice) {
        const authOracle = evaluateAuthoritativeTestOracle({
          artifact,
          design: linkedDesign,
          slice: linkedSlice,
          testPath,
          oracle,
          status,
        });
        authoritativeOracleOk = authOracle.ok;
        if (!authOracle.ok) {
          errors.push(...(authOracle.errors || []));
        }
      } else if (!oracle.ok || oracle.spec === null || oracle.behavior === null) {
        authoritativeOracleOk = false;
        errors.push(
          gateError(
            "R1_ORACLE",
            `test must declare @opsx-red-spec-value and @opsx-red-behavior-value: ${testPath}`,
          ),
        );
      } else if (!receiptBoundBehaviorObligationSatisfied(artifact)) {
        authoritativeOracleOk = false;
      }
    }

    if (declared.command && declared.command !== command) {
      errors.push(gateError("R2_EXECUTION", "receipt command mismatch"));
    }

    const skipCommandLivePytest =
      stampReuseEligible &&
      obligationVerificationOk &&
      authoritativeOracleOk;

    if (phase === RED_VALIDATION_PHASE_HISTORICAL || skipCommandLivePytest) {
      if (skipCommandLivePytest) {
        skipLivePytest = true;
      }
      validateReceiptBoundCommandExecution(status, testPath, declared, errors);
      continue;
    }

    const live = runFrozenRedCommand(command, { cwd: repoRoot, timeoutMs: 120000 });
    const liveClass = classifyCommandExecutionFromOutputWithGate({
      output: live.relevant_output,
      exitCode: live.exit_code,
      intendedSeam: declared.intended_seam || "",
      testSource: oracle.source,
    });

    if (declared.final_exit_code !== live.exit_code) {
      errors.push(
        gateError(
          "R2_EXECUTION",
          `exit_code mismatch for ${testPath}`,
          { declared: declared.final_exit_code, live: live.exit_code },
        ),
      );
    }
    if (declared.final_classification !== liveClass.classification) {
      errors.push(
        gateError(
          "R5_TRANSITION",
          `classification mismatch for ${testPath}`,
          { declared: declared.final_classification, live: liveClass.classification },
        ),
      );
    }

    if (status === "RED_PROVEN") {
      if (!liveClass.red_qualified) {
        errors.push(
          gateError("R5_TRANSITION", `live run not red-qualified: ${testPath}`),
        );
      }
      if (live.exit_code === 0) {
        errors.push(gateError("R2_EXECUTION", "RED_PROVEN requires non-zero live exit"));
      }
      if (liveClass.classification !== RED_PROVEN_CLASSIFICATION) {
        errors.push(
          gateError("R5_TRANSITION", `operative RED requires ${RED_PROVEN_CLASSIFICATION}`),
        );
      }
    }

    if (status === "ALREADY_SATISFIED") {
      if (live.exit_code !== 0 || !liveClass.pass_qualified) {
        errors.push(
          gateError("R5_TRANSITION", `ALREADY_SATISFIED requires live pass: ${testPath}`),
        );
      }
    }

  }

  return {
    ok: errors.length === 0,
    errors,
    live_pytest_skipped: skipLivePytest,
    live_pytest_skip_reason: skipLivePytest
      ? "operative_red_transition_reuse_test_content_identity_unchanged"
      : null,
  };
}

/**
 * Идентичность в decision digest всегда в operative-профиле (canonical authority).
 * Historical gate только отключает live pytest; digest stamp не пересчитывается в legacy-профиле.
 */
function frozenIdentityForOperativeDecision(artifact, repoRoot, _validationPhase) {
  return authoritativeFrozenTestContentIdentityFromRed(artifact, repoRoot);
}

/**
 * Digest сохранённого stamp: operative-представление decision (как при выдаче RED_PROVEN).
 */
export function computeRedOperativeStampDigest(artifact, gate, repoRoot = DEFAULT_REPO_ROOT) {
  const decision = buildRedOperativeTransitionDecision({
    artifact,
    gate,
    repoRoot,
    validationPhase: RED_VALIDATION_PHASE_OPERATIVE,
  });
  return computeRedOperativeDecisionDigest(decision);
}

export function buildRedOperativeTransitionDecision({
  artifact,
  gate,
  repoRoot = DEFAULT_REPO_ROOT,
  validationPhase = RED_VALIDATION_PHASE_OPERATIVE,
} = {}) {
  const status = artifact?.status ?? null;
  const permittedTransition =
    status === "RED_PROVEN" || status === "ALREADY_SATISFIED" ? status : null;
  const identity = frozenIdentityForOperativeDecision(
    artifact,
    repoRoot,
    validationPhase,
  );
  const cmds = Array.isArray(artifact?.test_commands) ? artifact.test_commands : [];
  const execs = Array.isArray(artifact?.command_executions)
    ? artifact.command_executions
    : [];
  const commandOutcomes = cmds.map((command, index) => {
    const declared = execs[index] || {};
    return {
      command,
      final_exit_code: declared.final_exit_code ?? null,
      final_classification: declared.final_classification ?? null,
      red_qualified: declared.red_qualified === true,
      pass_qualified: declared.pass_qualified === true,
    };
  });
  const decision = {
    verifier_id: RED_VERIFIER_ID,
    permitted_transition: permittedTransition,
    design_spec_id:
      artifact?.slice_identity?.slice_digest || artifact?.slice_id || null,
    test_content_identity: identity.ok
      ? identity.digest
      : artifact?.test_content_identity || null,
    execution_id: artifact?.repository_identity_before_red?.head || null,
    command_outcomes: commandOutcomes,
    gate_ok: gate?.ok === true,
  };
  if (validationPhase !== RED_VALIDATION_PHASE_HISTORICAL) {
    decision.frozen_test_identity_algorithm = identity.algorithm;
  }
  return decision;
}

/** SHA256 от stableRedDecisionStringify(decision). */
export function computeRedOperativeDecisionDigest(decision) {
  return createHash("sha256")
    .update(stableRedDecisionStringify(decision), "utf8")
    .digest("hex");
}

function redReceiptShaped(artifact) {
  if (!isPlainObject(artifact)) return false;
  return (
    Array.isArray(artifact.test_commands) ||
    Array.isArray(artifact.command_executions) ||
    Number.isInteger(artifact.schema_version) ||
    typeof artifact.evidence_contract === "string"
  );
}

/**
 * R5-02: независимая operative authority для downstream consumers.
 * Структурный receipt без успешного gate → false; self-issued stamp с неверным digest → false.
 */
export function redOperativeTransitionAuthorized(
  artifact,
  repoRoot = DEFAULT_REPO_ROOT,
  options = {},
) {
  if (!isPlainObject(artifact)) return false;
  const status = artifact.status;
  if (status !== "RED_PROVEN" && status !== "ALREADY_SATISFIED") {
    return false;
  }
  if (!redReceiptShaped(artifact)) {
    return false;
  }
  const validationPhase =
    options.validationPhase ??
    (options.historicalRead
      ? RED_VALIDATION_PHASE_HISTORICAL
      : RED_VALIDATION_PHASE_OPERATIVE);
  const gate = evaluateRedTransitionGate({
    artifact,
    repoRoot,
    validationPhase,
    historicalRead: validationPhase === RED_VALIDATION_PHASE_HISTORICAL,
  });
  if (!gate.ok) {
    return false;
  }
  const digest = computeRedOperativeStampDigest(artifact, gate, repoRoot);
  const stamp = artifact.operative_red_transition;
  if (isPlainObject(stamp)) {
    if (stamp.verifier_id !== RED_VERIFIER_ID) return false;
    if (stamp.permitted_transition !== status) return false;
    if (stamp.decision_digest !== digest) return false;
    if (
      stamp.test_content_identity &&
      typeof stamp.test_content_identity === "string"
    ) {
      const resolved = resolveDeclaredFrozenTestContentIdentity(artifact, repoRoot);
      const accepted = new Set(resolved.accepted_digests || []);
      if (
        resolved.declared &&
        accepted.size > 0 &&
        !accepted.has(stamp.test_content_identity)
      ) {
        return false;
      }
    }
  }
  return true;
}

