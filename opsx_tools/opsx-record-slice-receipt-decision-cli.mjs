#!/usr/bin/env node
import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Materialize slice stage receipt JSON from a minimal hop decision (stdin JSON).
 * Digests, identities, and structural const fields are filled here — not by the model.
 *
 * Usage:
 *   echo '{"repo":"...","change_id":"c","decision":{...}}' | node opsx-record-slice-receipt-decision-cli.mjs
 */
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  ALREADY_SATISFIED,
  APPLY_RED_SCHEMA_VERSION,
  IMPLEMENTATION_WRITTEN,
  RED_EVIDENCE_CONTRACT,
  RED_PROVEN,
  RED_PROVEN_CLASSIFICATION,
  SCAFFOLD_READY,
  SCAFFOLD_NOT_IMPLEMENTED,
  applyReceiptRelPaths,
  candidateIdentityFromEntries,
  computeLiveFrozenTestContentIdentity,
  resolveDeclaredFrozenTestContentIdentity,
  fileSha256,
  hashRepoRelativePaths,
  isDurableAlreadySatisfiedObservation,
  posixPath,
} from "./opsx-apply-pipeline-identity.mjs";
import { readRedOracleMarkers } from "./opsx-apply-red-transition-gate.mjs";
import { findRedOracleBinding } from "./opsx-apply-red-oracle-authority.mjs";
import { implementationAdoptedTransitionAuthorized } from "./opsx-adoption-transition-gate.mjs";
import {
  adoptFromCheckedBundle,
  buildAdoptionAuthorityPath,
  buildAdoptionGapPath,
  buildCurrentByteAdoptionAuthority,
  buildImplementationAdoptedReceipt,
  classifyAdoptionEvidenceGap,
  IMPLEMENTATION_ADOPTED,
  loadApplyImplementSchema,
  validateApplyImplementArtifact,
} from "./opsx-apply-implement.mjs";
import { sha256CanonicalJson } from "./opsx-adoption-canonical-json.mjs";
import {
  ensureSliceAdoptionEvidence,
  resolvePipelineOwnerAttestationRef,
} from "./opsx-adoption-evidence-materialize.mjs";
import {
  classifyCommandExecutionFromOutput,
  parseFrozenTestCommand,
} from "./opsx-apply-red-frozen-command.mjs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";

const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

function loadImplementSchemaForRepo(repo) {
  return loadApplyImplementSchema(repo) || loadApplyImplementSchema(DEFAULT_ROOT);
}

function gitHead(repo) {
  const r = spawnSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" });
  return r.status === 0 ? String(r.stdout || "").trim() : "unknown";
}

function buildApplyDesignIdentity(repo, changeId) {
  const designRel = `openspec/changes/${changeId}/apply-design/APPLY-DESIGN.json`;
  const designPath = resourcePath(repo, designRel);
  if (!existsSync(designPath)) {
    return { ok: false, code: "APPLY_DESIGN_MISSING" };
  }
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  return {
    ok: true,
    identity: {
      machine_path: designRel,
      status: "APPLY_DESIGN_READY",
      planning_digest: design.planning_identity?.planning_digest,
      inspected_digest: design.repository_identity?.inspected_digest,
      protected_context_digest: design.protected_context_identity?.content_digest,
      artifact_sha256: fileSha256(designPath),
    },
    design,
  };
}

function sliceFromDesign(design, sliceId) {
  const slices = Array.isArray(design.slices) ? design.slices : [];
  return slices.find((s) => String(s.slice_id) === String(sliceId)) || null;
}

function workPathsFromSlice(slice) {
  const paths = new Set();
  const add = (v) => {
    if (typeof v === "string" && v.trim()) paths.add(posixPath(v.trim()));
    if (isObject(v) && typeof v.path === "string") paths.add(posixPath(v.path));
  };
  for (const item of slice.expected_product_write_surfaces || []) add(item);
  const contract = slice.scaffold_contract;
  if (isObject(contract)) {
    for (const item of contract.required_surfaces || []) add(item);
    for (const item of contract.allowed_scaffold_writes || []) add(item);
  }
  return [...paths];
}

function requiredSurfacesFromSlice(slice) {
  const contract = slice.scaffold_contract;
  if (!isObject(contract) || !Array.isArray(contract.required_surfaces)) return [];
  return contract.required_surfaces
    .map((s) => {
      if (!isObject(s)) return null;
      return {
        path: posixPath(s.path),
        action: String(s.action || "EXTEND"),
        required_symbols: Array.isArray(s.required_symbols)
          ? s.required_symbols.map(String)
          : [],
      };
    })
    .filter(Boolean);
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function repoIdentity(repo, paths) {
  const head = gitHead(repo);
  const inspected = [...new Set(paths.map((p) => posixPath(p)).filter(Boolean))].sort();
  const map = hashRepoRelativePaths(repo, inspected);
  const entries = Object.keys(map)
    .sort()
    .map((path) => ({ path, sha256: map[path] }));
  return candidateIdentityFromEntries(entries, { head });
}

function gitDiffNameOnly(repo) {
  const r = spawnSync("git", ["diff", "--name-only", "HEAD"], {
    cwd: repo,
    encoding: "utf8",
  });
  if (r.status !== 0) {
    return [];
  }
  return String(r.stdout || "")
    .split(/\r?\n/)
    .map((line) => posixPath(line.trim()))
    .filter(Boolean);
}

function gitDiffNameOnlySince(repo, baselineRef) {
  const base = String(baselineRef || "").trim();
  if (base && base !== "unknown") {
    const r = spawnSync("git", ["diff", "--name-only", base], {
      cwd: repo,
      encoding: "utf8",
    });
    if (r.status === 0) {
      return String(r.stdout || "")
        .split(/\r?\n/)
        .map((line) => posixPath(line.trim()))
        .filter(Boolean);
    }
  }
  return gitDiffNameOnly(repo);
}

const HOP_SNAPSHOT_SCHEMA = "hop_worktree_snapshot_v1";

function hopDeltaPaths(repo, baseline, hopFiles) {
  const base = String(baseline || "").trim();
  const claimed = Array.isArray(hopFiles)
    ? hopFiles.map((p) => posixPath(String(p))).filter(Boolean)
    : [];
  if (base.startsWith("{")) {
    try {
      const snapshot = JSON.parse(base);
      if (snapshot?.schema === HOP_SNAPSHOT_SCHEMA && snapshot.files) {
        const changed = [];
        const seen = new Set();
        for (const [path, priorSha] of Object.entries(snapshot.files)) {
          const rel = posixPath(String(path));
          const abs = resourcePath(repo, rel);
          if (!existsSync(abs)) {
            changed.push(rel);
            seen.add(rel);
            continue;
          }
          const live = fileSha256(abs);
          if (live !== priorSha) {
            changed.push(rel);
          }
          seen.add(rel);
        }
        for (const rel of claimed) {
          const abs = resourcePath(repo, rel);
          if (!existsSync(abs)) {
            continue;
          }
          try {
            if (!statSync(abs).isFile()) {
              continue;
            }
          } catch {
            continue;
          }
          const live = fileSha256(abs);
          if (!seen.has(rel) || snapshot.files[rel] !== live) {
            changed.push(rel);
          }
        }
        return [...new Set(changed)];
      }
    } catch {
      // fall through
    }
  }
  const since = gitDiffNameOnlySince(repo, base);
  return [...new Set([...since, ...claimed])];
}

function readTextIfExists(path) {
  if (!existsSync(path)) return null;
  return readFileSync(path, "utf8");
}

function commitReceiptIfValid(repo, outPath, relPath, body, validateFn) {
  const candidate = `${JSON.stringify(body, null, 2)}\n`;
  const tag = createHash("sha256").update(candidate).digest("hex").slice(0, 16);
  const tmpPath = `${outPath}.candidate.${tag}.tmp`;
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(tmpPath, candidate, "utf8");
  const verdict = validateFn(tmpPath);
  if (!verdict.ok) {
    try {
      unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    return verdict;
  }
  try {
    if (existsSync(outPath)) {
      unlinkSync(outPath);
    }
    renameSync(tmpPath, outPath);
  } catch {
    try {
      unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    return { ok: false, code: "RECEIPT_PUBLISH_FAILED" };
  }
  return { ok: true, path: relPath, status: body.status };
}

function pathTrackedAtHead(repo, relPath) {
  const r = spawnSync("git", ["cat-file", "-e", `HEAD:${posixPath(relPath)}`], {
    cwd: repo,
    encoding: "utf8",
  });
  return r.status === 0;
}

function fileContainsSymbols(repo, relPath, symbols) {
  const abs = resourcePath(repo, posixPath(relPath));
  if (!existsSync(abs)) {
    return false;
  }
  const text = readFileSync(abs, "utf8");
  return (symbols || []).every((sym) => text.includes(String(sym)));
}

function isPlannedPytestNode(name) {
  const node = String(name || "").trim();
  return /^test_[A-Za-z0-9_]+$/.test(node);
}

/**
 * Prior on-disk RED executions are reusable when commands match and each row
 * has a bounded pytest attempt (avoid re-running the full slice suite on hop
 * materialize when shape already ran in a previous attempt).
 */
function priorRedExecutionsReusable(prior, testCommands) {
  if (!isObject(prior) || !Array.isArray(prior.command_executions)) {
    return false;
  }
  const want = new Set(testCommands.map((c) => String(c)));
  if (!want.size || prior.command_executions.length !== want.size) {
    return false;
  }
  for (const exec of prior.command_executions) {
    const cmd = String(exec?.command || "");
    if (!want.has(cmd)) {
      return false;
    }
    if (typeof exec?.final_exit_code !== "number") {
      return false;
    }
    if (!Array.isArray(exec.attempts) || !exec.attempts.length) {
      return false;
    }
  }
  return true;
}

function buildRedTestCommands(slice) {
  const testPaths = new Set();
  const commands = [];
  const bindings = Array.isArray(slice?.red_oracle_bindings) ? slice.red_oracle_bindings : [];
  for (const binding of bindings) {
    const testPath = posixPath(binding.test_path || "");
    if (!testPath) {
      continue;
    }
    testPaths.add(testPath);
    for (const obs of binding.observed_intended_failures || []) {
      const node = String(obs.command || "").trim();
      if (!isPlannedPytestNode(node)) {
        continue;
      }
      commands.push(`poetry run pytest -q ${testPath}::${node}`);
    }
    for (const hold of binding.already_holding_not_failures || []) {
      const node = String(
        typeof hold === "object" && hold ? hold.command : hold,
      ).trim();
      if (!isPlannedPytestNode(node)) {
        continue;
      }
      commands.push(`poetry run pytest -q ${testPath}::${node}`);
    }
  }
  for (const p of slice.red_test_targets_or_seams || []) {
    if (typeof p === "string" && p.trim()) {
      testPaths.add(posixPath(p.trim()));
    }
  }
  return { test_paths: [...testPaths], test_commands: [...new Set(commands)] };
}

/**
 * R1 preflight (unchanged validator): ALREADY_SATISFIED only when frozen behavior
 * marker equals authoritative_expected from APPLY-DESIGN.
 */
function redOracleAuthorizesAlreadySatisfied(repo, slice, red) {
  const execs = Array.isArray(red.command_executions) ? red.command_executions : [];
  const seen = new Set();
  for (const exec of execs) {
    if (!exec || exec.pass_qualified !== true || exec.final_exit_code !== 0) {
      return false;
    }
    const parsed = parseFrozenTestCommand(String(exec.command || ""));
    const testPath = parsed.test_path || "";
    if (!testPath || seen.has(testPath)) {
      continue;
    }
    seen.add(testPath);
    const binding = findRedOracleBinding(slice, testPath);
    if (!binding?.authoritative_expected) {
      return false;
    }
    const oracle = readRedOracleMarkers(repo, testPath);
    if (!oracle.ok || oracle.spec === null || oracle.behavior === null) {
      return false;
    }
    const authoritative = String(binding.authoritative_expected);
    const behaviorVal = String(oracle.behavior);
    if (String(behaviorVal) !== authoritative) {
      return false;
    }
  }
  return seen.size > 0;
}

/**
 * После live shape: выставить RED_PROVEN / ALREADY_SATISFIED по command_executions,
 * не по заглушке stub-статуса.
 */
function finalizeRedReceiptFromExecutions(outPath, repo, slice) {
  const red = JSON.parse(readFileSync(outPath, "utf8"));
  const execs = Array.isArray(red.command_executions) ? red.command_executions : [];
  const cmds = Array.isArray(red.test_commands) ? red.test_commands : [];
  if (execs.length !== cmds.length || !execs.length) {
    return { ok: false, code: "RED_EXECUTIONS_INCOMPLETE" };
  }
  for (const exec of execs) {
    if (!exec || exec.final_exit_code === 0 || exec.red_qualified === true) {
      continue;
    }
    const cls = classifyCommandExecutionFromOutput({
      exitCode: exec.final_exit_code,
      output: String(exec.attempts?.[0]?.relevant_output || ""),
    });
    if (cls && cls.red_qualified !== true) {
      return { ok: false, code: cls.code || "RED_INFRA_NOT_PROVEN" };
    }
  }
  const redQualified = execs.filter((e) => e && e.red_qualified === true);
  const allPassObserved = execs.every(
    (e) =>
      e &&
      e.final_exit_code === 0 &&
      e.pass_qualified === true &&
      e.final_classification === ALREADY_SATISFIED,
  );
  if (allPassObserved) {
    if (!slice || !redOracleAuthorizesAlreadySatisfied(repo, slice, red)) {
      return {
        ok: false,
        code: "RED_ORACLE_BEHAVIOR_MARKER_GAP",
        gap_detail:
          "live pytest pass with behavior marker not equal to authoritative obligation",
      };
    }
    red.status = ALREADY_SATISFIED;
    red.observation_only = true;
    red.implementation_write_authorized = false;
    red.adoption_audit_eligible = true;
    red.next_stage = "APPLY_IMPLEMENT";
    red.tests_frozen = true;
    red.observed_behavior =
      red.observed_behavior ||
      `Frozen RED suite pass-qualified (${execs.length} command(s)); obligation already satisfied on disk.`;
    red.red_reason = ALREADY_SATISFIED;
    delete red.final_execution;
    delete red.failure_classification;
  } else if (redQualified.length > 0 && redQualified.length === execs.length) {
    const lead = redQualified[0];
    const output = String(lead.attempts?.[0]?.relevant_output || "");
    const signature = output.includes(SCAFFOLD_NOT_IMPLEMENTED)
      ? SCAFFOLD_NOT_IMPLEMENTED
      : RED_PROVEN_CLASSIFICATION;
    red.status = RED_PROVEN;
    red.observation_only = false;
    red.implementation_write_authorized = true;
    red.adoption_audit_eligible = false;
    red.next_stage = "APPLY_IMPLEMENT";
    red.tests_frozen = true;
    red.failure_classification = RED_PROVEN_CLASSIFICATION;
    red.final_execution = {
      command: lead.command,
      exit_code: lead.final_exit_code,
      failure_classification: RED_PROVEN_CLASSIFICATION,
      failure_signature: signature,
    };
  } else if (redQualified.length > 0) {
    return { ok: false, code: "RED_MIXED_SUITE" };
  } else {
    return { ok: false, code: "RED_EVIDENCE_AMBIGUOUS" };
  }
  writeFileSync(outPath, `${JSON.stringify(red, null, 2)}\n`, "utf8");
  return { ok: true, status: red.status };
}

function writeScaffoldDeterministic(repo, changeId, sliceId, decision = {}) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) {
    return built;
  }
  const slice = sliceFromDesign(built.design, sliceId);
  if (!slice) {
    return { ok: false, code: "SLICE_NOT_IN_DESIGN" };
  }
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, sliceId);
  const outPath = resourcePath(repo, rel.scaffold);
  const workPaths = workPathsFromSlice(slice);
  const required = requiredSurfacesFromSlice(slice);
  const gateBlockers = [];
  for (const surf of required) {
    const abs = resourcePath(repo, surf.path);
    if (!existsSync(abs)) {
      gateBlockers.push({ code: "SURFACE_MISSING", detail: surf.path });
      continue;
    }
    const syms = surf.required_symbols || [];
    if (syms.length && !fileContainsSymbols(repo, surf.path, syms)) {
      gateBlockers.push({ code: "SYMBOL_MISSING", detail: surf.path });
    }
  }
  if (gateBlockers.length) {
    const now = new Date().toISOString();
    const body = {
      schema: "opsx.apply_scaffold",
      schema_version: 1,
      change_id: changeId,
      slice_id: sliceId,
      status: "BLOCKED",
      generated_at: now,
      chat_history_required: false,
      behavior_implemented: false,
      tests_modified: false,
      tests_executed: false,
      blockers: gateBlockers,
      next_stage: "NONE",
    };
    mkdirSync(dirname(outPath), { recursive: true });
    writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
    return { ok: true, path: rel.scaffold, status: "BLOCKED" };
  }

  const diffPaths = hopDeltaPaths(
    repo,
    decision.hop_worktree_baseline,
    decision.hop_files_changed,
  ).filter(
    (p) => workPaths.includes(p) || workPaths.some((w) => p.startsWith(`${w}/`)),
  );
  const created = [];
  const extended = [];
  for (const p of diffPaths) {
    if (pathTrackedAtHead(repo, p)) {
      extended.push(p);
    } else {
      created.push(p);
    }
  }
  const productModified = diffPaths.some((p) => p.startsWith("src/"));
  const noOpAllowed = Boolean(slice.scaffold_contract?.no_op_allowed);
  const noOp = !productModified && created.length === 0 && extended.length === 0;
  // `no_op_allowed: false` blocks only no-op when required surfaces are not yet
  // operative. Satisfied shape on disk → runtime-owned deterministic no-op receipt.
  if (noOp && !noOpAllowed) {
    const shapeOperative = required.every((surf) => {
      const abs = resourcePath(repo, surf.path);
      if (!existsSync(abs)) {
        return false;
      }
      const syms = surf.required_symbols || [];
      return !syms.length || fileContainsSymbols(repo, surf.path, syms);
    });
    if (!shapeOperative) {
      return {
        ok: false,
        code: "SCAFFOLD_NO_OP_FORBIDDEN",
      };
    }
  }

  const scaffoldDecision = {
    slice_id: sliceId,
    receipt_status: SCAFFOLD_READY,
    no_op: noOp,
    product_files_modified: productModified,
    created_paths: created,
    extended_paths: extended,
    created_symbols: [],
    structural_changes: noOp
      ? []
      : diffPaths.map((p) => ({
          path: p,
          kind: created.includes(p) ? "create" : "extend",
          detail: "deterministic worktree diff",
        })),
  };
  return writeScaffold(repo, changeId, scaffoldDecision);
}

function writeRedSuccess(repo, changeId, sliceId, decision = {}) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) {
    return built;
  }
  const slice = sliceFromDesign(built.design, sliceId);
  if (!slice) {
    return { ok: false, code: "SLICE_NOT_IN_DESIGN" };
  }
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, sliceId);
  const outPath = resourcePath(repo, rel.red);
  const { test_paths, test_commands } = buildRedTestCommands(slice, repo);
  if (!test_commands.length) {
    return { ok: false, code: "RED_COMMANDS_EMPTY" };
  }
  const status = RED_PROVEN;
  const scaffoldRel = rel.scaffold;
  const scaffoldPath = resourcePath(repo, scaffoldRel);
  let scaffoldIdentity = null;
  if (existsSync(scaffoldPath)) {
    const scaffoldBody = JSON.parse(readFileSync(scaffoldPath, "utf8"));
    scaffoldIdentity = {
      machine_path: scaffoldRel,
      slice_id: sliceId,
      status: scaffoldBody.status,
      behavior_implemented: Boolean(scaffoldBody.behavior_implemented),
      no_op: Boolean(scaffoldBody.no_op),
      artifact_sha256: fileSha256(scaffoldPath),
    };
  }
  const now = new Date().toISOString();
  const body = {
    schema: "opsx.apply_red",
    schema_version: APPLY_RED_SCHEMA_VERSION,
    evidence_contract: RED_EVIDENCE_CONTRACT,
    change_id: changeId,
    slice_id: sliceId,
    slice_identity: { slice_id: sliceId },
    status,
    observation_only: status === ALREADY_SATISFIED,
    implementation_write_authorized: status === RED_PROVEN,
    adoption_audit_eligible: status === ALREADY_SATISFIED,
    generated_at: now,
    chat_history_required: false,
    product_files_modified: false,
    apply_design_identity: built.identity,
    scaffold_identity: scaffoldIdentity,
    attempt_limit: 3,
    test_paths,
    fixture_paths: [],
    test_commands,
    command_executions: [],
    tests_frozen: false,
    blockers: [],
    next_stage: status === ALREADY_SATISFIED ? "APPLY_GREEN" : "APPLY_IMPLEMENT",
  };
  mkdirSync(dirname(outPath), { recursive: true });
  const priorRed = readTextIfExists(outPath);
  let priorParsed = null;
  if (priorRed !== null) {
    try {
      priorParsed = JSON.parse(priorRed);
    } catch {
      priorParsed = null;
    }
  }
  if (priorParsed && priorRedExecutionsReusable(priorParsed, test_commands)) {
    body.command_executions = priorParsed.command_executions;
    body.tests_frozen = Boolean(priorParsed.tests_frozen);
    if (priorParsed.test_content_identity) {
      body.test_content_identity = priorParsed.test_content_identity;
    }
    if (priorParsed.behavior_obligation_identity) {
      body.behavior_obligation_identity = priorParsed.behavior_obligation_identity;
    }
    writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  } else {
    writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  }

  const shapeScript = resolve(
    dirname(fileURLToPath(import.meta.url)),
    "opsx-shape-slice-red-receipt-cli.mjs",
  );
  const skipShape =
    priorParsed && priorRedExecutionsReusable(priorParsed, test_commands);
  const shape = skipShape
    ? { status: 0, stdout: `${JSON.stringify({ ok: true, reused: true })}\n` }
    : spawnSync(
        "node",
        [shapeScript, "--repo", repo, "--change", changeId, "--slice", sliceId],
        { encoding: "utf8", cwd: repo, timeout: 150000 },
      );
  if (shape.status !== 0) {
    if (priorRed !== null) {
      writeFileSync(outPath, priorRed, "utf8");
    } else {
      try {
        unlinkSync(outPath);
      } catch {
        // ignore
      }
    }
    return { ok: false, code: "RED_SHAPE_FAILED" };
  }
  try {
    const line = String(shape.stdout || "")
      .trim()
      .split(/\r?\n/)
      .pop();
    const parsed = JSON.parse(line || "{}");
    if (parsed.ok !== true) {
      if (priorRed !== null) {
        writeFileSync(outPath, priorRed, "utf8");
      } else {
        try {
          unlinkSync(outPath);
        } catch {
          // ignore
        }
      }
      return { ok: false, code: parsed.code || "RED_SHAPE_REJECTED" };
    }
  } catch {
    if (priorRed !== null) {
      writeFileSync(outPath, priorRed, "utf8");
    }
    return { ok: false, code: "RED_SHAPE_BAD_JSON" };
  }
  const finalized = finalizeRedReceiptFromExecutions(outPath, repo, slice);
  if (!finalized.ok) {
    if (priorRed !== null) {
      writeFileSync(outPath, priorRed, "utf8");
    } else {
      try {
        unlinkSync(outPath);
      } catch {
        // ignore
      }
    }
    return finalized;
  }
  const redValidator = resolve(
    dirname(fileURLToPath(import.meta.url)),
    "opsx-apply-red.mjs",
  );
  const validateRun = spawnSync("node", [redValidator, outPath], {
    cwd: repo,
    encoding: "utf8",
  });
  let validateJson = { status: "FAIL" };
  try {
    validateJson = JSON.parse(String(validateRun.stdout || "{}"));
  } catch {
    validateJson = { status: "FAIL" };
  }
  if (validateRun.status !== 0 || validateJson.status !== "PASS") {
    if (priorRed !== null) {
      writeFileSync(outPath, priorRed, "utf8");
    } else {
      try {
        unlinkSync(outPath);
      } catch {
        // ignore
      }
    }
    return {
      ok: false,
      code: "RED_VALIDATE_FAILED",
      detail: (validateJson.errors || []).slice(0, 3),
    };
  }
  const transition = validateJson.transition_decision;
  if (transition && typeof transition === "object") {
    const stamped = JSON.parse(readFileSync(outPath, "utf8"));
    stamped.operative_red_transition = {
      verifier_id: transition.verifier_id,
      permitted_transition: transition.permitted_transition,
      decision_digest: transition.decision_digest,
      test_content_identity: transition.test_content_identity,
      behavior_obligation_identity: transition.behavior_obligation_identity,
    };
    writeFileSync(outPath, `${JSON.stringify(stamped, null, 2)}\n`, "utf8");
  }
  const finalStatus = String(validateJson.artifact_status || finalized.status || status);
  return { ok: true, path: rel.red, status: finalStatus };
}

function loadOptionalDesignReview(repo, changeId) {
  const reviewPath = resolve(
    repo,
    `openspec/changes/${changeId}/apply-design-review/APPLY-DESIGN-REVIEW.json`,
  );
  if (!existsSync(reviewPath)) {
    return null;
  }
  return JSON.parse(readFileSync(reviewPath, "utf8"));
}

function productionManifestForSurfaces(repo, surfaces) {
  const productionManifest = [];
  for (const raw of surfaces) {
    const path = posixPath(String(raw || ""));
    if (!path) continue;
    const abs = resourcePath(repo, path);
    if (!existsSync(abs)) {
      return { ok: false, code: "ADOPTION_PRODUCT_MISSING", path };
    }
    productionManifest.push({ path, sha256: fileSha256(abs) });
  }
  productionManifest.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  return { ok: true, productionManifest };
}

function writeImplementBlockedValidated(repo, outPath, relImplement, built, opts) {
  const { changeId, sliceId, blockers } = opts;
  const now = new Date().toISOString();
  const body = {
    schema: "opsx.apply_implement",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    status: "BLOCKED",
    generated_at: now,
    chat_history_required: false,
    apply_design_identity: built.identity,
    blockers,
    next_stage: "NONE",
    product_files_modified: false,
    tests_modified: false,
    tests_executed: false,
  };
  const schema = loadImplementSchemaForRepo(repo);
  mkdirSync(dirname(outPath), { recursive: true });
  return commitReceiptIfValid(repo, outPath, relImplement, body, (candidatePath) => {
    const verdict = validateApplyImplementArtifact({
      artifact: body,
      schema,
      design: built.design,
      repoRoot: repo,
      artifactAbsPath: candidatePath,
    });
    if (!verdict.ok) {
      return { ok: false, code: "IMPLEMENT_VALIDATE_FAILED", errors: verdict.errors };
    }
    return { ok: true };
  });
}

/**
 * Frozen test bytes для IMPLEMENTATION_ADOPTED: при tests_frozen якоримся на
 * объявленный RED test_content_identity (legacy/canonical), а не на live canonical.
 */
function frozenTestContentIdentityForAdoption(red, repo) {
  const declared =
    red?.test_content_identity != null && String(red.test_content_identity).trim()
      ? String(red.test_content_identity).trim()
      : null;
  const operativeRaw = red?.operative_red_transition?.test_content_identity;
  const operative =
    operativeRaw != null && String(operativeRaw).trim() ? String(operativeRaw).trim() : "";
  const resolved = resolveDeclaredFrozenTestContentIdentity(red, repo);
  const accepted = new Set(resolved.accepted_digests || []);
  if (operative && accepted.has(operative)) return operative;
  if (declared && accepted.has(declared)) return declared;
  if (resolved.ok && resolved.declared && accepted.has(String(resolved.declared))) {
    return String(resolved.declared);
  }
  const live = computeLiveFrozenTestContentIdentity(repo, {
    test_paths: red?.test_paths || [],
    fixture_paths: red?.fixture_paths || [],
  });
  if (!declared && !operative) return live.identity || null;
  return live.identity || declared || operative || null;
}

function tryMaterializeImplementationAdopted(repo, changeId, sliceId, ctx) {
  const materialized = ensureSliceAdoptionEvidence({
    repo,
    changeId,
    sliceId,
    changeRoot: ctx.changeRoot,
    built: ctx.built,
    slice: ctx.slice,
    red: ctx.red,
    scaffold: ctx.scaffold,
    rel: ctx.rel,
    designReview: ctx.designReview,
    productionManifest: ctx.productionManifest,
  });
  if (materialized.ok === false) {
    return null;
  }
  const ownerRef = resolvePipelineOwnerAttestationRef(repo, changeId, sliceId);
  if (!ownerRef) {
    return null;
  }
  const implementDir = resourcePath(repo, `${ctx.changeRoot}/apply-implement/${sliceId}`);
  if (!existsSync(implementDir)) {
    return null;
  }
  const names = readdirSync(implementDir);
  const manifestName = names.find((n) => n.startsWith("ADOPTION-CANDIDATE-MANIFEST-"));
  const auditName = names.find((n) => n.startsWith("ADOPTION-AUDIT-"));
  if (!manifestName || !auditName) {
    return null;
  }
  const manifestPath = `${ctx.changeRoot}/apply-implement/${sliceId}/${manifestName}`;
  const auditPath = `${ctx.changeRoot}/apply-implement/${sliceId}/${auditName}`;
  const manifest = JSON.parse(readFileSync(resourcePath(repo, manifestPath), "utf8"));
  const audit = JSON.parse(readFileSync(resourcePath(repo, auditPath), "utf8"));
  const digest = manifestName.replace("ADOPTION-CANDIDATE-MANIFEST-", "").replace(".json", "");
  const gapPath = buildAdoptionGapPath(ctx.changeRoot, sliceId, digest);
  const authorityPath = buildAdoptionAuthorityPath(ctx.changeRoot, sliceId, digest);
  const auditFileSha = sha256CanonicalJson(audit, createHash);
  const adopted = adoptFromCheckedBundle({
    audit,
    manifest,
    audit_file_sha256: auditFileSha,
    red_status: ALREADY_SATISFIED,
    owner_attestation_ref: ownerRef,
    change_id: changeId,
    slice_id: sliceId,
    gap_path: gapPath,
    authority_path: authorityPath,
    repository_head: gitHead(repo),
  });
  if (!adopted.ok || !adopted.implementation || !adopted.authority) {
    return null;
  }
  const gapBody = {
    schema: "opsx.adoption_evidence_gap",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    status: "ADOPTION_EVIDENCE_GAP_RESOLVED",
    code: null,
    detail: "Owner attestation supplied; authority v2 minted",
    candidate_digest: digest,
    gap_path: gapPath,
    authority_path: authorityPath,
    owner_attestation_ref: ownerRef,
    manifest,
    resolved_at: new Date().toISOString(),
  };
  const gapSha256 = createHash("sha256").update(JSON.stringify(gapBody), "utf8").digest("hex");
  const authority = buildCurrentByteAdoptionAuthority({
    changeId,
    sliceId,
    gapPath,
    gapSha256,
    candidateDigest: digest,
    manifest,
    repositoryHead: gitHead(repo),
    redIdentity: ctx.red,
    scaffoldIdentity: {
      machine_path: ctx.rel.scaffold,
      slice_id: sliceId,
      status: ctx.scaffold.status,
      artifact_sha256: fileSha256(resourcePath(repo, ctx.rel.scaffold)),
    },
    designIdentity: ctx.built.identity,
    auditReceiptSha256: adopted.check.audit_receipt_sha256,
    ownerAttestationRef: ownerRef,
  });
  const frozenTestContentIdentity = frozenTestContentIdentityForAdoption(ctx.red, repo);
  const implementation = {
    ...buildImplementationAdoptedReceipt({
      changeId,
      sliceId,
      designIdentity: ctx.built.identity,
      scaffoldIdentity: {
        machine_path: ctx.rel.scaffold,
        slice_id: sliceId,
        status: ctx.scaffold.status,
        artifact_sha256: fileSha256(resourcePath(repo, ctx.rel.scaffold)),
      },
      redIdentity: {
        machine_path: ctx.rel.red,
        slice_id: sliceId,
        status: ctx.red.status,
        test_content_identity: frozenTestContentIdentity,
        tests_frozen: Boolean(ctx.red.tests_frozen),
        artifact_sha256: fileSha256(resourcePath(repo, ctx.rel.red)),
        observation_only: true,
        implementation_write_authorized: false,
        adoption_audit_eligible: true,
      },
      frozenTestContentIdentity,
      authorityPath,
      authorityDigest: digest,
      auditReceiptSha256: adopted.check.audit_receipt_sha256,
      ownerAttestationRef: ownerRef,
    }),
    audit_receipt_path: auditPath,
  };
  mkdirSync(dirname(resourcePath(repo, gapPath)), { recursive: true });
  mkdirSync(dirname(resourcePath(repo, authorityPath)), { recursive: true });
  writeFileSync(resourcePath(repo, gapPath), `${JSON.stringify(gapBody, null, 2)}\n`, "utf8");
  writeFileSync(resourcePath(repo, authorityPath), `${JSON.stringify(authority, null, 2)}\n`, "utf8");
  if (!implementationAdoptedTransitionAuthorized(implementation, repo)) {
    return null;
  }
  const schema = loadImplementSchemaForRepo(repo);
  return commitReceiptIfValid(repo, ctx.outPath, ctx.rel.implement, implementation, (candidatePath) => {
    const verdict = validateApplyImplementArtifact({
      artifact: implementation,
      schema,
      design: ctx.built.design,
      repoRoot: repo,
      artifactAbsPath: candidatePath,
    });
    if (!verdict.ok || verdict.transition_authorized !== true) {
      return {
        ok: false,
        code: "IMPLEMENT_VALIDATE_FAILED",
        errors: verdict.errors,
      };
    }
    if (!implementationAdoptedTransitionAuthorized(implementation, repo)) {
      return { ok: false, code: "IMPLEMENTATION_AUTHORITY_MISSING" };
    }
    return { ok: true, status: IMPLEMENTATION_ADOPTED };
  });
}

function writeImplementAdoptionObservation(
  repo,
  changeId,
  sliceId,
  decision,
  ctx,
) {
  const { built, slice, rel, outPath, red, scaffold, prodOnly, expected } = ctx;
  const changeRoot = `openspec/changes/${changeId}`;
  if (prodOnly.length > 0) {
    return writeImplementBlockedValidated(repo, outPath, rel.implement, built, {
      changeId,
      sliceId,
      blockers: [
        {
          code: "ADOPTION_PRODUCT_WRITE_FORBIDDEN",
          detail:
            `ALREADY_SATISFIED adoption forbids product writes in hop: ${prodOnly.join(", ")}`.slice(
              0,
              2000,
            ),
        },
      ],
    });
  }
  const surfaces = [...expected];
  const production = productionManifestForSurfaces(repo, surfaces);
  if (!production.ok) {
    return writeImplementBlockedValidated(repo, outPath, rel.implement, built, {
      changeId,
      sliceId,
      blockers: [
        {
          code: production.code || "ADOPTION_PRODUCT_MISSING",
          detail: String(production.path || "expected product surface missing"),
        },
      ],
    });
  }
  const designReview = loadOptionalDesignReview(repo, changeId);
  const adopted = tryMaterializeImplementationAdopted(repo, changeId, sliceId, {
    changeRoot,
    built,
    slice,
    red,
    scaffold,
    rel,
    outPath,
    designReview,
    productionManifest: production.productionManifest,
  });
  if (adopted && adopted.ok) {
    return adopted;
  }
  const gap = classifyAdoptionEvidenceGap({
    red,
    design: built.design,
    scaffold,
    designReview,
    slice,
    sliceId,
    changeRoot,
    repoRoot: repo,
    productionManifest: production.productionManifest,
  });
  const durable = isDurableAlreadySatisfiedObservation(red, repo);
  const blockerCode =
    gap.code ||
    (durable ? "ADOPTION_EVIDENCE_GAP" : "IMPLEMENTATION_AUTHORITY_MISSING");
  const detail =
    gap.detail ||
    gap.owner_invocation ||
    "current-byte adoption authority required before IMPLEMENTATION_ADOPTED";
  return writeImplementBlockedValidated(repo, outPath, rel.implement, built, {
    changeId,
    sliceId,
    blockers: [{ code: blockerCode, detail: String(detail).slice(0, 2000) }],
  });
}

function writeImplementSuccess(repo, changeId, sliceId, decision = {}) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) {
    return built;
  }
  const slice = sliceFromDesign(built.design, sliceId);
  if (!slice) {
    return { ok: false, code: "SLICE_NOT_IN_DESIGN" };
  }
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, sliceId);
  const outPath = resourcePath(repo, rel.implement);
  const redPath = resourcePath(repo, rel.red);
  const scaffoldPath = resourcePath(repo, rel.scaffold);
  if (!existsSync(redPath) || !existsSync(scaffoldPath)) {
    return { ok: false, code: "UPSTREAM_RECEIPT_MISSING" };
  }
  const red = JSON.parse(readFileSync(redPath, "utf8"));
  const scaffold = JSON.parse(readFileSync(scaffoldPath, "utf8"));
  const redStatus = String(red.status || "");
  const expected = new Set(
    (slice.expected_product_write_surfaces || [])
      .map((p) => (typeof p === "string" ? posixPath(p) : ""))
      .filter(Boolean),
  );
  const hopFiles = Array.isArray(decision.hop_files_changed)
    ? decision.hop_files_changed.map((p) => posixPath(String(p)))
    : [];
  const delta = hopDeltaPaths(repo, decision.hop_worktree_baseline, hopFiles);
  const production = delta.filter(
    (p) =>
      expected.has(p) ||
      [...expected].some((s) => p.startsWith(`${s}/`) || s.startsWith(`${p}/`)),
  );
  const prodOnly = production.filter((p) => !looksLikeTestPath(p));
  if (redStatus === ALREADY_SATISFIED) {
    return writeImplementAdoptionObservation(repo, changeId, sliceId, decision, {
      built,
      slice,
      rel,
      outPath,
      red,
      scaffold,
      prodOnly,
      expected,
    });
  }
  if (!prodOnly.length) {
    return { ok: false, code: "IMPLEMENT_NO_PRODUCT_DELTA" };
  }
  const live = computeLiveFrozenTestContentIdentity(repo, {
    test_paths: red.test_paths || [],
    fixture_paths: red.fixture_paths || [],
  });
  const freezeDigest = frozenTestContentIdentityForAdoption(red, repo);
  if (!freezeDigest) {
    if (!live.ok || !live.identity) {
      return { ok: false, code: live.code || "FROZEN_IDENTITY_FAILED" };
    }
  }
  const frozenId = freezeDigest || live.identity;
  if (!frozenId) {
    return { ok: false, code: "FROZEN_IDENTITY_FAILED" };
  }
  const created = [];
  const modified = [];
  for (const p of prodOnly) {
    if (pathTrackedAtHead(repo, p)) {
      modified.push(p);
    } else {
      created.push(p);
    }
  }
  const now = new Date().toISOString();
  const body = {
    schema: "opsx.apply_implement",
    schema_version: 1,
    change_id: changeId,
    slice_id: sliceId,
    status: IMPLEMENTATION_WRITTEN,
    generated_at: now,
    chat_history_required: false,
    apply_design_identity: built.identity,
    scaffold_identity: {
      machine_path: rel.scaffold,
      slice_id: sliceId,
      status: scaffold.status,
      artifact_sha256: fileSha256(scaffoldPath),
    },
    red_identity: {
      machine_path: rel.red,
      slice_id: sliceId,
      status: red.status,
      test_content_identity: frozenId,
      tests_frozen: Boolean(red.tests_frozen),
      artifact_sha256: fileSha256(redPath),
    },
    frozen_test_content_identity: frozenId,
    frozen_test_identity_before: frozenId,
    frozen_test_identity_after: frozenId,
    expected_product_write_surfaces: [...expected],
    production_paths_modified: modified,
    production_paths_created: created,
    product_files_modified: true,
    tests_modified: false,
    tests_executed: false,
    blockers: [],
    next_stage: "APPLY_GREEN",
  };
  const schema = loadImplementSchemaForRepo(repo);
  return commitReceiptIfValid(repo, outPath, rel.implement, body, (candidatePath) => {
    const verdict = validateApplyImplementArtifact({
      artifact: body,
      schema,
      design: built.design,
      repoRoot: repo,
      artifactAbsPath: candidatePath,
    });
    if (!verdict.ok) {
      return { ok: false, code: "IMPLEMENT_VALIDATE_FAILED", errors: verdict.errors };
    }
    return { ok: true };
  });
}

function looksLikeTestPath(p) {
  return (
    /(^|\/)tests?\//.test(p) ||
    /\.test\.(mjs|js|cjs|py)$/.test(p) ||
    /(^|\/)test_[^/]+\.py$/.test(p)
  );
}

function writeScaffold(repo, changeId, decision) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) return built;
  const slice = sliceFromDesign(built.design, decision.slice_id);
  if (!slice) return { ok: false, code: "SLICE_NOT_IN_DESIGN" };
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, decision.slice_id);
  const outPath = resourcePath(repo, rel.scaffold);
  const status = String(decision.receipt_status || "");
  const now = new Date().toISOString();
  const workPaths = workPathsFromSlice(slice);

  if (status === "BLOCKED") {
    const blockers = Array.isArray(decision.blockers) ? decision.blockers : [];
    if (!blockers.length) {
      const note = String(decision.gap_note || decision.completion_note || "").trim();
      blockers.push({
        code: "HOP_BLOCKED",
        detail: note || "blocked by hop decision",
      });
    }
    const body = {
      schema: "opsx.apply_scaffold",
      schema_version: 1,
      change_id: changeId,
      slice_id: decision.slice_id,
      status: "BLOCKED",
      generated_at: now,
      chat_history_required: false,
      behavior_implemented: false,
      tests_modified: false,
      tests_executed: false,
      blockers,
      next_stage: "NONE",
    };
    mkdirSync(dirname(outPath), { recursive: true });
    writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
    return { ok: true, path: rel.scaffold, status: "BLOCKED" };
  }

  if (status !== SCAFFOLD_READY) {
    return { ok: false, code: "SCAFFOLD_STATUS_INVALID" };
  }

  const noOp = Boolean(decision.no_op);
  const created = (decision.created_paths || []).map((p) => posixPath(p)).filter(Boolean);
  const extended = (decision.extended_paths || []).map((p) => posixPath(p)).filter(Boolean);
  const createdSymbols = (decision.created_symbols || []).map(String).filter(Boolean);
  const structural = Array.isArray(decision.structural_changes)
    ? decision.structural_changes.filter(isObject)
    : [];
  const productModified = Boolean(decision.product_files_modified);
  const inspectedPaths = [...new Set([...workPaths, ...created, ...extended])];
  const before = repoIdentity(repo, workPaths);
  const after = repoIdentity(repo, inspectedPaths);

  const body = {
    schema: "opsx.apply_scaffold",
    schema_version: 1,
    change_id: changeId,
    slice_id: decision.slice_id,
    apply_design_identity: built.identity,
    status: SCAFFOLD_READY,
    generated_at: now,
    chat_history_required: false,
    repository_identity_before: before,
    repository_identity_after: after,
    required_surfaces: requiredSurfacesFromSlice(slice),
    created_paths: created,
    extended_paths: extended,
    created_symbols: createdSymbols,
    structural_changes: structural.map((s) => ({
      path: posixPath(s.path),
      kind: String(s.kind || "shape"),
      detail: String(s.detail || decision.completion_note || "").slice(0, 2000),
    })),
    no_op: noOp,
    behavior_implemented: false,
    product_files_modified: productModified,
    tests_modified: false,
    tests_executed: false,
    authorized_write_paths: workPaths,
    other_eligible_slice_ids: [],
    blockers: [],
    next_stage: "APPLY_RED",
  };
  if (noOp) {
    body.no_op_reason = String(
      decision.completion_note || "no scaffold shape change required",
    ).slice(0, 2000);
    body.product_files_modified = false;
  }
  if (!structural.length && decision.completion_note) {
    body.structural_changes = [
      {
        path: workPaths[0] || "scope",
        kind: "hop_note",
        detail: String(decision.completion_note).slice(0, 2000),
      },
    ];
  }
  mkdirSync(dirname(outPath), { recursive: true });
  const toolingRoot = dirname(fileURLToPath(import.meta.url));
  const schemaPath = resolve(
    existsSync(resourcePath(repo, "opsx_tools"))
      ? resourcePath(repo, "opsx_tools/registry/apply_scaffold.schema.json")
      : resourcePath(toolingRoot, "registry/apply_scaffold.schema.json"),
  );
  const scaffoldValidator = resourcePath(toolingRoot, "opsx-apply-scaffold.mjs");
  return commitReceiptIfValid(repo, outPath, rel.scaffold, body, (candidatePath) => {
    const validateRun = spawnSync(
      "node",
      [scaffoldValidator, "--schema", schemaPath, candidatePath],
      { cwd: repo, encoding: "utf8" },
    );
    let verdict = { status: "FAIL" };
    try {
      verdict = JSON.parse(String(validateRun.stdout || "{}"));
    } catch {
      verdict = { status: "FAIL" };
    }
    if (validateRun.status !== 0 || verdict.status !== "PASS") {
      return {
        ok: false,
        code: "SCAFFOLD_VALIDATE_FAILED",
        errors: verdict.errors || [],
      };
    }
    return { ok: true };
  });
}

function writeRedBlocked(repo, changeId, decision) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) return built;
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, decision.slice_id);
  const outPath = resourcePath(repo, rel.red);
  const blockers = Array.isArray(decision.blockers) ? decision.blockers : [];
  if (!blockers.length) {
    blockers.push({
      code: "HOP_BLOCKED",
      detail: String(decision.gap_note || decision.completion_note || "blocked"),
    });
  }
  const now = new Date().toISOString();
  const body = {
    schema: "opsx.apply_red",
    schema_version: APPLY_RED_SCHEMA_VERSION,
    evidence_contract: RED_EVIDENCE_CONTRACT,
    change_id: changeId,
    slice_id: decision.slice_id,
    status: "BLOCKED",
    generated_at: now,
    chat_history_required: false,
    product_files_modified: false,
    apply_design_identity: built.identity,
    blockers,
    next_stage: "NONE",
    observation_only: true,
    implementation_write_authorized: false,
    test_commands: [],
    test_paths: [],
    command_executions: [],
    tests_frozen: false,
  };
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  return { ok: true, path: rel.red, status: "BLOCKED" };
}

function writeImplementBlocked(repo, changeId, decision) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) return built;
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, decision.slice_id);
  const outPath = resourcePath(repo, rel.implement);
  const blockers = Array.isArray(decision.blockers) ? [...decision.blockers] : [];
  if (!blockers.length) {
    blockers.push({
      code: "HOP_BLOCKED",
      detail: String(decision.gap_note || "blocked"),
    });
  }
  return writeImplementBlockedValidated(repo, outPath, rel.implement, built, {
    changeId,
    sliceId: decision.slice_id,
    blockers,
  });
}

function writeGreenBlocked(repo, changeId, decision) {
  const built = buildApplyDesignIdentity(repo, changeId);
  if (!built.ok) return built;
  const rel = applyReceiptRelPaths(`openspec/changes/${changeId}`, decision.slice_id);
  const outPath = resourcePath(repo, rel.green);
  const blockers = Array.isArray(decision.blockers) ? decision.blockers : [];
  if (!blockers.length) {
    blockers.push({
      code: "HOP_BLOCKED",
      detail: String(decision.gap_note || "blocked"),
    });
  }
  const now = new Date().toISOString();
  const body = {
    schema: "opsx.apply_green",
    schema_version: 1,
    change_id: changeId,
    slice_id: decision.slice_id,
    status: "BLOCKED",
    generated_at: now,
    chat_history_required: false,
    apply_design_identity: built.identity,
    blockers,
    tests_executed: false,
    tests_modified: false,
    product_files_modified: false,
  };
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  return { ok: true, path: rel.green, status: "BLOCKED" };
}

function main() {
  const raw = readFileSync(0, "utf8");
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "INVALID_JSON" })}\n`);
    process.exit(1);
  }
  const repo = resolve(payload.repo || DEFAULT_ROOT);
  const changeId = String(payload.change_id || "").trim();
  const decision = payload.decision;
  if (!changeId || !isObject(decision)) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "ARGS" })}\n`);
    process.exit(2);
  }
  const stage = String(decision.stage || "").trim();
  const sliceId = String(decision.slice_id || "").trim();
  if (!stage || !sliceId) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "ARGS" })}\n`);
    process.exit(2);
  }
  const forbid = [
    "planning_digest",
    "inspected_digest",
    "work_scope_digest",
    "next_stage",
    "apply_design_identity",
    "repository_identity_before",
    "repository_identity_after",
    "artifact_sha256",
    "receipt_body",
    "content",
    "machine_path",
  ];
  for (const key of forbid) {
    if (key in decision) {
      process.stdout.write(
        `${JSON.stringify({ ok: false, code: "FORBIDDEN_PARAM", field: key })}\n`,
      );
      process.exit(3);
    }
  }

  const hopStatus = String(decision.hop_status || "").trim();
  const receiptStatus = String(decision.receipt_status || "").trim();
  const blocked =
    hopStatus === "BLOCKED" || receiptStatus === "BLOCKED";

  let result;
  if (blocked) {
    if (stage === "APPLY_SCAFFOLD") {
      result = writeScaffold(repo, changeId, { ...decision, slice_id: sliceId });
    } else if (stage === "APPLY_RED") {
      result = writeRedBlocked(repo, changeId, { ...decision, slice_id: sliceId });
    } else if (stage === "APPLY_IMPLEMENT") {
      result = writeImplementBlocked(repo, changeId, { ...decision, slice_id: sliceId });
    } else if (stage === "APPLY_GREEN") {
      result = writeGreenBlocked(repo, changeId, { ...decision, slice_id: sliceId });
    } else {
      result = { ok: false, code: "STAGE_UNSUPPORTED" };
    }
  } else if (hopStatus && stage === "APPLY_SCAFFOLD") {
    result = writeScaffoldDeterministic(repo, changeId, sliceId, decision);
  } else if (hopStatus && stage === "APPLY_RED") {
    result = writeRedSuccess(repo, changeId, sliceId, decision);
  } else if (hopStatus && stage === "APPLY_IMPLEMENT") {
    result = writeImplementSuccess(repo, changeId, sliceId, decision);
  } else if (stage === "APPLY_SCAFFOLD") {
    result = writeScaffold(repo, changeId, { ...decision, slice_id: sliceId });
  } else if (stage === "APPLY_RED" && receiptStatus === "BLOCKED") {
    result = writeRedBlocked(repo, changeId, { ...decision, slice_id: sliceId });
  } else if (stage === "APPLY_IMPLEMENT" && receiptStatus === "BLOCKED") {
    result = writeImplementBlocked(repo, changeId, { ...decision, slice_id: sliceId });
  } else if (stage === "APPLY_GREEN" && receiptStatus === "BLOCKED") {
    result = writeGreenBlocked(repo, changeId, { ...decision, slice_id: sliceId });
  } else {
    result = { ok: false, code: "STAGE_UNSUPPORTED" };
  }

  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(result.ok ? 0 : 4);
}

main();
