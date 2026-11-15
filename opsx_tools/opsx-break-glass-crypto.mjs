import { createHash, createHmac } from "node:crypto";

export const GRANT_SCHEMA = "opsx.break_glass_owner_grant";

export function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((e) => stableStringify(e)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(",")}}`;
}

export function sha256Hex(input) {
  return createHash("sha256").update(input, "utf8").digest("hex");
}

export function computeGrantSignatureBody(grant) {
  const body = { ...grant };
  delete body.signature_hmac_sha256;
  return stableStringify(body);
}

export function signBreakGlassOwnerGrant(grant, secret) {
  const payload = computeGrantSignatureBody(grant);
  return createHmac("sha256", secret).update(payload, "utf8").digest("hex");
}
