/**
 * REQUIRED shared matcher for bug-learning V0 and APPLY consumer hooks.
 * Deterministic path / symbol / concept matching and equivalent-knowledge merge.
 */
import { matchGlob, normalizeRelPath } from "./opsx-process-repair-authority.mjs";
import { canonicalContentHash, rawBytesSha256 } from "./opsx-bug-learning-hash.mjs";

export const TOOL_VERSION = "0.1.0";

/**
 * Normalize repo-relative path (forward slashes, no trailing slash except ".").
 * @param {string} p
 * @returns {string}
 */
export function normalizePath(p) {
  let n = normalizeRelPath(String(p || ""));
  if (n.length > 1 && n.endsWith("/")) {
    n = n.slice(0, -1);
  }
  return n || ".";
}

/**
 * Unicode casefold for concept matching (simple toLowerCase for ASCII-heavy tokens).
 * @param {string} s
 * @returns {string}
 */
export function casefold(s) {
  return String(s || "").trim().toLowerCase();
}

/**
 * Normalize responsibility slug for equivalent-knowledge comparison.
 * @param {string} s
 * @returns {string}
 */
export function normalizeResponsibilitySlug(s) {
  return casefold(s).replace(/\s+/g, " ");
}

/**
 * @param {string[]} arr
 * @returns {string[]}
 */
function sortedUnique(arr) {
  return [...new Set((arr || []).map((x) => String(x).trim()).filter(Boolean))].sort();
}

/**
 * @param {{ paths?: string[], symbols?: string[], concepts?: string[] }} matchers
 * @returns {boolean}
 */
export function matchersNonempty(matchers) {
  if (!matchers) return false;
  return (
    (matchers.paths || []).length > 0 ||
    (matchers.symbols || []).length > 0 ||
    (matchers.concepts || []).length > 0
  );
}

/**
 * Path dimension hit (exact or glob).
 * @param {string} pathPattern
 * @param {string} surface
 * @returns {boolean}
 */
export function pathMatches(pathPattern, surface) {
  const pat = normalizePath(pathPattern);
  const surf = normalizePath(surface);
  if (pat.includes("*")) {
    return matchGlob(pat, surf);
  }
  return pat === surf;
}

/**
 * A CURRENT seam matches query if ANY dimension hits (OR across paths/symbols/concepts).
 * @param {{ paths?: string[], symbols?: string[], concepts?: string[] }} query
 * @param {{ matchers?: { paths?: string[], symbols?: string[], concepts?: string[] }, status?: string }} seam
 * @returns {boolean}
 */
export function matchQuery(query, seam) {
  if (!seam || seam.status === "STALE" || seam.status === "SUPERSEDED") {
    return false;
  }
  const m = seam.matchers || {};
  const qPaths = query.paths || [];
  const qSymbols = query.symbols || [];
  const qConcepts = (query.concepts || []).map(casefold);

  for (const qp of qPaths) {
    for (const sp of m.paths || []) {
      if (pathMatches(sp, qp)) return true;
    }
  }
  for (const qs of qSymbols) {
    const qt = String(qs).trim();
    for (const ss of m.symbols || []) {
      if (String(ss).trim() === qt) return true;
    }
  }
  for (const qc of qConcepts) {
    for (const sc of m.concepts || []) {
      if (casefold(sc) === qc) return true;
    }
  }
  return false;
}

/**
 * Match seams from index records against a set of surface paths/symbols/concepts.
 * @param {{ paths?: string[], symbols?: string[], concepts?: string[] }} query
 * @param {Array<{ seam_id: string, status: string, content_sha256: string, matchers: object, responsibility?: string, regression_obligations?: object[] }>} records
 * @returns {Array<object>}
 */
export function matchSeamsForQuery(query, records) {
  const hits = [];
  for (const rec of records || []) {
    if (rec.status !== "CURRENT") continue;
    const seam = {
      id: rec.seam_id,
      status: rec.status,
      content_sha256: rec.content_sha256,
      matchers: rec.matchers,
      responsibility: rec.responsibility,
      regression_obligations: rec.regression_obligations,
    };
    if (matchQuery(query, seam)) {
      hits.push({ ...rec, id: rec.seam_id });
    }
  }
  return hits;
}

/**
 * Equivalent CURRENT knowledge: same responsibility slug + sorted unique matcher dims.
 * @param {object} candidate
 * @param {object[]} currentSeams
 * @returns {object|null}
 */
export function findEquivalentCurrent(candidate, currentSeams) {
  const resp = normalizeResponsibilitySlug(candidate.responsibility);
  const cPaths = sortedUnique(candidate.matchers?.paths);
  const cSymbols = sortedUnique(candidate.matchers?.symbols);
  const cConcepts = sortedUnique((candidate.matchers?.concepts || []).map(casefold));

  const equivalents = (currentSeams || []).filter((s) => {
    if (s.status !== "CURRENT") return false;
    if (normalizeResponsibilitySlug(s.responsibility) !== resp) return false;
    const sPaths = sortedUnique(s.matchers?.paths);
    const sSymbols = sortedUnique(s.matchers?.symbols);
    const sConcepts = sortedUnique((s.matchers?.concepts || []).map(casefold));
    return (
      JSON.stringify(sPaths) === JSON.stringify(cPaths) &&
      JSON.stringify(sSymbols) === JSON.stringify(cSymbols) &&
      JSON.stringify(sConcepts) === JSON.stringify(cConcepts)
    );
  });

  if (equivalents.length === 0) return null;
  equivalents.sort((a, b) => String(a.id).localeCompare(String(b.id)));
  return equivalents[0];
}

/**
 * Parse JSONL into seam objects (latest line per id wins for status).
 * @param {string} jsonlText
 * @returns {object[]}
 */
export function parseSeamsJsonl(jsonlText) {
  const byId = new Map();
  for (const line of String(jsonlText || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const obj = JSON.parse(trimmed);
    byId.set(obj.id, obj);
  }
  return [...byId.values()];
}

/**
 * Compute content_sha256 for a seam line object.
 * @param {object} seam
 * @returns {string}
 */
export function seamContentHash(seam) {
  return canonicalContentHash(seam, "content_sha256");
}

/**
 * Rebuild CURRENT-only index projection from behavioral-seams.jsonl raw bytes.
 * @param {string|Buffer} jsonlBytes
 * @param {string} [toolVersion]
 * @returns {object}
 */
export function rebuildIndex(jsonlBytes, toolVersion = TOOL_VERSION) {
  const text = Buffer.isBuffer(jsonlBytes) ? jsonlBytes.toString("utf8") : String(jsonlBytes || "");
  const seams = parseSeamsJsonl(text);
  const records = [];
  for (const seam of seams) {
    if (seam.status !== "CURRENT") continue;
    if (!matchersNonempty(seam.matchers)) {
      throw new Error(`Seam ${seam.id} lacks nonempty matcher dimension`);
    }
    records.push({
      seam_id: seam.id,
      status: "CURRENT",
      content_sha256: seam.content_sha256 || seamContentHash(seam),
      matchers: {
        paths: seam.matchers?.paths || [],
        symbols: seam.matchers?.symbols || [],
        concepts: seam.matchers?.concepts || [],
      },
      responsibility: seam.responsibility,
      regression_obligations: seam.regression_obligations || [],
    });
  }
  records.sort((a, b) => a.seam_id.localeCompare(b.seam_id));
  return {
    schema_version: 1,
    rebuilt_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    rebuilt_from_sha256: rawBytesSha256(Buffer.isBuffer(jsonlBytes) ? jsonlBytes : Buffer.from(text, "utf8")),
    tool_version: toolVersion,
    records,
  };
}
