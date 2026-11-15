/**
 * Dual-root write surfaces: change-local receipts vs repository product/test trees.
 *
 * TARGET contract: production `app/**` and `tests/**` are repo-root relative;
 * process receipts live under openspec/changes/<id>/apply-* trees.
 */
import { posixPath } from "./opsx-apply-pipeline-identity.mjs";

export const STAGE_DUAL_WRITE_SURFACES = Object.freeze({
  APPLY_DESIGN: {
    change_local: ["apply-design/**"],
    repo_root: [],
  },
  APPLY_DESIGN_REVIEW: {
    change_local: ["apply-design-review/**"],
    repo_root: [],
  },
  APPLY_SCAFFOLD: {
    change_local: ["apply-scaffold/**"],
    repo_root: [],
  },
  APPLY_RED: {
    change_local: ["apply-red/**"],
    repo_root: ["tests/**"],
  },
  APPLY_IMPLEMENT: {
    change_local: ["apply-implement/**"],
    repo_root: ["app/**"],
  },
  APPLY_GREEN: {
    change_local: ["apply-green/**"],
    repo_root: ["app/**"],
  },
});

/**
 * Allowed relative paths (repo-root relative) for promotion after an agent hop.
 */
export function allowedPromotionPaths(changeRoot, stage) {
  const spec = STAGE_DUAL_WRITE_SURFACES[stage];
  if (!spec) return { change_local: [], repo_root: [] };
  const cr = posixPath(changeRoot).replace(/\/$/, "");
  const changeLocal = spec.change_local.map((g) => `${cr}/${g}`);
  return { change_local: changeLocal, repo_root: [...spec.repo_root] };
}

/**
 * Classify a repo-relative path as allowed for stage promotion.
 */
export function normalizePromotionRelPath(relRepoPath) {
  const rel = posixPath(String(relRepoPath || ""));
  if (!rel || rel.startsWith("/") || rel.includes("..")) {
    return { ok: false, code: "PROMOTION_PATH_TRAVERSAL", path: relRepoPath };
  }
  return { ok: true, path: rel };
}

export function classifyPromotionPath(changeRoot, stage, relRepoPath) {
  const norm = normalizePromotionRelPath(relRepoPath);
  if (!norm.ok) return norm;
  const rel = norm.path;
  const { change_local, repo_root } = allowedPromotionPaths(changeRoot, stage);
  for (const pattern of change_local) {
    if (globMatch(rel, pattern)) return { ok: true, surface: "change_local" };
  }
  for (const pattern of repo_root) {
    if (globMatch(rel, pattern)) return { ok: true, surface: "repo_root" };
  }
  return { ok: false, code: "PROMOTION_PATH_FORBIDDEN", path: rel };
}

function globMatch(rel, pattern) {
  const esc = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*/g, "§§")
    .replace(/\*/g, "[^/]*")
    .replace(/§§/g, ".*");
  return new RegExp(`^${esc}$`).test(rel);
}
