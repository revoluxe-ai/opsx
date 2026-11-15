/**
 * Stage-scoped filesystem write enforcement for Super Implement native dispatch.
 *
 * Activated via OPSX_STAGE_WRITE_GUARD_JSON in child processes (see hook module).
 * Paths are evaluated relative to repo root; only permitted globs under change_root apply.
 */
import { posixPath } from "./opsx-apply-pipeline-identity.mjs";

function loadGuardConfig() {
  const raw = process.env.OPSX_STAGE_WRITE_GUARD_JSON;
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return { corrupt: true };
  }
}

/**
 * Convert a simple glob (with **) to RegExp against posix relative paths.
 */
export function globToRegExp(glob) {
  const g = String(glob).replace(/\\/g, "/");
  let re = "^";
  for (let i = 0; i < g.length; i += 1) {
    const ch = g[i];
    if (ch === "*") {
      if (g[i + 1] === "*") {
        re += ".*";
        i += 1;
        if (g[i + 1] === "/") i += 1;
      } else {
        re += "[^/]*";
      }
      continue;
    }
    if ("\\.[]{}()+^$|?".includes(ch)) {
      re += `\\${ch}`;
    } else {
      re += ch;
    }
  }
  re += "$";
  return new RegExp(re);
}

export function pathAllowedByGuard(absPath, guard) {
  if (!guard || guard.corrupt) {
    return { ok: false, code: "STAGE_WRITE_GUARD_MISSING" };
  }
  const repoRoot = posixPath(guard.repo_root || "").replace(/\/$/, "");
  const changeRoot = posixPath(guard.change_root || "").replace(/\/$/, "");
  const norm = posixPath(absPath);
  const relRepo = norm.startsWith(`${repoRoot}/`)
    ? norm.slice(repoRoot.length + 1)
    : norm;
  const prefix = `${changeRoot}/`;
  if (!relRepo.startsWith(prefix) && relRepo !== changeRoot) {
    return { ok: false, code: "STAGE_WRITE_OUTSIDE_CHANGE_ROOT", path: relRepo };
  }
  const relChange = relRepo === changeRoot ? "" : relRepo.slice(prefix.length);
  const globs = guard.permitted_globs || [];
  for (const glob of globs) {
    const fullGlob = `${changeRoot}/${glob}`.replace(/\/+/g, "/");
    if (globToRegExp(fullGlob).test(relRepo)) {
      return { ok: true, matched_glob: glob };
    }
    if (!relChange && glob.endsWith("/**") && globToRegExp(`${changeRoot}/${glob}`).test(`${relRepo}/`)) {
      return { ok: true, matched_glob: glob };
    }
  }
  return { ok: false, code: "STAGE_WRITE_FORBIDDEN", path: relRepo, permitted_globs: globs };
}

/**
 * Fail-closed guard for a filesystem mutation target (absolute or repo-relative).
 */
export function assertStageWriteAllowed(targetPath) {
  const guard = loadGuardConfig();
  if (!guard) {
    const err = new Error("STAGE_WRITE_GUARD_MISSING");
    err.code = "STAGE_WRITE_GUARD_MISSING";
    throw err;
  }
  const repoRoot = posixPath(guard.repo_root || "");
  const abs = targetPath.startsWith("/") ? posixPath(targetPath) : posixPath(`${repoRoot}/${targetPath}`);
  const verdict = pathAllowedByGuard(abs, guard);
  if (!verdict.ok) {
    const err = new Error(verdict.code);
    err.code = verdict.code;
    err.detail = verdict;
    throw err;
  }
  return verdict;
}

export function stageWriteGuardActive() {
  return Boolean(process.env.OPSX_STAGE_WRITE_GUARD_JSON);
}
