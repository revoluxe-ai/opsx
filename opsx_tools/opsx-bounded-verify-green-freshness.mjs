/**
 * CLI: native GREEN freshness for bounded-verify parity (classifyGreenEvidenceFreshness).
 *
 * Usage:
 *   node opsx_tools/opsx-bounded-verify-green-freshness.mjs --change-root openspec/changes/<id>
 */
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  classifyGreenEvidenceFreshness,
  loadCurrentSliceArtifactIdentities,
} from "./opsx-apply-pipeline-identity.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

function main(argv) {
  const idx = argv.indexOf("--change-root");
  const changeRoot = idx >= 0 ? argv[idx + 1] : argv[0];
  if (!changeRoot) {
    process.stdout.write(JSON.stringify({ ok: false, code: "missing_change_root" }) + "\n");
    process.exit(1);
  }
  const designPath = join(DEFAULT_ROOT, changeRoot, "apply-design/APPLY-DESIGN.json");
  if (!existsSync(designPath)) {
    process.stdout.write(
      JSON.stringify({ ok: true, split_apply: false, policy: "NOT_APPLICABLE" }) + "\n",
    );
    process.exit(0);
  }
  const design = JSON.parse(readFileSync(designPath, "utf8"));
  const sliceIds =
    design.slice_order?.nodes || (design.slices || []).map((s) => s.slice_id).filter(Boolean);
  const slices = [];
  for (const sliceId of sliceIds) {
    const ids = loadCurrentSliceArtifactIdentities(DEFAULT_ROOT, changeRoot, sliceId);
    const freshness = classifyGreenEvidenceFreshness(ids.green, {
      design: ids.design,
      scaffold: ids.scaffold,
      red: ids.red,
      implement: ids.implement,
      designArtifactSha256: ids.designArtifactSha256,
      scaffoldArtifactSha256: ids.scaffoldArtifactSha256,
      redArtifactSha256: ids.redArtifactSha256,
      implementArtifactSha256: ids.implementArtifactSha256,
      frozenTestContentIdentity: ids.frozenTestContentIdentity,
      liveFrozenTestContentIdentity: ids.liveFrozenTestContentIdentity,
      repoRoot: DEFAULT_ROOT,
    });
    slices.push({
      slice_id: sliceId,
      green_status: ids.green?.status ?? null,
      freshness,
    });
  }
  process.stdout.write(
    JSON.stringify({
      ok: true,
      split_apply: true,
      policy: "SPLIT_APPLY_CURRENT_GREEN",
      slices,
    }) + "\n",
  );
}

main(process.argv.slice(2));
