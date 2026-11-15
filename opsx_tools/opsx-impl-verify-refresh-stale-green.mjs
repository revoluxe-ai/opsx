#!/usr/bin/env node
import { resourcePath } from "./opsx-repo-root.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
/**
 * PHASE_IMPL: авто-refresh STALE_GREEN — bounded pytest по frozen RED + обновление GREEN.
 *
 * Usage:
 *   node opsx_tools/opsx-impl-verify-refresh-stale-green.mjs --change-root openspec/changes/<id>
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const repoRoot = defaultRepoRootFromToolFile(import.meta.url);

function parseArgs(argv) {
  const out = { changeRoot: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--change-root" && argv[i + 1]) {
      out.changeRoot = String(argv[i + 1]).replace(/\\/g, "/");
      i += 1;
    }
  }
  return out;
}

function listStaleSlices(changeRootRel) {
  const script = resolve(TOOL_DIR, "bounded-verify-list-stale-green.mjs");
  const proc = spawnSync("node", [script, changeRootRel], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  if (proc.status !== 0 || !proc.stdout?.trim()) {
    return {
      stale: [],
      error: proc.stderr?.trim() || "list_stale_failed",
      exit_code: proc.status,
    };
  }
  try {
    return JSON.parse(proc.stdout.trim());
  } catch {
    return { stale: [], error: "list_stale_invalid_json" };
  }
}

function refreshInvestYaltaSlice(sliceId) {
  const script = resolve(TOOL_DIR, "invest-yalta-closeout-green-refresh.mjs");
  const proc = spawnSync("node", [script, "green", sliceId], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: 600000,
  });
  return {
    slice_id: sliceId,
    ok: proc.status === 0,
    exit_code: proc.status,
    stdout_tail: (proc.stdout || "").slice(-1500),
    stderr_tail: (proc.stderr || "").slice(-1500),
  };
}

function main() {
  const { changeRoot } = parseArgs(process.argv.slice(2));
  if (!changeRoot) {
    process.stdout.write(
      JSON.stringify({ status: "BLOCKED", reason: "missing_change_root" }) + "\n",
    );
    process.exit(1);
  }
  const designPath = resourcePath(repoRoot, changeRoot, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    process.stdout.write(
      JSON.stringify({ status: "SKIP", reason: "not_split_apply", stale: [] }) + "\n",
    );
    process.exit(0);
  }
  const listed = listStaleSlices(changeRoot);
  const stale = listed.stale || [];
  if (!stale.length) {
    process.stdout.write(
      JSON.stringify({
        status: "SKIP",
        reason: "no_stale_slices",
        change_root: changeRoot,
        refreshed: [],
        failed: [],
      }) + "\n",
    );
    process.exit(0);
  }

  const changeId = changeRoot.split("/").pop();
  const refreshed = [];
  const failed = [];
  const skipped = [];

  for (const row of stale) {
    const sliceId = String(row.slice_id || row).trim();
    if (!sliceId) continue;
    if (changeId === "invest-yalta-max-funnel") {
      const result = refreshInvestYaltaSlice(sliceId);
      if (result.ok) refreshed.push(result);
      else failed.push(result);
    } else {
      skipped.push({
        slice_id: sliceId,
        reason: "generic_refresh_not_implemented",
      });
    }
  }

  const status =
    failed.length > 0 ? "PARTIAL" : refreshed.length > 0 ? "PASS" : "SKIP";
  process.stdout.write(
    JSON.stringify(
      {
        status,
        change_root: changeRoot,
        stale_count: stale.length,
        refreshed,
        failed,
        skipped,
        list_error: listed.error || null,
      },
      null,
      2,
    ) + "\n",
  );
  process.exit(failed.length > 0 ? 2 : 0);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
