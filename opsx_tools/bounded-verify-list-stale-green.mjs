import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Emit slice ids whose apply-green receipt is stale vs current identities.
 * Uses the same upstream shape as opsx-apply-verify-completion plus live
 * frozen-test identity. Usage: node bounded-verify-list-stale-green.mjs <change-root-rel>
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import {
  classifyGreenEvidenceFreshness,
  computeLiveFrozenTestContentIdentity,
  loadBoundedVerifyBindingFromDisk,
  loadChangeReceiptMaps,
} from "./opsx-apply-pipeline-identity.mjs";

const repoRoot = resolve(import.meta.dirname, "../..");
const changeRoot = process.argv[2];
if (!changeRoot) {
  process.stdout.write(JSON.stringify({ stale: [], error: "missing_change_root" }) + "\n");
  process.exit(1);
}
const designPath = resourcePath(repoRoot, changeRoot, "apply-design/APPLY-DESIGN.json");
if (!existsSync(designPath)) {
  process.stdout.write(JSON.stringify({ stale: [], split_apply: false }) + "\n");
  process.exit(0);
}
const design = JSON.parse(readFileSync(designPath, "utf8"));
const sliceIds =
  design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id);
const maps = loadChangeReceiptMaps(repoRoot, changeRoot, sliceIds);
const boundedVerifyBinding = loadBoundedVerifyBindingFromDisk(repoRoot, changeRoot);
const stale = [];
for (const id of sliceIds) {
  const red = maps.redBySlice?.[id];
  const live = red
    ? computeLiveFrozenTestContentIdentity(repoRoot, {
        test_paths: red.test_paths || [],
        fixture_paths: red.fixture_paths || [],
      })
    : null;
  const upstream = {
    design: maps.design,
    scaffold: maps.scaffoldBySlice?.[id],
    red,
    implement: maps.implementBySlice?.[id],
    designArtifactSha256: maps.designArtifactSha256,
    scaffoldArtifactSha256: maps.scaffoldArtifactSha256BySlice?.[id],
    redArtifactSha256: maps.redArtifactSha256BySlice?.[id],
    implementArtifactSha256: maps.implementArtifactSha256BySlice?.[id],
    frozenTestContentIdentity: maps.frozenTestContentBySlice?.[id],
    liveFrozenTestContentIdentity: live?.identity || null,
    boundedVerifyBinding,
  };
  const verdict = classifyGreenEvidenceFreshness(maps.greenBySlice?.[id], upstream);
  if (!verdict.current) {
    stale.push({
      slice_id: id,
      code: verdict.code || "STALE_GREEN",
      mismatched: verdict.mismatched || null,
    });
  }
}
process.stdout.write(JSON.stringify({ stale, split_apply: true }) + "\n");
