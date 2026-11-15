import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Frozen test byte identity — legacy concat digest (sorted paths, raw bytes).
 *
 * Canonical per-path `identityDigest` lives in `opsx-apply-pipeline-identity.mjs`
 * (`computeLiveFrozenTestContentIdentity`). RED receipts may declare any accepted
 * variant; see `resolveDeclaredFrozenTestContentIdentity`.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

/** Legacy v1: concat sorted file bytes (test_paths ± fixture_paths). */
export const FROZEN_TEST_IDENTITY_LEGACY_CONCAT_V1 = "legacy_concat_v1";

/** Canonical v1: per-path sha256 lines via identityDigest (pipeline). */
export const FROZEN_TEST_IDENTITY_CANONICAL_PATH_SHA_V1 = "canonical_path_sha256_v1";

/**
 * @param {string} repoRoot
 * @param {string[]} testPaths
 * @returns {{ ok: true, digest: string } | { ok: false, code: string, path: string }}
 */
export function computeFrozenTestContentIdentity(repoRoot, testPaths = []) {
  const hash = createHash("sha256");
  for (const rel of [...testPaths].map(String).sort()) {
    const abs = resourcePath(repoRoot, rel);
    if (!existsSync(abs)) {
      return { ok: false, code: "missing_test_path", path: rel };
    }
    hash.update(readFileSync(abs));
  }
  return { ok: true, digest: hash.digest("hex") };
}
