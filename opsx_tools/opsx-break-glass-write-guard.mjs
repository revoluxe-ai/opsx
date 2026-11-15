/**
 * Enforced bounded write paths for Break Glass continuation.
 */
import { existsSync, lstatSync, mkdirSync, realpathSync, writeFileSync, openSync, closeSync } from "node:fs";
import { dirname, isAbsolute, normalize, resolve, sep, join } from "node:path";

function posix(p) {
  return String(p || "").replace(/\\/g, "/");
}

function underRoot(candidate, rootReal) {
  const c = posix(candidate);
  const r = posix(rootReal);
  return c === r || c.startsWith(`${r}/`);
}

/**
 * Walk ancestors; reject symlink escape even when leaf is missing.
 */
export function assertRepoPathSafeForWrite(repoRoot, relPath) {
  const root = resolve(repoRoot);
  if (!existsSync(root)) {
    return { ok: false, code: "DENIED", reason: "REPO_ROOT_MISSING" };
  }
  const rootReal = realpathSync(root);
  const raw = String(relPath || "");
  if (!raw || isAbsolute(raw)) {
    return { ok: false, code: "DENIED", reason: "ABSOLUTE_PATH_FORBIDDEN" };
  }
  const normalized = normalize(raw.replace(/\\/g, "/"));
  if (normalized.startsWith("..") || normalized.includes("/../")) {
    return { ok: false, code: "DENIED", reason: "PATH_TRAVERSAL" };
  }
  const segments = normalized.split("/").filter(Boolean);
  let current = rootReal;
  for (let i = 0; i < segments.length; i += 1) {
    const segment = segments[i];
    const next = join(current, segment);
    if (existsSync(next)) {
      const lst = lstatSync(next);
      if (lst.isSymbolicLink()) {
        const target = realpathSync(next);
        if (!underRoot(target, rootReal)) {
          return { ok: false, code: "DENIED", reason: "SYMLINK_ANCESTOR_ESCAPE" };
        }
        current = target;
        continue;
      }
      current = realpathSync(next);
      if (!underRoot(current, rootReal)) {
        return { ok: false, code: "DENIED", reason: "SYMLINK_ESCAPE" };
      }
    } else {
      if (!underRoot(current, rootReal)) {
        return { ok: false, code: "DENIED", reason: "WRITE_PARENT_OUTSIDE_REPO" };
      }
      const rest = segments.slice(i);
      const abs = join(current, ...rest);
      return { ok: true, rel_path: posix(join(...segments)), abs_path: abs };
    }
  }
  return { ok: true, rel_path: posix(join(...segments)), abs_path: current };
}

export function canonicalRepoRelativePath(repoRoot, targetPath) {
  return assertRepoPathSafeForWrite(repoRoot, targetPath);
}

function prefixMatches(relPath, prefix) {
  const p = posix(prefix).replace(/\/$/, "");
  const t = posix(relPath);
  if (t === p) return true;
  return t.startsWith(`${p}/`);
}

export function writeSetAllows(grant, targetPath, repoRoot) {
  const canon = assertRepoPathSafeForWrite(repoRoot, targetPath);
  if (!canon.ok) return false;
  return (grant.bounded_write_set || []).some((prefix) => prefixMatches(canon.rel_path, prefix));
}

export function assertWriteAllowed({ grant, repoRoot, targetPath }) {
  const canon = assertRepoPathSafeForWrite(repoRoot, targetPath);
  if (!canon.ok) return canon;
  if (!writeSetAllows(grant, canon.rel_path, repoRoot)) {
    return { ok: false, code: "DENIED", reason: "WRITE_OUTSIDE_BOUNDED_SET" };
  }
  return { ok: true, rel_path: canon.rel_path, abs_path: canon.abs_path };
}

export function guardedWriteText({ grant, repoRoot, relPath, content }) {
  const allowed = assertWriteAllowed({ grant, repoRoot, targetPath: relPath });
  if (!allowed.ok) return allowed;
  const parent = dirname(allowed.abs_path);
  mkdirSync(parent, { recursive: true });
  const safeAgain = assertRepoPathSafeForWrite(repoRoot, relPath);
  if (!safeAgain.ok) return safeAgain;
  const fd = openSync(allowed.abs_path, "w");
  try {
    writeFileSync(fd, content, "utf8");
  } finally {
    closeSync(fd);
  }
  const st = lstatSync(allowed.abs_path);
  if (!st.isFile()) {
    return { ok: false, code: "DENIED", reason: "WRITE_NOT_REGULAR_FILE" };
  }
  return { ok: true, path: allowed.rel_path };
}
