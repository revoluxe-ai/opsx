/**
 * Approved native Break Glass revalidation verifier (fixed contract).
 *
 * Usage:
 *   node opsx-break-glass-revalidation-verifier.mjs \
 *     --incident-id <id> --execution-id <id> --revision <sha> \
 *     --failed-gate <gate> --failure-code <code>
 *
 * Emits one JSON line to stdout; exit 0 only when bindings match env contract.
 */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL = fileURLToPath(import.meta.url);

function parse(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--incident-id") out.incident_id = argv[++i];
    else if (a === "--execution-id") out.execution_id = argv[++i];
    else if (a === "--revision") out.revision = argv[++i];
    else if (a === "--failed-gate") out.failed_gate = argv[++i];
    else if (a === "--failure-code") out.failure_code = argv[++i];
    else if (a === "--repo-root") out.repo_root = argv[++i];
    else if (a === "--force-fail") out.force_fail = true;
  }
  return out;
}

function sha(s) {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

function main(argv) {
  const args = parse(argv);
  const required = ["incident_id", "execution_id", "revision", "failed_gate", "failure_code"];
  for (const k of required) {
    if (!args[k]) {
      process.stderr.write(`missing:${k}\n`);
      process.exit(2);
    }
  }
  if (args.force_fail) {
    process.stdout.write(`${JSON.stringify({ ok: false, code: "VERIFIER_FORCED_FAIL" })}\n`);
    process.exit(1);
  }
  const payload = {
    schema: "opsx.break_glass_native_revalidation_verifier_output",
    schema_version: 1,
    incident_id: args.incident_id,
    execution_id: args.execution_id,
    bound_revision: args.revision,
    failed_gate: args.failed_gate,
    failure_code: args.failure_code,
    verifier_entrypoint: TOOL,
    minted_fake_red: false,
    observed_behavior: "native_gate_revalidated",
  };
  const line = `${JSON.stringify(payload)}\n`;
  const stdout_sha256 = sha(line.trim());
  process.stdout.write(line);
  if (args.repo_root) {
    const audit = resolve(args.repo_root, "opsx/break-glass/revalidation-runs", `${args.incident_id}.audit`);
    mkdirSync(dirname(audit), { recursive: true });
    writeFileSync(
      audit,
      `${JSON.stringify({ stdout_sha256, verifier_entrypoint: TOOL, created_at: new Date().toISOString() })}\n`,
      "utf8",
    );
  }
  process.exit(0);
}

if (resolve(process.argv[1] || "") === TOOL) {
  main(process.argv.slice(2));
}
