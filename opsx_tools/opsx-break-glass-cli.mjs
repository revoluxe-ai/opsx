/**
 * Hermetic CLI — Owner `/opsx-break-glass` (no secrets) + legacy HMAC grant paths.
 */
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ISSUER_HMAC_ENV,
  VERIFY_HMAC_ENV,
  buildOwnerGrantTemplate,
  findOpenIncidents,
  revalidateBreakGlassIncident,
  writeOwnerAttestationRecord,
  issueGrantToTrustedStore,
  resolveAuthoritativeExecutionIdentity,
  resolveActiveOpsxChangeContext,
} from "./opsx-break-glass.mjs";
import {
  activateBreakGlassOwnerOverride,
  activateBreakGlassUniversalV2,
  buildUniversalV2OwnerGrantTemplate,
  issueUniversalV2GrantToTrustedStore,
  FAILURE_UNKNOWN,
  GATE_ANY,
} from "./opsx-break-glass-universal-v2.mjs";
import { ensureProcessGateDeferralForChange } from "./opsx-break-glass-process-gate-deferral.mjs";

function parseArgs(argv) {
  const out = {
    command: argv[0] || null,
    grantId: null,
    incidentId: null,
    blockedGate: null,
    failureCode: null,
    changeId: null,
    changeRoot: null,
    executionId: null,
    revision: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--grant-id" && argv[i + 1]) out.grantId = argv[++i];
    else if (a === "--incident-id" && argv[i + 1]) out.incidentId = argv[++i];
    else if (a === "--blocked-gate" && argv[i + 1]) out.blockedGate = argv[++i];
    else if (a === "--failure-code" && argv[i + 1]) out.failureCode = argv[++i];
    else if (a === "--change-id" && argv[i + 1]) out.changeId = argv[++i];
    else if (a === "--change-root" && argv[i + 1]) out.changeRoot = argv[++i];
    else if (a === "--execution-id" && argv[i + 1]) out.executionId = argv[++i];
    else if (a === "--revision" && argv[i + 1]) out.revision = argv[++i];
  }
  return out;
}

function resolveOpsxChangeBinding(repoRoot, args) {
  const changeRootHint =
    args.changeRoot || process.env.BG_CHANGE_ROOT || process.env.OPSX_CHANGE_ROOT || null;
  const changeIdHint = args.changeId || process.env.BG_CHANGE_ID || process.env.OPSX_CHANGE_ID || null;
  return resolveActiveOpsxChangeContext(repoRoot, { changeRootHint, changeIdHint });
}

function resolveRuntimeForOwnerOverride(repoRoot, args) {
  const binding = resolveOpsxChangeBinding(repoRoot, args);
  if (!binding.ok) {
    return binding;
  }
  const executionId =
    args.executionId || process.env.BG_EXECUTION_ID || `exec:${binding.change_id}`;
  return resolveAuthoritativeExecutionIdentity({
    repoRoot,
    changeId: binding.change_id,
    executionId,
    blockedGate: args.blockedGate || process.env.BG_BLOCKED_GATE || GATE_ANY,
    failureCode: args.failureCode || process.env.BG_FAILURE_CODE || FAILURE_UNKNOWN,
    revision: args.revision || process.env.BG_REVISION || null,
    branch: process.env.BG_BRANCH || null,
  });
}

function runOwnerOverride(repoRoot, args) {
  const runtimeIdentity = resolveRuntimeForOwnerOverride(repoRoot, args);
  if (!runtimeIdentity.ok) {
    return runtimeIdentity;
  }
  return activateBreakGlassOwnerOverride({
    repoRoot,
    runtimeIdentity,
  });
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const repoRoot = resolve(process.cwd());
  const command = args.command;

  if (!command || command === "owner-override" || command === "override") {
    const result = runOwnerOverride(repoRoot, args);
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 2;
  }
  if (command === "list-open") {
    console.log(JSON.stringify({ open: findOpenIncidents(repoRoot) }, null, 2));
    return 0;
  }
  if (command === "ensure-deferral") {
    const binding = resolveOpsxChangeBinding(repoRoot, args);
    if (!binding.ok) {
      console.log(JSON.stringify(binding, null, 2));
      return 2;
    }
    const result = ensureProcessGateDeferralForChange({
      repoRoot,
      changeId: binding.change_id,
      changeRoot: binding.change_root,
    });
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 2;
  }
  if (command === "issue-grant") {
    const issuer = process.env[ISSUER_HMAC_ENV];
    if (!issuer) {
      console.error("OWNER_AUTHORITY_UNBOUND: issuer secret missing");
      return 2;
    }
    const grant = buildOwnerGrantTemplate({
      changeId: process.env.BG_CHANGE_ID || "break-glass-pilot",
      executionId: process.env.BG_EXECUTION_ID || "exec-pilot-001",
      revision: process.env.BG_REVISION || "0000000",
    });
    writeOwnerAttestationRecord(repoRoot, grant.owner_attestation_ref, grant.grant_id);
    const issued = issueGrantToTrustedStore(repoRoot, grant, issuer);
    console.log(JSON.stringify(issued, null, 2));
    return issued.ok ? 0 : 2;
  }
  if (command === "activate") {
    if (!process.env[VERIFY_HMAC_ENV]) {
      console.error("OWNER_AUTHORITY_UNBOUND: verify secret missing");
      return 2;
    }
    const runtimeIdentity = resolveRuntimeForOwnerOverride(repoRoot, args);
    if (!runtimeIdentity.ok) {
      console.error(JSON.stringify(runtimeIdentity, null, 2));
      return 2;
    }
    const result = activateBreakGlassUniversalV2({
      repoRoot,
      grantId: args.grantId,
      runtimeIdentity,
    });
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 2;
  }
  if (command === "issue-universal-v2-grant") {
    const issuer = process.env[ISSUER_HMAC_ENV];
    if (!issuer) {
      console.error("OWNER_AUTHORITY_UNBOUND: issuer secret missing");
      return 2;
    }
    const runtimeIdentity = resolveRuntimeForOwnerOverride(repoRoot, args);
    if (!runtimeIdentity.ok) {
      console.error(JSON.stringify(runtimeIdentity, null, 2));
      return 2;
    }
    const grant = buildUniversalV2OwnerGrantTemplate({
      changeId: runtimeIdentity.identity?.change_id,
      executionId: runtimeIdentity.identity?.execution_id,
      revision: runtimeIdentity.identity?.revision,
      repo: runtimeIdentity.identity?.repo,
      branch: runtimeIdentity.identity?.branch,
      blockedGate: process.env.BG_BLOCKED_GATE || GATE_ANY,
      failureCode: process.env.BG_FAILURE_CODE || FAILURE_UNKNOWN,
    });
    writeOwnerAttestationRecord(repoRoot, grant.owner_attestation_ref, grant.grant_id);
    const issued = issueUniversalV2GrantToTrustedStore(
      repoRoot,
      grant,
      issuer,
      runtimeIdentity,
    );
    console.log(JSON.stringify(issued, null, 2));
    return issued.ok ? 0 : 2;
  }
  if (command === "revalidate") {
    if (!args.grantId || !args.incidentId) {
      console.error("usage: revalidate --grant-id <id> --incident-id <id>");
      return 2;
    }
    if (!process.env[VERIFY_HMAC_ENV]) {
      console.error("OWNER_AUTHORITY_UNBOUND: verify secret missing");
      return 2;
    }
    const result = revalidateBreakGlassIncident({
      repoRoot,
      incidentId: args.incidentId,
      grantId: args.grantId,
      runtimeIdentity: {
        ok: true,
        identity: {
          change_id: process.env.BG_CHANGE_ID,
          execution_id: process.env.BG_EXECUTION_ID,
          revision: process.env.BG_REVISION,
          blocked_gate: "APPLY_RED_OPERATIVE",
          failure_code: "SEMANTIC_ORACLE_UNPROVEN",
          process_defect_predicate: {
            class: "OPSX_GATE_DEFECT",
            independent_witness_ref: "owner:revalidation",
          },
        },
      },
    });
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 2;
  }
  console.error("unknown command");
  return 2;
}

const __cliEntry = process.argv[1] && resolve(process.argv[1]);
const __modulePath = fileURLToPath(import.meta.url);
if (__cliEntry === __modulePath) {
  process.exit(main());
}
