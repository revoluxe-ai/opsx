import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Authentic native revalidation — approved verifier only, re-execution required.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync, mkdirSync, renameSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { bindGrantToRuntimeIdentity } from "./opsx-break-glass-execution-identity.mjs";

export const REVALIDATION_RECEIPT_SCHEMA = "opsx.break_glass_revalidation_receipt";
const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
export const APPROVED_REVALIDATION_VERIFIER = join(
  TOOL_DIR,
  "opsx-break-glass-revalidation-verifier.mjs",
);

export function revalidationReceiptPath(repoRoot, incidentId) {
  return resourcePath(repoRoot, "opsx/break-glass/revalidation", `${incidentId}.json`);
}

function sha256Hex(s) {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

export function buildApprovedRevalidationCommand({ incident, runtimeIdentity, repoRoot }) {
  return [
    process.execPath,
    APPROVED_REVALIDATION_VERIFIER,
    "--incident-id",
    incident.incident_id,
    "--execution-id",
    incident.execution_id,
    "--revision",
    runtimeIdentity.revision,
    "--failed-gate",
    incident.failed_gate,
    "--failure-code",
    incident.failure_code,
    "--repo-root",
    repoRoot,
  ];
}

export function executeApprovedRevalidationVerifier({ incident, runtimeIdentity, repoRoot }) {
  const command = buildApprovedRevalidationCommand({ incident, runtimeIdentity, repoRoot });
  const result = spawnSync(command[0], command.slice(1), { encoding: "utf8", cwd: repoRoot });
  const stdout = String(result.stdout || "").trim();
  const lastLine = stdout.split("\n").filter(Boolean).pop() || "";
  const stdout_sha256 = lastLine ? sha256Hex(lastLine.trim()) : null;
  return {
    command,
    exit_code: result.status ?? 1,
    stdout,
    stdout_sha256,
    stderr: result.stderr,
  };
}

export function runBreakGlassRevalidationVerifier({ repoRoot, incident, runtimeIdentity }) {
  if (!runtimeIdentity?.revision || !runtimeIdentity?.execution_id) {
    return { ok: false, code: "DENIED", reason: "RUNTIME_IDENTITY_INCOMPLETE" };
  }
  const bind = bindGrantToRuntimeIdentity(
    {
      repository_identity: { repo: runtimeIdentity.repo, branch: runtimeIdentity.branch, revision: runtimeIdentity.revision },
      change_id: incident.change_id,
      execution_id: incident.execution_id,
      blocked_gate: incident.failed_gate,
      original_failure_code: incident.failure_code,
    },
    runtimeIdentity,
  );
  if (!bind.ok) return bind;

  const run = executeApprovedRevalidationVerifier({ incident, runtimeIdentity, repoRoot });
  const lastLine = run.stdout.split("\n").filter(Boolean).pop();
  let parsed = null;
  try {
    parsed = JSON.parse(lastLine || "{}");
  } catch {
    parsed = null;
  }
  const receipt = {
    schema: REVALIDATION_RECEIPT_SCHEMA,
    schema_version: 1,
    receipt_id: randomUUID(),
    incident_id: incident.incident_id,
    execution_id: incident.execution_id,
    bound_revision: runtimeIdentity.revision,
    failed_gate: incident.failed_gate,
    failure_code: incident.failure_code,
    verifier_entrypoint: APPROVED_REVALIDATION_VERIFIER,
    verifier_command: run.command,
    verifier_exit_code: run.exit_code,
    verifier_stdout_sha256: run.stdout_sha256,
    verifier_output: parsed,
    created_at: new Date().toISOString(),
  };
  const out = revalidationReceiptPath(repoRoot, incident.incident_id);
  mkdirSync(dirname(out), { recursive: true });
  const tmp = `${out}.tmp.${randomUUID()}`;
  writeFileSync(tmp, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  renameSync(tmp, out);
  if (run.exit_code !== 0) {
    return { ok: false, code: "REVALIDATION_REQUIRED", reason: "VERIFIER_DID_NOT_PASS" };
  }
  return { ok: true, receipt, receipt_path: out };
}

/**
 * Re-run approved verifier; forged on-disk JSON without matching live execution fails.
 */
export function validateRevalidationDurableReceipt({
  repoRoot,
  incident,
  runtimeIdentity,
  grant = null,
  receiptPath = null,
} = {}) {
  const path = receiptPath || revalidationReceiptPath(repoRoot, incident.incident_id);
  if (!existsSync(path)) {
    return { ok: false, code: "REVALIDATION_REQUIRED", reason: "DURABLE_RECEIPT_MISSING" };
  }
  let receipt;
  try {
    receipt = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_RECEIPT_CORRUPT" };
  }
  if (receipt.schema !== REVALIDATION_RECEIPT_SCHEMA || receipt.schema_version !== 1) {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_RECEIPT_SCHEMA_INVALID" };
  }
  if (receipt.verifier_entrypoint !== APPROVED_REVALIDATION_VERIFIER) {
    return { ok: false, code: "DENIED", reason: "UNAPPROVED_VERIFIER" };
  }
  if (receipt.incident_id !== incident.incident_id) {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_INCIDENT_MISMATCH" };
  }
  if (receipt.execution_id !== incident.execution_id) {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_EXECUTION_MISMATCH" };
  }
  if (runtimeIdentity?.revision && receipt.bound_revision !== runtimeIdentity.revision) {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_REVISION_STALE" };
  }
  if (receipt.failed_gate !== incident.failed_gate) {
    return { ok: false, code: "DENIED", reason: "REVALIDATION_GATE_MISMATCH" };
  }
  if (grant) {
    const bind = bindGrantToRuntimeIdentity(grant, runtimeIdentity);
    if (!bind.ok) return bind;
  }

  const liveSha = receipt.verifier_stdout_sha256;
  if (!liveSha || liveSha.length !== 64) {
    return { ok: false, code: "DENIED", reason: "FORGED_OR_STALE_REVALIDATION_RECEIPT" };
  }
  const live = executeApprovedRevalidationVerifier({ incident, runtimeIdentity, repoRoot });
  if (live.exit_code !== 0) {
    return { ok: false, code: "REVALIDATION_REQUIRED", reason: "LIVE_VERIFIER_FAILED" };
  }
  const recomputedSha = sha256Hex((live.stdout.split("\n").filter(Boolean).pop() || "").trim());
  if (liveSha !== recomputedSha) {
    return { ok: false, code: "DENIED", reason: "FORGED_OR_STALE_REVALIDATION_RECEIPT" };
  }
  return { ok: true, receipt, receipt_path: path, live_verifier: live };
}
