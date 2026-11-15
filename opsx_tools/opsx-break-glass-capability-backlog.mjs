import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Machine-readable Break Glass capability backlog (separate from incident ledger).
 *
 * Tracks deferred OPSX capability gaps such as OPSX-BG-001; not a runtime
 * incident ledger and not used for live activation.
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_DIR = resolve(fileURLToPath(import.meta.url), "..");
export const DEFAULT_CAPABILITY_BACKLOG_REL = "opsx/break-glass/capability-backlog.ndjson";

export const BG_001_NATIVE_REVALIDATION_ID = "OPSX-BG-001";

/**
 * @param {string} repoRoot
 * @param {string} [relPath]
 */
export function capabilityBacklogPath(repoRoot, relPath = DEFAULT_CAPABILITY_BACKLOG_REL) {
  return resourcePath(repoRoot, relPath);
}

/**
 * @param {string} repoRoot
 * @returns {object[]}
 */
export function readCapabilityBacklog(repoRoot) {
  const path = capabilityBacklogPath(repoRoot);
  if (!existsSync(path)) return [];
  const lines = readFileSync(path, "utf8").split("\n").filter((l) => l.trim());
  const rows = [];
  for (const line of lines) {
    try {
      rows.push(JSON.parse(line));
    } catch {
      /* skip corrupt line */
    }
  }
  return rows;
}

/**
 * @param {string} repoRoot
 * @param {string} entryId
 */
export function findCapabilityBacklogEntry(repoRoot, entryId) {
  return readCapabilityBacklog(repoRoot).find((e) => e.id === entryId) || null;
}

/**
 * V1: native revalidation closure is deferred while OPSX-BG-001 remains OPEN.
 */
export function nativeGateRevalidationDeferredForV1(repoRoot) {
  const entry = findCapabilityBacklogEntry(repoRoot, BG_001_NATIVE_REVALIDATION_ID);
  if (!entry) {
    return { deferred: false, reason: "OPSX_BG_001_NOT_REGISTERED" };
  }
  if (entry.status !== "OPEN") {
    return { deferred: false, entry };
  }
  return {
    deferred: true,
    entry,
    code: "NATIVE_GATE_REVALIDATION_DEFERRED",
    reason: "OPSX_BG_001_DEFERRED_OUT_OF_SCOPE",
  };
}
