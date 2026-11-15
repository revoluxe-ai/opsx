/**
 * Owner execution grant binding for agent hop V0 (live SDK only).
 *
 * Workers verify store-resident HMAC grants; they cannot mint signatures.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import {
  computeAttestationBodySha256,
  loadOwnerAttestation,
  OWNER_ATTESTATION_PREFIX,
} from "./opsx-super-implement-live-v1.mjs";
import {
  resolveStoreGrantPath,
  validateAgentHopMandatoryGrantFields,
  validateOwnerExecutionGrant,
} from "./opsx-super-implement-owner-grant.mjs";
import { validateOwnerAttestationDocument } from "./opsx-super-implement-owner-schema-validate.mjs";

function stableModelRouteString(route) {
  if (!route || typeof route !== "object") return "";
  const keys = Object.keys(route).sort();
  const sorted = {};
  for (const k of keys) sorted[k] = route[k];
  return JSON.stringify(sorted);
}

export function sha256ModelRoute(modelRoute) {
  return createHash("sha256").update(stableModelRouteString(modelRoute), "utf8").digest("hex");
}

function loadGrantBody(grantRef) {
  const resolved = resolveStoreGrantPath(grantRef);
  if (!resolved.ok) return resolved;
  if (!existsSync(resolved.abs_path)) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_MISSING" };
  }
  try {
    const grant = JSON.parse(readFileSync(resolved.abs_path, "utf8"));
    return { ok: true, grant };
  } catch {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_CORRUPT" };
  }
}

/**
 * Resolve effective model route: Owner grant pins authority; caller cannot override.
 */
export function resolveOwnerBoundModelRoute({ grant, callerRoute = null }) {
  if (!grant?.bound_model_route || typeof grant.bound_model_route !== "object") {
    return { ok: false, code: "OWNER_GRANT_MANDATORY_FIELD_MISSING", field: "bound_model_route" };
  }
  const pinnedSha = grant.bound_model_route_sha256 || sha256ModelRoute(grant.bound_model_route);
  const computedSha = sha256ModelRoute(grant.bound_model_route);
  if (pinnedSha !== computedSha) {
    return { ok: false, code: "OWNER_GRANT_MODEL_ROUTE_SHA256_MISMATCH" };
  }
  if (callerRoute) {
    const callerSha = sha256ModelRoute(callerRoute);
    if (callerSha !== pinnedSha) {
      return {
        ok: false,
        code: "OWNER_GRANT_MODEL_ROUTE_OVERRIDE_FORBIDDEN",
        tier_substitution_forbidden: true,
      };
    }
  }
  return {
    ok: true,
    route: grant.bound_model_route,
    effective_source: "owner_grant_bound_model_route",
    model_route_sha256: pinnedSha,
  };
}

const AGENT_HOP_WRITE_SCOPE = "agent_hop_v0";

/**
 * Owner attestation for agent hop (distinct from split_apply native stage attestation).
 */
export function validateAgentHopOwnerAttestation({
  repoRoot,
  changeRoot,
  changeId,
  planningDigest,
  primitiveCommand,
  repositoryHead = null,
  attestation = null,
} = {}) {
  const doc = attestation;
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
  const bodySha = computeAttestationBodySha256(doc);
  if (doc.attestation_body_sha256 && doc.attestation_body_sha256 !== bodySha) {
    return { ok: false, code: "ATTESTATION_BODY_SHA256_MISMATCH" };
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
  if (doc.permitted_write_scope !== AGENT_HOP_WRITE_SCOPE) {
    return { ok: false, code: "ATTESTATION_WRITE_SCOPE_INVALID" };
  }
  const permitted = doc.permitted_primitive_commands || [];
  if (!primitiveCommand || !permitted.includes(primitiveCommand)) {
    return { ok: false, code: "ATTESTATION_PRIMITIVE_NOT_PERMITTED", primitive: primitiveCommand };
  }
  if (!doc.owner_authority_grant_ref) {
    return {
      ok: false,
      code: "OWNER_EXECUTION_GRANT_REF_MISSING",
      recovery_contract: {
        code: "OWNER_EXECUTION_GRANT_REF_MISSING",
        next_action: "Owner must publish store:-resident HMAC grant",
        resume_requires: ["owner_execution_grant_verified"],
      },
    };
  }
  return { ok: true, attestation_ref: doc.owner_attestation_ref };
}

/**
 * Validate Owner grant for one agent hop before SDK launch.
 */
export function validateAgentHopOwnerGrant({
  repoRoot,
  changeRoot,
  spec,
  permittedWriteGlobs,
  callerModelRoute = null,
  nowMs = Date.now(),
} = {}) {
  const attestation = loadOwnerAttestation(repoRoot, changeRoot);
  const att = validateAgentHopOwnerAttestation({
    repoRoot,
    changeRoot,
    changeId: spec.change_id,
    planningDigest: spec.planning_digest,
    primitiveCommand: spec.primitive_command,
    attestation,
  });
  if (!att.ok) return att;
  if (!attestation.owner_authority_grant_ref) {
    return {
      ok: false,
      code: "OWNER_EXECUTION_GRANT_REF_MISSING",
      recovery_contract: {
        code: "OWNER_EXECUTION_GRANT_REF_MISSING",
        next_action: "publish store:-resident Owner HMAC grant on attestation",
        resume_requires: ["owner_execution_grant_verified"],
      },
    };
  }
  const routeProbe = callerModelRoute ? sha256ModelRoute(callerModelRoute) : null;
  const grantResult = validateOwnerExecutionGrant({
    grantRef: attestation.owner_authority_grant_ref,
    attestation,
    executionIdentity: { planning_digest: spec.planning_digest },
    modelRouteSha256: routeProbe,
    nowMs,
    repoRoot,
  });
  if (!grantResult.ok) return grantResult;

  const loaded = loadGrantBody(attestation.owner_authority_grant_ref);
  if (!loaded.ok) return loaded;
  const grant = loaded.grant;

  const mandatory = validateAgentHopMandatoryGrantFields(grant);
  if (!mandatory.ok) return mandatory;

  if (grant.bound_execution_id !== spec.execution_id) {
    return { ok: false, code: "OWNER_GRANT_EXECUTION_ID_MISMATCH" };
  }
  if (grant.bound_stage !== spec.stage) {
    return { ok: false, code: "OWNER_GRANT_STAGE_MISMATCH" };
  }
  if (grant.bound_primitive_command !== spec.primitive_command) {
    return { ok: false, code: "OWNER_GRANT_PRIMITIVE_MISMATCH" };
  }
  if (grant.bound_planning_digest !== spec.planning_digest) {
    return { ok: false, code: "OWNER_GRANT_PLANNING_DIGEST_MISMATCH" };
  }
  if (Array.isArray(permittedWriteGlobs)) {
    const allowed = new Set(grant.permitted_write_globs.map(String));
    for (const g of permittedWriteGlobs) {
      if (!allowed.has(String(g))) {
        return { ok: false, code: "OWNER_GRANT_WRITE_SCOPE_MISMATCH", glob: g };
      }
    }
  }
  const model = resolveOwnerBoundModelRoute({ grant, callerRoute: callerModelRoute });
  if (!model.ok) return model;
  return {
    ok: true,
    grant_id: grantResult.grant_id,
    grant,
    model_route: model.route,
    model_route_source: model.effective_source,
    model_route_sha256: model.model_route_sha256,
    attestation,
  };
}
