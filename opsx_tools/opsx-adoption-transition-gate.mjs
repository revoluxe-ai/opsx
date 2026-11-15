import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Adoption transition gate: Python adoption_authority.py over IMPLEMENTATION_ADOPTED.
 * Historical READ_LEGACY / git pin удалён — только transition authority.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { sha256CanonicalJson } from "./opsx-adoption-canonical-json.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const ADOPTION_CONTRACT_ID = "EVIDENCE_INTEGRITY_ADOPTION_V0";
const ADOPTION_CHECKER_REL = ".cursor/pipeline/evidence-integrity/adoption_authority.py";
export const IMPLEMENTATION_ADOPTED = "IMPLEMENTATION_ADOPTED";
export const CURRENT_BYTE_OWNER_ADOPTION = "CURRENT_BYTE_OWNER_ADOPTION";
export const VALIDATE_GREEN_TRANSITION = "VALIDATE_GREEN_TRANSITION";

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function isRepoContainedPath(absPath, repoRoot = DEFAULT_ROOT) {
  const repo = resolve(repoRoot);
  const target = resolve(absPath);
  const rel = relative(repo, target);
  if (rel === "") return true;
  return rel.length > 0 && !rel.startsWith("..") && !rel.split(sep).includes("..");
}

export function resolveAdoptionCheckerPython(repoRoot = DEFAULT_ROOT) {
  if (process.env.PYTHON && String(process.env.PYTHON).trim()) {
    return String(process.env.PYTHON).trim();
  }
  const venv312 = resolve(
    repoRoot,
    "tools/langgraph_runtime/.venv312/Scripts/python.exe",
  );
  if (existsSync(venv312)) return venv312;
  return "python3";
}

function resolveAdoptionCheckerScript(repoRoot = DEFAULT_ROOT) {
  const local = resourcePath(repoRoot, ADOPTION_CHECKER_REL);
  if (existsSync(local)) {
    return local;
  }
  const fallback = resolve(DEFAULT_ROOT, ADOPTION_CHECKER_REL);
  if (existsSync(fallback)) {
    return fallback;
  }
  return local;
}

export function runAdoptionAuthorityCheck(bundle, repoRoot = DEFAULT_ROOT) {
  const checker = resolveAdoptionCheckerScript(repoRoot);
  const python = resolveAdoptionCheckerPython(DEFAULT_ROOT);
  const run = spawnSync(python, [checker], {
    input: JSON.stringify(bundle),
    encoding: "utf8",
  });
  if (run.status !== 0 || !run.stdout) {
    return {
      schema: "opsx.adoption_authority_check",
      owner_authentication: "UNPROVEN",
      live_authority: "NO",
      authority_permitted: false,
      authority_emitted: false,
      implementation_adopted_authorized: false,
      findings: [
        {
          code: "ADOPTION_AUTHORITY_INVALID",
          severity: "HARD",
          evidence: "adoption checker failed closed",
        },
      ],
      writes: [],
    };
  }
  return JSON.parse(run.stdout);
}

function auditReceiptSha256(audit) {
  return sha256CanonicalJson(audit, createHash);
}

export function isLegacyAdoptionAuthority(authority) {
  if (!isObject(authority)) return false;
  const version = authority.schema_version;
  const v2Marker =
    (typeof version === "number" && version >= 2) ||
    Object.prototype.hasOwnProperty.call(authority, "audit_receipt_sha256") ||
    Object.prototype.hasOwnProperty.call(authority, "owner_attestation_ref") ||
    Object.prototype.hasOwnProperty.call(authority, "bindings");
  if (v2Marker) return false;
  return (
    authority.authorization_class === CURRENT_BYTE_OWNER_ADOPTION &&
    typeof authority.candidate_digest === "string" &&
    authority.candidate_digest.length > 0
  );
}

export function isAdoptionV2Claim(artifact) {
  if (!isObject(artifact) || artifact.status !== IMPLEMENTATION_ADOPTED) return false;
  if (artifact.adoption_contract === ADOPTION_CONTRACT_ID) return true;
  const pointer = artifact.current_byte_adoption_authority;
  if (!isObject(pointer)) return false;
  if (typeof pointer.schema_version === "number" && pointer.schema_version >= 2) return true;
  return typeof pointer.audit_receipt_sha256 === "string" && pointer.audit_receipt_sha256.length > 0;
}

export function resolveImplementationAdoptionBundle(artifact, repoRoot = DEFAULT_ROOT) {
  if (!isObject(artifact) || artifact.status !== IMPLEMENTATION_ADOPTED) return null;
  const bundle = {
    implementation: artifact,
    red_status: artifact.red_identity?.status || "ALREADY_SATISFIED",
  };
  const auditPath = artifact.audit_receipt_path;
  if (typeof auditPath === "string" && auditPath.length > 0) {
    const auditAbs = resourcePath(repoRoot, auditPath);
    if (existsSync(auditAbs) && isRepoContainedPath(auditAbs, repoRoot)) {
      const audit = JSON.parse(readFileSync(auditAbs, "utf8"));
      bundle.audit = audit;
      bundle.audit_file_sha256 = auditReceiptSha256(audit);
    }
  }
  const authorityPath = artifact.current_byte_adoption_authority?.machine_path;
  if (typeof authorityPath === "string" && authorityPath.length > 0) {
    const authorityAbs = resourcePath(repoRoot, authorityPath);
    if (existsSync(authorityAbs) && isRepoContainedPath(authorityAbs, repoRoot)) {
      bundle.authority = JSON.parse(readFileSync(authorityAbs, "utf8"));
    }
  }
  return bundle;
}

export function implementationAdoptedTransitionAuthorized(artifact, repoRoot = DEFAULT_ROOT) {
  if (!isObject(artifact) || artifact.status !== IMPLEMENTATION_ADOPTED) return false;
  const bundle = resolveImplementationAdoptionBundle(artifact, repoRoot);
  if (!bundle) return false;
  const check = runAdoptionAuthorityCheck(bundle, repoRoot);
  return check.implementation_adopted_authorized === true;
}

export function implementationAdoptedTransitionErrors(
  artifact,
  repoRoot = DEFAULT_ROOT,
  _options = {},
) {
  if (!isObject(artifact) || artifact.status !== IMPLEMENTATION_ADOPTED) return [];
  const bundle = resolveImplementationAdoptionBundle(artifact, repoRoot);
  const check = runAdoptionAuthorityCheck(bundle || { implementation: artifact }, repoRoot);
  if (check.implementation_adopted_authorized === true) return [];
  const hard = (check.findings || []).find((item) => item && item.severity === "HARD");
  const legacyInfo = (check.findings || []).find((item) => item && item.code === "ADOPTION_AUTHORITY_LEGACY");
  const code =
    hard?.code ||
    (legacyInfo ? "ADOPTION_AUTHORITY_LEGACY" : "ADOPTION_AUDIT_MISSING");
  return [
    {
      code,
      message:
        hard?.evidence ||
        "IMPLEMENTATION_ADOPTED is not transition-authorized without audit and authority v2",
      transition_gate: true,
    },
  ];
}

export function adoptionGreenTransitionErrors(greenArtifact, repoRoot = DEFAULT_ROOT, _options = {}) {
  if (!isObject(greenArtifact) || greenArtifact.predecessor_branch !== "ADOPTION") return [];
  const implPath = greenArtifact.implementation_identity?.machine_path;
  if (typeof implPath !== "string" || !implPath) {
    return [
      {
        code: "ADOPTION_AUDIT_MISSING",
        message: "ADOPTION GREEN requires implementation_identity.machine_path",
        transition_gate: true,
      },
    ];
  }
  const implAbs = resourcePath(repoRoot, implPath);
  if (!existsSync(implAbs)) {
    return [
      {
        code: "ADOPTION_AUDIT_MISSING",
        message: "linked IMPLEMENTATION receipt is missing",
        transition_gate: true,
      },
    ];
  }
  const implement = JSON.parse(readFileSync(implAbs, "utf8"));
  if (implementationAdoptedTransitionAuthorized(implement, repoRoot)) return [];
  const bundle = resolveImplementationAdoptionBundle(implement, repoRoot);
  const check = runAdoptionAuthorityCheck(bundle || { implementation: implement }, repoRoot);
  const hard = (check.findings || []).find((item) => item && item.severity === "HARD");
  const legacyInfo = (check.findings || []).find((item) => item && item.code === "ADOPTION_AUTHORITY_LEGACY");
  const code =
    hard?.code ||
    (legacyInfo ? "ADOPTION_AUTHORITY_LEGACY" : "ADOPTION_AUDIT_MISSING");
  return [
    {
      code,
      message:
        hard?.evidence ||
        "ADOPTION GREEN requires audit and authority v2 on the linked IMPLEMENTATION receipt",
      transition_gate: true,
    },
  ];
}
