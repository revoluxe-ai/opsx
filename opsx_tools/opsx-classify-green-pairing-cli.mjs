/**
 * CLI: stdin JSON { red, implement } → stdout classifyGreenPredecessorPairing result.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import { classifyGreenPredecessorPairing } from "./opsx-apply-pipeline-identity.mjs";

const args = process.argv.slice(2);
let repoRoot = defaultRepoRootFromToolFile(import.meta.url);
const repoFlag = args.indexOf("--repo");
if (repoFlag >= 0) {
  repoRoot = resolve(args[repoFlag + 1] ?? "");
}
const raw = readFileSync(0, "utf8");
const input = JSON.parse(raw);
const result = classifyGreenPredecessorPairing({
  red: input.red,
  implement: input.implement,
  repoRoot,
});
process.stdout.write(JSON.stringify(result));
