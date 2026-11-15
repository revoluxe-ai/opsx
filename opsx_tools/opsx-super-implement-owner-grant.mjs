/**
 * Owner-held execution grant verification (HMAC, store-resident).
 *
 * Workers cannot mint valid signatures without SUPER_IMPLEMENT_OWNER_AUTHORITY_HMAC_SECRET.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateOwnerExecutionGrantDocument } from "./opsx-super-implement-owner-schema-validate.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const OWNER_GRANT_SCHEMA = "opsx.super_implement_owner_execution_grant";
export const STORE_REF_PREFIX = /^store:(\/cursor\/stores\/|cursor-stores\/)/;

/** Agent hop live SDK: every signed grant must carry these bindings (fail closed if omitted). */
export const AGENT_HOP_GRANT_MANDATORY_STRING_FIELDS = Object.freeze([
  "bound_execution_id",
  "bound_stage",
  "bound_primitive_command",
  "bound_planning_digest",
  "bound_model_route_sha256",
  "expires_at",
]);

export const AGENT_HOP_GRANT_WRITE_SCOPE = "agent_hop_v0";

/**
 * Fail closed when a signed grant omits agent-hop mandatory bindings.
 */
export function validateAgentHopMandatoryGrantFields(grant) {
  if (!grant || typeof grant !== "object") {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_CORRUPT" };
  }
  for (const field of AGENT_HOP_GRANT_MANDATORY_STRING_FIELDS) {
    const val = grant[field];
    if (typeof val !== "string" || !val.trim()) {
      return { ok: false, code: "OWNER_GRANT_MANDATORY_FIELD_MISSING", field };
    }
  }
  if (!grant.bound_model_route || typeof grant.bound_model_route !== "object") {
    return { ok: false, code: "OWNER_GRANT_MANDATORY_FIELD_MISSING", field: "bound_model_route" };
  }
  if (!Array.isArray(grant.permitted_write_globs) || grant.permitted_write_globs.length === 0) {
    return { ok: false, code: "OWNER_GRANT_MANDATORY_FIELD_MISSING", field: "permitted_write_globs" };
  }
  if (grant.permitted_write_scope !== AGENT_HOP_GRANT_WRITE_SCOPE) {
    return { ok: false, code: "OWNER_GRANT_WRITE_SCOPE_INVALID", expected: AGENT_HOP_GRANT_WRITE_SCOPE };
  }
  return { ok: true };
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

export function resolveStoreGrantPath(grantRef) {
  const raw = String(grantRef || "");
  if (!STORE_REF_PREFIX.test(raw)) {
    return { ok: false, code: "OWNER_GRANT_REF_NOT_STORE_HELD" };
  }
  const pathPart = raw.replace(/^store:/, "");
  const abs = pathPart.startsWith("/") ? pathPart : resolve(pathPart);
  return { ok: true, abs_path: abs };
}

export function computeOwnerGrantSignatureBody(grant) {
  const body = { ...grant };
  delete body.signature_hmac_sha256;
  return stableStringify(body);
}

export function signOwnerGrant(grant, secret) {
  const payload = computeOwnerGrantSignatureBody(grant);
  return createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}

/**
 * Verify grant file at store: path with Owner-only HMAC secret in environment.
 */
export function validateOwnerExecutionGrant({
  grantRef,
  attestation,
  executionIdentity = null,
  modelRouteSha256 = null,
  nowMs = Date.now(),
  repoRoot = DEFAULT_ROOT,
} = {}) {
  const secret = process.env.SUPER_IMPLEMENT_OWNER_AUTHORITY_HMAC_SECRET;
  if (!secret || secret.length < 16) {
    return {
      ok: false,
      code: "OWNER_AUTHORITY_HMAC_SECRET_MISSING",
      recovery_contract: {
        code: "OWNER_AUTHORITY_HMAC_SECRET_MISSING",
        next_action: "set HMAC secret only on Owner execution host; never in cloud agent",
        resume_requires: ["owner_hmac_secret_configured"],
      },
    };
  }
  const resolved = resolveStoreGrantPath(grantRef);
  if (!resolved.ok) return resolved;
  if (!existsSync(resolved.abs_path)) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_MISSING", path: resolved.abs_path };
  }
  let grant;
  try {
    grant = JSON.parse(readFileSync(resolved.abs_path, "utf8"));
  } catch {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_CORRUPT" };
  }
  const schemaGrant = validateOwnerExecutionGrantDocument(grant);
  if (!schemaGrant.ok) return schemaGrant;
  if (grant.schema !== OWNER_GRANT_SCHEMA || grant.schema_version !== 1) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_SCHEMA_INVALID" };
  }
  const expectedSig = signOwnerGrant(grant, secret);
  const got = String(grant.signature_hmac_sha256 || "");
  if (got.length !== expectedSig.length) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_SIGNATURE_INVALID" };
  }
  if (!timingSafeEqual(Buffer.from(got, "utf8"), Buffer.from(expectedSig, "utf8"))) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_SIGNATURE_INVALID" };
  }
  if (attestation) {
    if (grant.bound_change_root !== attestation.bound_change_root) {
      return { ok: false, code: "OWNER_GRANT_CHANGE_ROOT_MISMATCH" };
    }
    if (grant.bound_change_id !== attestation.bound_change_id) {
      return { ok: false, code: "OWNER_GRANT_CHANGE_ID_MISMATCH" };
    }
    if (grant.bound_planning_digest !== attestation.bound_planning_digest) {
      return { ok: false, code: "OWNER_GRANT_PLANNING_DIGEST_MISMATCH" };
    }
  }
  if (executionIdentity?.planning_digest && grant.bound_planning_digest !== executionIdentity.planning_digest) {
    return { ok: false, code: "OWNER_GRANT_EXECUTION_IDENTITY_STALE" };
  }
  if (modelRouteSha256 && grant.bound_model_route_sha256 && grant.bound_model_route_sha256 !== modelRouteSha256) {
    return { ok: false, code: "OWNER_GRANT_MODEL_ROUTE_MISMATCH" };
  }
  const expiresAt = Date.parse(grant.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) {
    return { ok: false, code: "OWNER_EXECUTION_GRANT_EXPIRED" };
  }
  return { ok: true, grant_id: grant.grant_id, expires_at: grant.expires_at };
}
