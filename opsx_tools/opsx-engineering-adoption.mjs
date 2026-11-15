/**
 * Node bridge for OPSX project engineering adoption workflow.
 *
 * Usage:
 *   node opsx_tools/opsx-engineering-adoption.mjs <ACTION> [--owner-token TOKEN] [--root PATH]
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const __filename = fileURLToPath(import.meta.url);

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

function parseArgs(argv) {
  const out = { action: null, ownerToken: null, root: DEFAULT_ROOT, quarantineJson: null };
  const rest = [...argv];
  if (rest.length > 0 && !rest[0].startsWith("-")) {
    out.action = rest.shift();
  }
  while (rest.length > 0) {
    const token = rest.shift();
    if (token === "--owner-token" && rest.length > 0) {
      out.ownerToken = rest.shift();
    } else if (token === "--root" && rest.length > 0) {
      out.root = resolve(rest.shift());
    } else if (token === "--quarantine-json" && rest.length > 0) {
      out.quarantineJson = rest.shift();
    }
  }
  return out;
}

export function runEngineeringAdoption(argv, options = {}) {
  const repoRoot = options.repoRoot ?? DEFAULT_ROOT;
  const parsed = parseArgs(argv);
  if (!parsed.action) {
    return { ok: false, code: "USAGE", detail: "ACTION required" };
  }
  const python = process.env.PYTHON || "python3";
  const cmd = [
    python,
    "-m",
    "tools.openspec.opsx_engineering_adoption",
    parsed.action,
    "--root",
    parsed.root ?? repoRoot,
  ];
  if (parsed.ownerToken) {
    cmd.push("--owner-token", parsed.ownerToken);
  }
  if (parsed.quarantineJson) {
    cmd.push("--quarantine-json", parsed.quarantineJson);
  }
  const run = spawnSync(cmd[0], cmd.slice(1), {
    cwd: parsed.root ?? repoRoot,
    encoding: "utf8",
  });
  return {
    ok: run.status === 0,
    code: run.status === 0 ? "OK" : "FAIL_CLOSED",
    stdout: run.stdout ?? "",
    stderr: run.stderr ?? "",
  };
}

if (process.argv[1] && resolve(process.argv[1]) === __filename) {
  const result = runEngineeringAdoption(process.argv.slice(2));
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exit(result.ok ? 0 : 1);
}
