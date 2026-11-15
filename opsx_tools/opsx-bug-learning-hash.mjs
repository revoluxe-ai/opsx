/**
 * Canonical JSON hashing for OPSX bug-learning V0.
 * Mirrors opsx-process-repair-authority.mjs (sortKeysDeep + UTF-8 SHA-256).
 */
import { createHash } from "node:crypto";

/**
 * Recursively sort object keys for stable JSON serialization.
 * @param {unknown} value
 * @returns {unknown}
 */
export function sortKeysDeep(value) {
  if (Array.isArray(value)) {
    return value.map(sortKeysDeep);
  }
  if (value !== null && typeof value === "object") {
    const sorted = {};
    for (const key of Object.keys(value).sort()) {
      sorted[key] = sortKeysDeep(value[key]);
    }
    return sorted;
  }
  return value;
}

/**
 * Canonical content hash: omit one field, sortKeysDeep, JSON.stringify UTF-8, SHA-256 hex.
 * @param {object} obj
 * @param {string} omitField
 * @returns {string}
 */
export function canonicalContentHash(obj, omitField) {
  const { [omitField]: _omit, ...rest } = obj;
  const payload = JSON.stringify(sortKeysDeep(rest));
  return createHash("sha256").update(payload, "utf8").digest("hex");
}

/**
 * Raw file-byte SHA-256 (consumer artifact_sha256, index rebuilt_from_sha256).
 * @param {string|Buffer} content
 * @returns {string}
 */
export function rawBytesSha256(content) {
  return createHash("sha256").update(content).digest("hex");
}
