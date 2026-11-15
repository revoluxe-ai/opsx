import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Disposable isolated workspace for Super Implement agent hop V0.
 *
 * Agent cwd is the isolated root; canonical TARGET is not writable by the agent.
 * Promotion copies only stage-allowed paths after independent diff audit.
 */
import { createHash } from "node:crypto";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { posixPath } from "./opsx-apply-pipeline-identity.mjs";
import { STAGE_DUAL_WRITE_SURFACES } from "./opsx-super-implement-dual-write-policy.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));

function copyDirRecursive(src, dest) {
  if (!existsSync(src)) return;
  mkdirSync(dest, { recursive: true });
  for (const name of readdirSync(src)) {
    const s = join(src, name);
    const d = join(dest, name);
    if (statSync(s).isDirectory()) copyDirRecursive(s, d);
    else cpSync(s, d);
  }
}

/** Immutable toolchain snapshot roots (copied, never symlinked to canonical TARGET). */
export const TOOLCHAIN_SNAPSHOT_DIRS = Object.freeze([
  { src: "opsx_tools", dest: "opsx_tools" },
  { src: "tools/cursor_executor", dest: "tools/cursor_executor" },
]);

/**
 * Create isolated workspace with copied toolchain and writable product/change trees.
 */
export function createIsolatedAgentWorkspace({
  repoRoot,
  changeRoot,
  stage,
  isolatedParent = null,
} = {}) {
  const repo = resolve(repoRoot);
  const cr = posixPath(changeRoot);
  const hopId = createHash("sha256")
    .update(`${cr}:${stage}:${Date.now()}:${Math.random()}`, "utf8")
    .digest("hex")
    .slice(0, 16);
  const parent = isolatedParent || resourcePath(repo, "opsx", "tmp", "agent-hop-workspaces");
  const isolatedRoot = join(parent, hopId);
  mkdirSync(isolatedRoot, { recursive: true });

  mkdirSync(join(isolatedRoot, "opsx", "tmp"), { recursive: true });
  for (const { src, dest } of TOOLCHAIN_SNAPSHOT_DIRS) {
    const from = resourcePath(repo, src);
    const to = join(isolatedRoot, dest);
    if (existsSync(from)) copyDirRecursive(from, to);
  }

  const changeAbs = join(isolatedRoot, cr);
  const srcChange = resourcePath(repo, cr);
  if (existsSync(srcChange)) {
    copyDirRecursive(srcChange, changeAbs);
  } else {
    mkdirSync(changeAbs, { recursive: true });
  }

  const surfaces = STAGE_DUAL_WRITE_SURFACES[stage];
  if (surfaces?.repo_root?.includes("app/**") && existsSync(resourcePath(repo, "app"))) {
    copyDirRecursive(resourcePath(repo, "app"), join(isolatedRoot, "app"));
  }
  const needsFrozenTestsSnapshot =
    surfaces?.repo_root?.includes("tests/**") ||
    stage === "APPLY_IMPLEMENT" ||
    stage === "APPLY_GREEN";
  if (needsFrozenTestsSnapshot && existsSync(resourcePath(repo, "tests"))) {
    copyDirRecursive(resourcePath(repo, "tests"), join(isolatedRoot, "tests"));
    if (existsSync(resourcePath(repo, "app"))) {
      copyDirRecursive(resourcePath(repo, "app"), join(isolatedRoot, "app"));
    }
  }

  const manifestPath = join(isolatedRoot, "opsx-agent-hop-manifest.json");
  const manifest = {
    schema: "opsx.super_implement_agent_hop_workspace",
    schema_version: 1,
    isolated_root: isolatedRoot,
    canonical_repo_root: repo,
    change_root: cr,
    stage,
    toolchain_snapshot: TOOLCHAIN_SNAPSHOT_DIRS.map((d) => d.dest),
    canonical_symlinks_forbidden: true,
    created_at: new Date().toISOString(),
  };
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  return { isolatedRoot, manifest, manifestPath, hop_id: hopId };
}

/**
 * Content snapshot (path → sha256) for promotion diff.
 */
export function snapshotWorkspaceContent(isolatedRoot, { changeRoot } = {}) {
  const paths = snapshotWorkspaceFiles(isolatedRoot, { changeRoot });
  const map = new Map();
  for (const rel of paths) {
    const content = readFileFromIsolated(isolatedRoot, rel);
    if (content === null) continue;
    map.set(rel, createHash("sha256").update(content, "utf8").digest("hex"));
  }
  return map;
}

export function snapshotWorkspaceFiles(isolatedRoot, { changeRoot } = {}) {
  const root = resolve(isolatedRoot);
  const cr = changeRoot ? posixPath(changeRoot) : null;
  const rootsToWalk = [];
  if (cr && existsSync(resourcePath(root, cr))) rootsToWalk.push(cr);
  if (existsSync(resourcePath(root, "app"))) rootsToWalk.push("app");
  if (existsSync(resourcePath(root, "tests"))) rootsToWalk.push("tests");
  const files = [];
  function walk(abs, rel) {
    let st;
    try {
      st = lstatSync(abs);
    } catch {
      return;
    }
    if (st.isSymbolicLink()) return;
    if (st.isDirectory()) {
      for (const name of readdirSync(abs)) {
        if (rel === "" && name === "opsx-agent-hop-manifest.json") continue;
        walk(join(abs, name), rel ? `${rel}/${name}` : name);
      }
      return;
    }
    files.push(rel.replace(/\\/g, "/"));
  }
  for (const relRoot of rootsToWalk) {
    walk(resourcePath(root, relRoot), relRoot);
  }
  return new Set(files);
}

export function readFileFromIsolated(isolatedRoot, relPath) {
  const abs = join(isolatedRoot, relPath);
  if (!existsSync(abs)) return null;
  return readFileSync(abs, "utf8");
}
