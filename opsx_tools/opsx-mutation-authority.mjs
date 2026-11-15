/**
 * Central OPSX mutation authority (Node): delegates to Python registry.
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const __filename = fileURLToPath(import.meta.url);

/**
 * @returns {{ ok: boolean, payload?: object, reason?: string }}
 */
export function enforceMutationAuthority(repoRoot = DEFAULT_ROOT, entrypointId) {
  if (!entrypointId || typeof entrypointId !== "string") {
    return {
      ok: false,
      reason: "MUTATION_ENTRYPOINT_UNREGISTERED",
      payload: { STATUS: "FAIL_CLOSED", REASON: "MUTATION_ENTRYPOINT_UNREGISTERED" },
    };
  }
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const workspaceRoot = process.env.OPSX_WORKSPACE_ROOT || repoRoot;
  const env = { ...process.env };
  if (!env.PYTHONPATH) {
    env.PYTHONPATH = workspaceRoot;
  } else if (!env.PYTHONPATH.split(":").includes(workspaceRoot)) {
    env.PYTHONPATH = `${workspaceRoot}:${env.PYTHONPATH}`;
  }
  const run = spawnSync(
    python,
    [
      "-m",
      "tools.openspec.opsx_mutation_authority",
      "--enforce",
      "--entrypoint-id",
      entrypointId,
      "--root",
      repoRoot,
    ],
    { cwd: workspaceRoot, encoding: "utf8", env },
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
      payload: {
        STATUS: "FAIL_CLOSED",
        REASON: "OPSX_ADOPTION_REQUIRED",
        DETAIL: run.stderr || "mutation authority check failed",
      },
    };
  }
}

export function mutationAuthorityBlockedResponse(result) {
  return {
    ok: false,
    code: result.reason || "OPSX_ADOPTION_REQUIRED",
    recovery_contract: {
      code: result.reason || "OPSX_ADOPTION_REQUIRED",
      next_action: result.payload?.NEXT_ACTION || "/opsx-adopt",
      detail: result.payload?.DETAIL || "",
      entrypoint_id: result.payload?.ENTRYPOINT_ID,
    },
  };
}

if (process.argv[1] && resolve(process.argv[1]) === __filename) {
  const entrypointId = process.argv[2];
  let repoRoot = DEFAULT_ROOT;
  const rootFlag = process.argv.indexOf("--root");
  if (rootFlag >= 0 && process.argv[rootFlag + 1]) {
    repoRoot = resolve(process.argv[rootFlag + 1]);
  }
  const result = enforceMutationAuthority(repoRoot, entrypointId);
  console.log(JSON.stringify(result.payload ?? {}, null, 2));
  process.exit(result.ok ? 0 : 1);
}
