import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Super Implement V1 — live execution readiness, native dispatch proof, and
 * durable dispatch ledger for crash recovery.
 *
 * V0 live SDK transport remains hard-disabled; V1 gates cursor-sdk and
 * native-tool adapters independently with Owner attestation and prerequisites.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  NATIVE_PRIMITIVE_BY_STAGE,
  assertDispatchCommandAllowed,
} from "./opsx-super-implement.mjs";
import {
  commandIdFromPrimitive,
  validateNativeCommandContractExists,
} from "./opsx-super-implement-executor.mjs";
import {
  NATIVE_TOOL_SCRIPT_BY_COMMAND,
  STAGE_PERMITTED_WRITE_SURFACES,
} from "./opsx-super-implement-stage-policy.mjs";

export { NATIVE_TOOL_SCRIPT_BY_COMMAND } from "./opsx-super-implement-stage-policy.mjs";
import { invokeNativePrimitiveSubprocess } from "./opsx-super-implement-native-invoke.mjs";
import { evaluateOsWriteIsolationAvailable } from "./opsx-super-implement-os-sandbox.mjs";
import { validateOwnerExecutionGrant } from "./opsx-super-implement-owner-grant.mjs";
import { validateOwnerAttestationDocument } from "./opsx-super-implement-owner-schema-validate.mjs";

export { STAGE_PERMITTED_WRITE_SURFACES } from "./opsx-super-implement-stage-policy.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const SUPER_IMPLEMENT_V1_ENABLED = true;
export const OWNER_ATTESTATION_PREFIX = /^owner-message:/;

/** Explicit env + attestation required before real cursor_executor subprocess. */
export function isV1LiveSdkTransportEnabled() {
  return process.env.SUPER_IMPLEMENT_V1_LIVE_SDK_TRANSPORT_ENABLED === "true";
}

export const APPLY_SCHEMA_REL_BY_STAGE = Object.freeze({
  APPLY_DESIGN: "opsx_tools/registry/apply_design.schema.json",
  APPLY_DESIGN_REVIEW: "opsx_tools/registry/apply_design_review.schema.json",
  APPLY_SCAFFOLD: "opsx_tools/registry/apply_scaffold.schema.json",
  APPLY_RED: "opsx_tools/registry/apply_red.schema.json",
  APPLY_IMPLEMENT: "opsx_tools/registry/apply_implement.schema.json",
  APPLY_GREEN: "opsx_tools/registry/apply_green.schema.json",
});

export const MAX_UNRESOLVED_FAILURE_REPEATS = 3;

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

/**
 * Canonical SHA-256 of attestation body (excludes self-referential proof fields).
 */
export function computeAttestationBodySha256(attestationDoc) {
  const body = { ...attestationDoc };
  delete body.attestation_body_sha256;
  delete body.authority_proof_rel_path;
  const canonical = stableStringify(body);
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

export function ownerAuthorityProofAbsPath(repoRoot, changeRoot, attestationDoc) {
  const rel = String(attestationDoc.authority_proof_rel_path || "").replace(/\\/g, "/");
  return resourcePath(repoRoot, changeRoot, rel);
}

/**
 * Verify Owner-held authority proof file matches attestation bindings (not prefix-only).
 */
export function validateOwnerAuthorityProof({ repoRoot, changeRoot, attestation }) {
  if (!attestation?.authority_proof_rel_path) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_PATH_MISSING" };
  }
  if (!attestation?.attestation_body_sha256) {
    return { ok: false, code: "ATTESTATION_BODY_SHA256_MISSING" };
  }
  const expectedBody = computeAttestationBodySha256(attestation);
  if (attestation.attestation_body_sha256 !== expectedBody) {
    return { ok: false, code: "ATTESTATION_BODY_SHA256_MISMATCH" };
  }
  const abs = ownerAuthorityProofAbsPath(repoRoot, changeRoot, attestation);
  if (!existsSync(abs)) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_MISSING", path: attestation.authority_proof_rel_path };
  }
  let proof;
  try {
    proof = JSON.parse(readFileSync(abs, "utf8"));
  } catch {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_CORRUPT" };
  }
  if (proof.schema !== "opsx.super_implement_owner_authority_proof" || proof.schema_version !== 1) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_SCHEMA_INVALID" };
  }
  if (proof.attestation_body_sha256 !== attestation.attestation_body_sha256) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_BODY_STALE" };
  }
  if (proof.owner_attestation_ref !== attestation.owner_attestation_ref) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_REF_MISMATCH" };
  }
  if (
    proof.bound_change_root !== attestation.bound_change_root ||
    proof.bound_change_id !== attestation.bound_change_id ||
    proof.bound_planning_digest !== attestation.bound_planning_digest ||
    proof.permitted_write_scope !== attestation.permitted_write_scope
  ) {
    return { ok: false, code: "OWNER_AUTHORITY_PROOF_BINDING_MISMATCH" };
  }
  return { ok: true, authority_proof_sha256: createHash("sha256").update(readFileSync(abs)).digest("hex") };
}

export function ownerAttestationRelPath(changeRoot) {
  const cr = String(changeRoot).replace(/\\/g, "/").replace(/\/$/, "");
  return `${cr}/super-implement/OWNER_EXECUTION_ATTESTATION.json`;
}

export function dispatchLedgerRelPath(changeRoot) {
  const cr = String(changeRoot).replace(/\\/g, "/").replace(/\/$/, "");
  return `${cr}/super-implement/DISPATCH_LEDGER.json`;
}

export function hasCursorApiKeyConfigured() {
  const key = process.env.CURSOR_API_KEY;
  return typeof key === "string" && key.trim().length > 0;
}

/**
 * Validate six TARGET native APPLY JSON schemas exist and parse.
 */
export function validateTargetNativeSchemas(repoRoot = DEFAULT_ROOT) {
  const missing = [];
  const digests = {};
  for (const [stage, rel] of Object.entries(APPLY_SCHEMA_REL_BY_STAGE)) {
    const abs = resourcePath(repoRoot, rel);
    if (!existsSync(abs)) {
      missing.push({ stage, path: rel });
      continue;
    }
    try {
      const bytes = readFileSync(abs);
      JSON.parse(bytes.toString("utf8"));
      digests[stage] = createHash("sha256").update(bytes).digest("hex");
    } catch {
      missing.push({ stage, path: rel, corrupt: true });
    }
  }
  if (missing.length) {
    return { ok: false, code: "TARGET_NATIVE_SCHEMAS_INVALID", missing };
  }
  return { ok: true, schema_sha256_by_stage: digests };
}

export function validateNativeToolScript(repoRoot, primitiveCommand) {
  const rel = NATIVE_TOOL_SCRIPT_BY_COMMAND[primitiveCommand];
  if (!rel) {
    return { ok: false, code: "NATIVE_TOOL_SCRIPT_UNKNOWN", command: primitiveCommand };
  }
  const abs = resourcePath(repoRoot, rel);
  if (!existsSync(abs)) {
    return { ok: false, code: "NATIVE_TOOL_SCRIPT_MISSING", path: rel };
  }
  const bytes = readFileSync(abs);
  return {
    ok: true,
    rel_path: rel,
    tool_script_sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

export function loadOwnerAttestation(repoRoot, changeRoot) {
  const abs = resourcePath(repoRoot, ownerAttestationRelPath(changeRoot));
  if (!existsSync(abs)) return null;
  try {
    return JSON.parse(readFileSync(abs, "utf8"));
  } catch {
    return { parse_error: true };
  }
}

/**
 * Owner execution attestation bound to change, planning digest, and optional HEAD.
 */
export function validateOwnerExecutionAttestation({
  repoRoot,
  changeRoot,
  changeId,
  planningDigest,
  repositoryHead = null,
  attestation = null,
} = {}) {
  const doc = attestation ?? loadOwnerAttestation(repoRoot, changeRoot);
  if (!doc || doc.parse_error) {
    return {
      ok: false,
      code: "OWNER_EXECUTION_ATTESTATION_MISSING",
      recovery_contract: {
        code: "OWNER_EXECUTION_ATTESTATION_MISSING",
        next_action: "write OWNER_EXECUTION_ATTESTATION.json after Owner GO",
        resume_requires: ["owner_execution_attestation"],
      },
    };
  }
  const schemaDoc = validateOwnerAttestationDocument(doc);
  if (!schemaDoc.ok) return schemaDoc;
  if (doc.schema !== "opsx.super_implement_owner_attestation" || doc.schema_version !== 1) {
    return { ok: false, code: "OWNER_ATTESTATION_SCHEMA_INVALID" };
  }
  if (!OWNER_ATTESTATION_PREFIX.test(String(doc.owner_attestation_ref || ""))) {
    return { ok: false, code: "ATTESTATION_REF_NOT_OWNER_HELD" };
  }
  if (doc.bound_change_root !== changeRoot) {
    return { ok: false, code: "ATTESTATION_CHANGE_ROOT_MISMATCH" };
  }
  if (doc.bound_change_id !== changeId) {
    return { ok: false, code: "ATTESTATION_CHANGE_ID_MISMATCH" };
  }
  if (doc.bound_planning_digest !== planningDigest) {
    return { ok: false, code: "ATTESTATION_PLANNING_DIGEST_STALE" };
  }
  if (doc.bound_repository_head && repositoryHead && doc.bound_repository_head !== repositoryHead) {
    return { ok: false, code: "ATTESTATION_REPOSITORY_HEAD_STALE" };
  }
  if (doc.permitted_write_scope !== "split_apply_native_stages") {
    return { ok: false, code: "ATTESTATION_WRITE_SCOPE_INVALID" };
  }
  const expected = Object.values(NATIVE_PRIMITIVE_BY_STAGE);
  const permitted = doc.permitted_primitive_commands || [];
  for (const cmd of expected) {
    if (!permitted.includes(cmd)) {
      return { ok: false, code: "ATTESTATION_PRIMITIVE_SET_INCOMPLETE", missing: cmd };
    }
  }
  if (!doc.owner_authority_grant_ref) {
    return {
      ok: false,
      code: "OWNER_EXECUTION_GRANT_REF_MISSING",
      recovery_contract: {
        code: "OWNER_EXECUTION_GRANT_REF_MISSING",
        next_action: "Owner must publish store:-resident HMAC grant; local proof JSON is insufficient",
        resume_requires: ["owner_execution_grant_verified"],
      },
    };
  }
  const grant = validateOwnerExecutionGrant({
    grantRef: doc.owner_authority_grant_ref,
    attestation: doc,
    executionIdentity: { planning_digest: planningDigest },
  });
  if (!grant.ok) {
    return {
      ...grant,
      recovery_contract: grant.recovery_contract || {
        code: grant.code,
        next_action: "verify Owner HMAC grant at store path",
        resume_requires: ["owner_execution_grant_verified"],
      },
    };
  }
  return {
    ok: true,
    attestation_ref: doc.owner_attestation_ref,
    owner_grant_id: grant.grant_id,
  };
}

export function evaluateStageWriteIsolation({ stage, primitiveCommand }) {
  const allow = assertDispatchCommandAllowed(primitiveCommand);
  if (!allow.ok) return allow;
  const surfaces = STAGE_PERMITTED_WRITE_SURFACES[stage];
  if (!surfaces) {
    return { ok: false, code: "STAGE_WRITE_ISOLATION_UNKNOWN", stage };
  }
  return { ok: true, permitted_surfaces: surfaces, stage };
}

/**
 * Bind dispatch evidence to execution identity and frozen revision inputs.
 */
export function bindEvidenceIdentity({ run, spec, ctx }) {
  const planningDigest = ctx?.design?.planning_identity?.planning_digest;
  const boundDigest = run?.execution_identity?.planning_digest;
  if (boundDigest && planningDigest && boundDigest !== planningDigest) {
    return { ok: false, code: "EVIDENCE_PLANNING_DIGEST_DRIFT" };
  }
  const inflightDigest = spec?.input_revision?.planning_digest;
  if (inflightDigest && planningDigest && inflightDigest !== planningDigest) {
    return { ok: false, code: "EVIDENCE_INFLIGHT_REVISION_STALE" };
  }
  const hop = spec?.hop_identity?.hop_digest;
  const stage = spec?.stage;
  const sliceId = spec?.slice_id ?? null;
  return {
    ok: true,
    binding: {
      planning_digest: planningDigest,
      hop_digest: hop,
      stage,
      slice_id: sliceId,
      execution_id: spec?.execution_id,
    },
  };
}

/**
 * Fail-closed live readiness for non-recording adapters.
 */
export function evaluateSuperImplementV1LiveReadiness({
  repoRoot = DEFAULT_ROOT,
  changeRoot,
  run,
  ctx,
  adapterKind,
} = {}) {
  const blockers = [];
  if (!SUPER_IMPLEMENT_V1_ENABLED) {
    blockers.push("SUPER_IMPLEMENT_V1_NOT_ENABLED");
  }
  if (!run?.live_execution_authorized) {
    blockers.push("LIVE_EXECUTION_NOT_AUTHORIZED");
  }
  const schemas = validateTargetNativeSchemas(repoRoot);
  if (!schemas.ok) blockers.push(schemas.code);
  const att = validateOwnerExecutionAttestation({
    repoRoot,
    changeRoot,
    changeId: ctx?.changeId ?? run?.change_id,
    planningDigest: ctx?.design?.planning_identity?.planning_digest ?? run?.execution_identity?.planning_digest,
    repositoryHead: run?.expected_head || ctx?.design?.repository_identity?.head || null,
  });
  if (!att.ok) blockers.push(att.code);
  if (adapterKind === "cursor-sdk") {
    blockers.push("CURSOR_SDK_MUTATING_SUPER_IMPLEMENT_DISABLED");
    if (!isV1LiveSdkTransportEnabled()) {
      blockers.push("LIVE_SDK_TRANSPORT_DISABLED_V1");
    }
    if (!hasCursorApiKeyConfigured()) {
      blockers.push("CURSOR_API_KEY_MISSING");
    }
    const raw = process.env.SUPER_IMPLEMENT_RESOLVED_MODEL_ROUTE_JSON;
    if (!raw) {
      blockers.push("MODEL_ROUTE_UNRESOLVED_FOR_LIVE");
    }
  }
  if (adapterKind === "native-tool") {
    const osIso = evaluateOsWriteIsolationAvailable();
    if (!osIso.ok) blockers.push(osIso.code);
    for (const cmd of Object.values(NATIVE_PRIMITIVE_BY_STAGE)) {
      const tool = validateNativeToolScript(repoRoot, cmd);
      if (!tool.ok) blockers.push(tool.code);
      const contract = validateNativeCommandContractExists(repoRoot, cmd);
      if (!contract.ok) blockers.push(contract.code);
    }
  }
  if (blockers.length) {
    return {
      ok: false,
      code: "LIVE_EXECUTION_BLOCKED",
      live_ready: false,
      blockers,
      recovery_contract: {
        code: "LIVE_EXECUTION_BLOCKED",
        next_action: "close all blockers before live Super dispatch",
        resume_requires: blockers,
      },
    };
  }
  return { ok: true, live_ready: true, attestation_ref: att.attestation_ref };
}

export function loadDispatchLedger(repoRoot, changeRoot) {
  const abs = resourcePath(repoRoot, dispatchLedgerRelPath(changeRoot));
  if (!existsSync(abs)) {
    return {
      schema: "opsx.super_implement_dispatch_ledger",
      schema_version: 1,
      change_id: null,
      entries: [],
    };
  }
  try {
    return JSON.parse(readFileSync(abs, "utf8"));
  } catch {
    return { corrupt: true, entries: [] };
  }
}

/**
 * Append-only dispatch ledger with idempotent dispatch_id.
 */
export function appendDispatchLedgerEntry(
  repoRoot,
  changeRoot,
  { changeId, dispatchId, executionId, stage, sliceId, status, transport, nativeDispatchProof },
) {
  const rel = dispatchLedgerRelPath(changeRoot);
  const abs = resourcePath(repoRoot, rel);
  mkdirSync(dirname(abs), { recursive: true });
  let ledger = loadDispatchLedger(repoRoot, changeRoot);
  if (ledger.corrupt) {
    return { ok: false, code: "DISPATCH_LEDGER_CORRUPT" };
  }
  ledger.change_id = changeId;
  const existing = (ledger.entries || []).find((e) => e.dispatch_id === dispatchId);
  if (existing) {
    if (existing.execution_id !== executionId) {
      return { ok: false, code: "DISPATCH_ID_EXECUTION_CONFLICT", dispatch_id: dispatchId };
    }
    return { ok: true, idempotent: true, ledger };
  }
  ledger.entries = [
    ...(ledger.entries || []),
    {
      dispatch_id: dispatchId,
      execution_id: executionId,
      stage,
      slice_id: sliceId ?? null,
      status,
      transport,
      native_dispatch_proof: nativeDispatchProof ?? null,
      recorded_at: new Date().toISOString(),
    },
  ];
  const body = `${JSON.stringify(ledger, null, 2)}\n`;
  const tmp = `${abs}.${createHash("sha256").update(body).digest("hex").slice(0, 8)}.tmp`;
  writeFileSync(tmp, body, "utf8");
  renameSync(tmp, abs);
  return { ok: true, ledger };
}

export function findDispatchLedgerEntry(repoRoot, changeRoot, dispatchId) {
  const ledger = loadDispatchLedger(repoRoot, changeRoot);
  return (ledger.entries || []).find((e) => e.dispatch_id === dispatchId) || null;
}

/**
 * Transition an existing ledger entry to a terminal status (idempotent).
 */
export function finalizeDispatchLedgerEntry(
  repoRoot,
  changeRoot,
  { dispatchId, status, nativeDispatchProof },
) {
  const rel = dispatchLedgerRelPath(changeRoot);
  const abs = resourcePath(repoRoot, rel);
  if (!existsSync(abs)) {
    return { ok: false, code: "DISPATCH_LEDGER_MISSING" };
  }
  const ledger = loadDispatchLedger(repoRoot, changeRoot);
  const idx = (ledger.entries || []).findIndex((e) => e.dispatch_id === dispatchId);
  if (idx < 0) {
    return { ok: false, code: "DISPATCH_LEDGER_ENTRY_MISSING" };
  }
  ledger.entries[idx] = {
    ...ledger.entries[idx],
    status,
    native_dispatch_proof: nativeDispatchProof ?? ledger.entries[idx].native_dispatch_proof,
    finalized_at: new Date().toISOString(),
  };
  const body = `${JSON.stringify(ledger, null, 2)}\n`;
  const tmp = `${abs}.fin.tmp`;
  writeFileSync(tmp, body, "utf8");
  renameSync(tmp, abs);
  return { ok: true };
}

/**
 * Static preflight metadata for a planned native hop (not invocation proof).
 */
export function buildNativeDispatchPreflight(repoRoot, spec) {
  const primitive = spec.primitive_command;
  const contract = validateNativeCommandContractExists(repoRoot, primitive);
  const tool = validateNativeToolScript(repoRoot, primitive);
  const commandId = commandIdFromPrimitive(primitive);
  return {
    native_primitive_command: primitive,
    command_id: commandId,
    command_contract_sha256: contract.ok ? contract.contract_sha256 : null,
    tool_script_path: tool.ok ? tool.rel_path : null,
    tool_script_sha256: tool.ok ? tool.tool_script_sha256 : null,
    stage: spec.stage,
    slice_id: spec.slice_id ?? null,
    hop_digest: spec.hop_identity?.hop_digest ?? spec.input_revision?.hop_digest ?? null,
  };
}

/** @deprecated use buildNativeDispatchPreflight + mergeNativeDispatchProof */
export function buildNativeDispatchProof(repoRoot, spec) {
  return buildNativeDispatchPreflight(repoRoot, spec);
}

/**
 * Merge subprocess invocation evidence into verifiable dispatch proof.
 */
export function mergeNativeDispatchProof(preflight, invokeResult, spec) {
  const slashImitation = invokeResult?.transport !== "native_subprocess" || !invokeResult?.subprocess_invoked;
  return {
    proof_kind: "native_subprocess_v1",
    proof_source: "opsx-super-implement-native-invoke.mjs",
    slash_prompt_imitation: slashImitation,
    subprocess_invoked: invokeResult?.subprocess_invoked === true,
    invocation_id: invokeResult?.invocation_id ?? spec?.dispatch_id ?? null,
    execution_id: spec?.execution_id ?? null,
    invoked_command: invokeResult?.invoked_command ?? spec?.primitive_command ?? null,
    invoked_argv: invokeResult?.invoked_argv ?? null,
    subprocess_exit_code: invokeResult?.exit_code ?? null,
    stdout_sha256: invokeResult?.stdout_sha256 ?? null,
    worker_code: invokeResult?.code ?? null,
    ...preflight,
  };
}

/**
 * Machine verification that dispatch proof matches spec and subprocess outcome.
 */
export function verifyNativeDispatchProof(spec, execResult) {
  const proof = execResult?.native_dispatch_proof;
  if (!proof || proof.proof_kind !== "native_subprocess_v1") {
    return { ok: false, code: "NATIVE_DISPATCH_PROOF_MISSING" };
  }
  if (proof.slash_prompt_imitation === true) {
    return { ok: false, code: "NATIVE_DISPATCH_SLASH_IMITATION" };
  }
  if (proof.execution_id !== spec.execution_id) {
    return { ok: false, code: "NATIVE_DISPATCH_EXECUTION_ID_MISMATCH" };
  }
  if (proof.invocation_id !== spec.dispatch_id) {
    return { ok: false, code: "NATIVE_DISPATCH_INVOCATION_ID_MISMATCH" };
  }
  if (proof.invoked_command !== spec.primitive_command) {
    return { ok: false, code: "NATIVE_DISPATCH_COMMAND_MISMATCH" };
  }
  if (!proof.subprocess_invoked || proof.subprocess_exit_code !== 0) {
    return { ok: false, code: "NATIVE_DISPATCH_SUBPROCESS_NOT_SUCCEEDED" };
  }
  if (!proof.stdout_sha256) {
    return { ok: false, code: "NATIVE_DISPATCH_STDOUT_EVIDENCE_MISSING" };
  }
  return { ok: true };
}

/**
 * Deterministic stop when the same operative failure repeats.
 */
export function evaluateConvergenceStop(run, operative) {
  const code = operative?.code || "UNKNOWN_OPERATIVE";
  const signature = `${operative?.stage || run?.in_flight_dispatch?.stage || "?"}:${code}`;
  const prev = run?.convergence_state || { last_signature: null, repeat_count: 0 };
  const repeat = prev.last_signature === signature ? prev.repeat_count + 1 : 1;
  const next = { last_signature: signature, repeat_count: repeat };
  if (repeat >= MAX_UNRESOLVED_FAILURE_REPEATS) {
    return {
      stop: true,
      run: { ...run, convergence_state: next },
      code: "CONVERGENCE_STOP_UNRESOLVED_FAILURE",
      recovery_contract: {
        code: "CONVERGENCE_STOP_UNRESOLVED_FAILURE",
        next_action: "Owner review required; do not auto-retry",
        resume_requires: ["owner_review"],
      },
    };
  }
  return { stop: false, run: { ...run, convergence_state: next } };
}

function runPythonDispatchPreflight(repoRoot, spec) {
  const executorPkg = resourcePath(repoRoot, "tools", "cursor_executor");
  const payload = JSON.stringify({
    execution_id: spec.execution_id,
    cursor_request: spec.cursor_request,
  });
  const proc = spawnSync(
    process.env.SUPER_IMPLEMENT_PYTHON || "python3",
    ["-c", `import json,sys; from cursor_executor.super_implement_bridge import validate_super_implement_dispatch; print(json.dumps(validate_super_implement_dispatch(json.loads(sys.stdin.read()))))`],
    {
      cwd: executorPkg,
      input: payload,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: executorPkg },
    },
  );
  if (proc.status !== 0) {
    return { ok: false, code: "NATIVE_PREFLIGHT_SUBPROCESS_FAILED" };
  }
  try {
    return JSON.parse(proc.stdout || "{}");
  } catch {
    return { ok: false, code: "NATIVE_PREFLIGHT_PARSE_FAILED" };
  }
}

/**
 * Native-tool executor: proves command contract + tool script before handler.
 */
export function createNativeToolExecutorAdapter({
  repoRoot = DEFAULT_ROOT,
  changeRoot = null,
  contractRepoRoot = repoRoot,
  onExecute = null,
} = {}) {
  return {
    is_simulated: false,
    transport: "native-tool",
    native_dispatch_required: true,
    async execute(spec) {
      const preflightMeta = buildNativeDispatchPreflight(contractRepoRoot, spec);
      if (!preflightMeta.tool_script_sha256 || !preflightMeta.command_contract_sha256) {
        return {
          terminal_class: "BLOCKED",
          code: "NATIVE_DISPATCH_PROOF_INCOMPLETE",
          native_dispatch_proof: preflightMeta,
          transport: "native-tool",
          subprocess_invoked: false,
        };
      }
      const preflight = runPythonDispatchPreflight(contractRepoRoot, spec);
      if (!preflight.ok) {
        return {
          terminal_class: "BLOCKED",
          code: preflight.preflight_code || preflight.code || "NATIVE_PREFLIGHT_FAILED",
          native_dispatch_proof: preflightMeta,
          preflight,
          transport: "native-tool",
          subprocess_invoked: true,
        };
      }
      if (onExecute) {
        const legacy = await onExecute(spec);
        return {
          ...legacy,
          native_dispatch_proof: mergeNativeDispatchProof(
            preflightMeta,
            {
              transport: "native_subprocess",
              subprocess_invoked: false,
              invocation_id: spec.dispatch_id,
              invoked_command: spec.primitive_command,
              exit_code: legacy.terminal_class === "SUCCEEDED" ? 0 : 1,
              stdout_sha256: createHash("sha256").update("legacy-onExecute", "utf8").digest("hex"),
              code: legacy.code || "LEGACY_ON_EXECUTE",
            },
            spec,
          ),
          preflight,
          transport: "native-tool",
        };
      }
      const cr =
        changeRoot ||
        spec.change_root ||
        spec.cursor_request?.metadata?.change_id ||
        null;
      if (!cr) {
        return {
          terminal_class: "BLOCKED",
          code: "NATIVE_DISPATCH_CHANGE_ROOT_MISSING",
          transport: "native-tool",
        };
      }
      const invoke = invokeNativePrimitiveSubprocess(repoRoot, spec, { changeRoot: cr });
      const proof = mergeNativeDispatchProof(preflightMeta, invoke, spec);
      const verified = verifyNativeDispatchProof(spec, { native_dispatch_proof: proof });
      if (!verified.ok) {
        return {
          terminal_class: "BLOCKED",
          code: verified.code,
          native_dispatch_proof: proof,
          preflight,
          invoke,
          transport: "native-tool",
          subprocess_invoked: invoke.subprocess_invoked,
        };
      }
      if (!invoke.ok) {
        return {
          terminal_class: "BLOCKED",
          code: invoke.code,
          native_dispatch_proof: proof,
          preflight,
          invoke,
          transport: "native-tool",
          subprocess_invoked: true,
        };
      }
      return {
        terminal_class: "SUCCEEDED",
        code: invoke.code,
        execution_id: spec.execution_id,
        native_dispatch_proof: proof,
        preflight,
        invoke,
        transport: "native-tool",
        subprocess_invoked: true,
        slash_prompt_imitation: false,
      };
    },
    async reconcile(executionId) {
      return {
        terminal_class: "UNKNOWN",
        reconciliation_state: "INDETERMINATE",
        execution_id: executionId,
        transport: "native-tool",
      };
    },
  };
}
