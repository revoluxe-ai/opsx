/**
 * Каноническое JSON-кодирование для adoption candidate_digest и audit_receipt_sha256.
 * Должно байт-в-байт совпадать с Python ``adoption_authority.candidate_digest`` /
 * ``audit_receipt_sha256`` (sort_keys, separators без пробелов, ensure_ascii=False).
 */

/**
 * @param {unknown} value
 * @returns {string}
 */
export function canonicalJsonStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => canonicalJsonStringify(entry)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${canonicalJsonStringify(value[key])}`)
    .join(",")}}`;
}

/**
 * SHA-256 hex канонического JSON объекта (манифест или audit).
 *
 * @param {Record<string, unknown>} object
 * @param {import("node:crypto").createHash} createHash
 * @returns {string}
 */
export function sha256CanonicalJson(object, createHash) {
  const payload = canonicalJsonStringify(object);
  return createHash("sha256").update(payload, "utf8").digest("hex");
}

/** Версия алгоритма для контрактных ссылок в gap/authority. */
export const ADOPTION_CANDIDATE_DIGEST_ALGORITHM = "canonical_json_utf8_v1";
