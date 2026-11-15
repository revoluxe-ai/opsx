import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Trusted grant store + Owner issuance boundary (P0-2).
 */
import { timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { executionIdentityDigest } from "./opsx-break-glass-execution-identity.mjs";
import { signBreakGlassOwnerGrant } from "./opsx-break-glass-crypto.mjs";

export const ISSUER_HMAC_ENV = "OPSX_BREAK_GLASS_ISSUER_HMAC_SECRET";
export const VERIFY_HMAC_ENV = "OPSX_BREAK_GLASS_VERIFY_HMAC_SECRET";
export const TRUSTED_GRANTS_REL = "opsx/break-glass/trusted-grants";
export const OWNER_ATTESTATIONS_REL = "opsx/break-glass/owner-attestations";

export function trustedGrantsDir(repoRoot) {
  return resourcePath(repoRoot, TRUSTED_GRANTS_REL);
}

export function ownerAttestationPath(repoRoot, attestationRef) {
  const id = String(attestationRef || "").replace(/^owner-message:/, "");
  return resourcePath(repoRoot, OWNER_ATTESTATIONS_REL, `${id}.json`);
}

export function executorSelfSigningForbidden() {
  if (process.env[ISSUER_HMAC_ENV]) {
    return { ok: false, code: "DENIED", reason: "EXECUTOR_SELF_SIGNING_FORBIDDEN" };
  }
  return { ok: true };
}

export function resolveVerifySecret() {
  const verify = process.env[VERIFY_HMAC_ENV] || process.env.OPSX_BREAK_GLASS_OWNER_HMAC_SECRET;
  if (!verify || verify.length < 16) {
    return { ok: false, code: "OWNER_AUTHORITY_UNBOUND", reason: "VERIFY_HMAC_SECRET_MISSING" };
  }
  return { ok: true, secret: verify };
}

export function loadTrustedBreakGlassGrant(repoRoot, grantId) {
  const selfSign = executorSelfSigningForbidden();
  if (!selfSign.ok) return selfSign;
  const path = join(trustedGrantsDir(repoRoot), `${grantId}.json`);
  if (!existsSync(path)) {
    return { ok: false, code: "DENIED", reason: "TRUSTED_GRANT_NOT_IN_STORE" };
  }
  try {
    const grant = JSON.parse(readFileSync(path, "utf8"));
    if (grant.grant_provenance !== "owner_trusted_store") {
      return { ok: false, code: "DENIED", reason: "GRANT_PROVENANCE_INVALID" };
    }
    return { ok: true, grant, store_path: path, grant_source: "trusted_store" };
  } catch {
    return { ok: false, code: "DENIED", reason: "TRUSTED_GRANT_CORRUPT" };
  }
}

export function validateOwnerAttestationRecord(repoRoot, grant) {
  const ref = String(grant.owner_attestation_ref || "");
  if (!ref.startsWith("owner-message:")) {
    return { ok: false, code: "DENIED", reason: "ATTESTATION_REF_NOT_OWNER_HELD" };
  }
  const path = ownerAttestationPath(repoRoot, ref);
  if (!existsSync(path)) {
    return { ok: false, code: "DENIED", reason: "OWNER_ATTESTATION_RECORD_MISSING" };
  }
  try {
    const doc = JSON.parse(readFileSync(path, "utf8"));
    if (doc.owner_attestation_ref !== ref) {
      return { ok: false, code: "DENIED", reason: "SPOOFED_OWNER_ATTESTATION" };
    }
    if (doc.bound_grant_id && doc.bound_grant_id !== grant.grant_id) {
      return { ok: false, code: "DENIED", reason: "ATTESTATION_GRANT_MISMATCH" };
    }
    return { ok: true, attestation: doc };
  } catch {
    return { ok: false, code: "DENIED", reason: "OWNER_ATTESTATION_CORRUPT" };
  }
}

export function verifyGrantSignature(grant, secret) {
  const expected = signBreakGlassOwnerGrant(grant, secret);
  const got = String(grant.signature_hmac_sha256 || "");
  if (got.length !== expected.length) {
    return { ok: false, code: "DENIED", reason: "GRANT_SIGNATURE_INVALID" };
  }
  if (!timingSafeEqual(Buffer.from(got, "utf8"), Buffer.from(expected, "utf8"))) {
    return { ok: false, code: "DENIED", reason: "GRANT_SIGNATURE_INVALID" };
  }
  return { ok: true };
}

/**
 * Owner-side issuance only (hermetic test issuer / Owner CLI).
 */
export function issueGrantToTrustedStore(repoRoot, grant, issuerSecret) {
  if (!issuerSecret || issuerSecret.length < 16) {
    return { ok: false, code: "OWNER_AUTHORITY_UNBOUND", reason: "ISSUER_SECRET_MISSING" };
  }
  const body = {
    ...grant,
    grant_provenance: "owner_trusted_store",
    issuer_role: "owner_break_glass_issuer",
  };
  if (!body.bound_execution_identity_digest && body.repository_identity) {
    body.bound_execution_identity_digest = executionIdentityDigest({
      repo: body.repository_identity.repo,
      branch: body.repository_identity.branch,
      revision: body.repository_identity.revision,
      change_id: body.change_id,
      execution_id: body.execution_id,
      blocked_gate: body.blocked_gate,
      failure_code: body.original_failure_code,
    });
  }
  const signed = {
    ...body,
    signature_hmac_sha256: signBreakGlassOwnerGrant(body, issuerSecret),
  };
  const dir = trustedGrantsDir(repoRoot);
  mkdirSync(dir, { recursive: true });
  const out = join(dir, `${signed.grant_id}.json`);
  writeFileSync(out, `${JSON.stringify(signed, null, 2)}\n`, "utf8");
  return { ok: true, path: out, grant: signed };
}
