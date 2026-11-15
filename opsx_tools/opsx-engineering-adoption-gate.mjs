/**
 * Fail-closed engineering adoption gate for Node mutation tools.
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const __filename = fileURLToPath(import.meta.url);

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const BOOTSTRAP_COMMAND_ALLOWLIST = new Set([
  "opsx-adopt",
  "opsx-projectize",
  "opsx-meta-bootstrap",
  "opsx-execution-preflight",
  "opsx-doctor",
  "opsx-source-resolution",
  "opsx-resolve-conflict",
  "opsx-meta-discover",
  "opsx-meta-consult",
]);

/**
 * @returns {{ ok: boolean, payload?: object, reason?: string }}
 */
export function checkEngineeringAdoptionGate(repoRoot = DEFAULT_ROOT, commandId = null) {
  if (commandId && BOOTSTRAP_COMMAND_ALLOWLIST.has(commandId)) {
    return { ok: true, payload: { STATUS: "OK", reason: "BOOTSTRAP_ALLOWLIST" } };
  }
  const python = process.env.PYTHON || "python3";
  const run = spawnSync(
    python,
    ["-m", "tools.openspec.opsx_engineering_adoption_gate", "--check-mutation", "--root", repoRoot],
    { cwd: repoRoot, encoding: "utf8" },
  );
  const text = (run.stdout || "").trim();
  try {
    const payload = JSON.parse(text || "{}");
    if (run.status === 0) {
      return { ok: true, payload };
    }
    return { ok: false, payload, reason: payload.REASON || "OPSX_ADOPTION_REQUIRED" };
  } catch {
    return {
      ok: false,
      reason: "OPSX_ADOPTION_REQUIRED",
      payload: { STATUS: "FAIL_CLOSED", REASON: "OPSX_ADOPTION_REQUIRED", DETAIL: run.stderr },
    };
  }
}

if (process.argv[1] && resolve(process.argv[1]) === __filename) {
  const result = checkEngineeringAdoptionGate(DEFAULT_ROOT, process.argv[2] || null);
  console.log(JSON.stringify(result.payload ?? { STATUS: "FAIL_CLOSED" }, null, 2));
  process.exit(result.ok ? 0 : 1);
}
