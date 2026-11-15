import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Idempotent integration ledger for agent hop V0 → canonical TARGET.
 *
 * Prevents duplicate promotion when the same execution_id is replayed after crash.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const LEDGER_SCHEMA = "opsx.super_implement_agent_hop_integrations";

function ledgerPath(repoRoot, changeRoot) {
  return resourcePath(repoRoot, changeRoot, "opsx", "agent-hop-integrations.json");
}

export function loadIntegrationLedger(repoRoot, changeRoot) {
  const path = ledgerPath(repoRoot, changeRoot);
  if (!existsSync(path)) {
    return { schema: LEDGER_SCHEMA, schema_version: 1, entries: [] };
  }
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return { schema: LEDGER_SCHEMA, schema_version: 1, entries: [], corrupt: true };
  }
}

export function findIntegratedEntry(repoRoot, changeRoot, executionId) {
  const ledger = loadIntegrationLedger(repoRoot, changeRoot);
  return ledger.entries?.find((e) => e.execution_id === executionId) || null;
}

/**
 * Fail-closed duplicate guard before promotion.
 */
export function assertAgentHopNotYetIntegrated(repoRoot, changeRoot, executionId) {
  const existing = findIntegratedEntry(repoRoot, changeRoot, executionId);
  if (existing) {
    return {
      ok: false,
      code: "AGENT_HOP_DUPLICATE_INTEGRATION",
      prior: existing,
      recovery_contract: {
        code: "AGENT_HOP_DUPLICATE_INTEGRATION",
        next_action: "resume from ledger evidence; do not re-promote",
        resume_requires: ["fresh_execution_id"],
      },
    };
  }
  return { ok: true };
}

export function recordAgentHopIntegration(repoRoot, changeRoot, entry) {
  const path = ledgerPath(repoRoot, changeRoot);
  const ledger = loadIntegrationLedger(repoRoot, changeRoot);
  ledger.entries = ledger.entries || [];
  ledger.entries.push({
    ...entry,
    integrated_at: new Date().toISOString(),
  });
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(ledger, null, 2)}\n`);
  return ledger;
}
