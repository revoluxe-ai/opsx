import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Process-repair write-authority validator for /opsx-meta-fix-command-defects.
 *
 * Authority model (single SoT chain):
 *   opsx/sources.yaml
 *     → process_repair_authority pointer (required, fail-closed)
 *     → opsx_tools/registry/process_repair_authority.json (schema-validated)
 *     → this validator
 *
 * CORE paths = sources.yaml allowlist (publication allowlist reused as CORE).
 * Conditional paths require FIX_PLAN packet_class + class-specific proof.
 * Hard deny always wins.
 *
 * Two-phase PROCESS_TEST validation:
 *   PRE_WRITE  — class + covers + (existing marker | new_file | marker_migration)
 *   POST_WRITE — marker always required; flags cannot waive
 *
 * Durable APPLY receipts (.tmp/opsx-process-repair/<MISSION>/):
 *   hash-bound projection for /opsx-meta-commit conditional admission.
 *
 * Usage:
 *   node opsx_tools/opsx-process-repair-authority.mjs \
 *     --files <path>[,<path>...] \
 *     [--phase PRE_WRITE|POST_WRITE] \
 *     [--class PATH=CLASS]... \
 *     [--covers PATH=covered1|covered2]... \
 *     [--defect-binding PATH=DEFECT_ID]... \
 *     [--decision-proof PATH=true]... \
 *     [--new-file PATH=true]... \
 *     [--marker-migration PATH=true]... \
 *     [--repo <root>]
 *
 *   node opsx_tools/opsx-process-repair-authority.mjs \
 *     --create-receipt --mission <id> --base-sha <sha> \
 *     --receipt-out <path> --files ... [classifications...]
 *
 *   node opsx_tools/opsx-process-repair-authority.mjs \
 *     --validate-receipt <path> [--chat-files a,b] [--repo <root>]
 */
import { createHash } from "node:crypto";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SOURCES_REL = "opsx/sources.yaml";
const SCHEMA_REL = "opsx_tools/registry/process_repair_authority.schema.json";
const PROCESS_TEST_MARKER = "@opsx-process-test";
const RECEIPT_SCHEMA = "opsx.process_repair_receipt";
const CONDITIONAL_CLASSES = new Set([
  "PROCESS_DECISION",
  "PROCESS_TEST",
  "PROCESS_OPENSPEC_POLICY",
  "PROCESS_OPENSPEC_SCHEMA",
  "TEMPORAL_PROCESS_RUNTIME",
]);

export function posixPath(p) {
  return String(p || "").replace(/\\/g, "/");
}

/** Repo-relative path normalization (forward slashes, no leading ./). */
export function normalizeRelPath(p) {
  let n = posixPath(String(p || "")).trim();
  while (n.startsWith("./")) n = n.slice(2);
  n = n.replace(/^\/+/, "");
  return n;
}

export function matchGlob(rel, pattern) {
  const n = posixPath(rel);
  const p = posixPath(pattern);
  const esc = p
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*/g, "<<DS>>")
    .replace(/\*/g, "[^/]*")
    .replace(/<<DS>>/g, ".*");
  if (new RegExp(`^${esc}$`).test(n)) return true;
  if (p.endsWith("/**")) {
    const prefix = p.slice(0, -3);
    return n === prefix || n.startsWith(`${prefix}/`);
  }
  if (!p.includes("*") && (n === p || n.startsWith(`${p}/`))) return true;
  return false;
}

export function fileSha256(absPath) {
  const data = readFileSync(absPath);
  return createHash("sha256").update(data).digest("hex");
}

export function loadSourcesYaml(repoRoot = DEFAULT_ROOT) {
  const p = resourcePath(repoRoot, SOURCES_REL);
  if (!existsSync(p)) {
    return { ok: false, error: `missing ${SOURCES_REL}` };
  }
  const text = readFileSync(p, "utf8");
  const cfg = {
    allowlist: [],
    hard_exclude: [],
    process_repair_authority: null,
  };
  let listKey = null;
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const list = raw.match(/^\s*-\s+(.*)$/);
    if (list && listKey) {
      let v = list[1].trim();
      if (
        (v.startsWith('"') && v.endsWith('"')) ||
        (v.startsWith("'") && v.endsWith("'"))
      ) {
        v = v.slice(1, -1);
      }
      cfg[listKey].push(v);
      continue;
    }
    const kv = raw.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
    if (!kv) continue;
    const key = kv[1];
    const rest = kv[2].trim();
    if (key === "allowlist" || key === "hard_exclude") {
      listKey = key;
      cfg[key] = [];
      continue;
    }
    listKey = null;
    if (key === "process_repair_authority" && rest) {
      let v = rest;
      if (
        (v.startsWith('"') && v.endsWith('"')) ||
        (v.startsWith("'") && v.endsWith("'"))
      ) {
        v = v.slice(1, -1);
      }
      cfg.process_repair_authority = v;
    }
  }
  return { ok: true, cfg };
}

/**
 * Fail-closed resolver for sources.yaml process_repair_authority pointer.
 * No default registry fallback.
 */
export function resolveAuthorityPointer(repoRoot = DEFAULT_ROOT, pointer = null) {
  const rel = posixPath(pointer);
  if (!rel || !rel.trim()) {
    return {
      ok: false,
      code: "AUTHORITY_POINTER_MISSING",
      detail:
        "process_repair_authority pointer is empty or missing in sources.yaml",
    };
  }
  if (rel.startsWith("/") || /^[A-Za-z]:/.test(rel)) {
    return {
      ok: false,
      code: "AUTHORITY_POINTER_ABSOLUTE",
      detail: "process_repair_authority pointer must be repository-relative",
    };
  }
  if (rel.includes("..")) {
    return {
      ok: false,
      code: "AUTHORITY_POINTER_ESCAPING",
      detail: "process_repair_authority pointer must not escape repository root",
    };
  }
  const abs = resourcePath(repoRoot, rel);
  const rootResolved = resolve(repoRoot);
  if (!abs.startsWith(rootResolved)) {
    return {
      ok: false,
      code: "AUTHORITY_POINTER_ESCAPING",
      detail: "process_repair_authority pointer resolves outside repository root",
    };
  }
  if (!existsSync(abs)) {
    return {
      ok: false,
      code: "AUTHORITY_POINTER_NOT_FOUND",
      detail: `process_repair_authority target does not exist: ${rel}`,
    };
  }
  return { ok: true, pointer: rel, abs };
}

function schemaTypeOf(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/**
 * Deterministic JSON Schema subset validator for process_repair_authority.schema.json.
 * Fail-closed: AUTHORITY_SCHEMA_INVALID on any mismatch.
 */
export function validateJsonSchemaSubset(value, schema, at = "$") {
  const errors = [];
  const t = schemaTypeOf(value);

  if (schema.type) {
    const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
    const mapped =
      t === "object" && value !== null && !Array.isArray(value)
        ? "object"
        : t === "number" && Number.isInteger(value)
          ? "integer"
          : t;
    if (!allowed.includes(mapped) && !(allowed.includes("integer") && mapped === "number")) {
      errors.push({ at, code: "type", detail: `expected ${allowed.join("|")}, got ${mapped}` });
      return errors;
    }
  }

  if (schema.const !== undefined && value !== schema.const) {
    errors.push({ at, code: "const", detail: `expected ${JSON.stringify(schema.const)}` });
  }

  if (schema.enum && !schema.enum.includes(value)) {
    errors.push({ at, code: "enum", detail: `value not in enum` });
  }

  if (schema.type === "string" || (t === "string" && schema.minLength !== undefined)) {
    if (typeof value === "string" && schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push({ at, code: "minLength", detail: `minLength ${schema.minLength}` });
    }
  }

  if (schema.type === "array" || Array.isArray(value)) {
    if (Array.isArray(value)) {
      if (schema.minItems !== undefined && value.length < schema.minItems) {
        errors.push({ at, code: "minItems", detail: `minItems ${schema.minItems}` });
      }
      if (schema.items) {
        value.forEach((item, i) => {
          errors.push(...validateJsonSchemaSubset(item, schema.items, `${at}[${i}]`));
        });
      }
    }
  }

  if (
    (schema.type === "object" || (t === "object" && value !== null && !Array.isArray(value))) &&
    value &&
    typeof value === "object" &&
    !Array.isArray(value)
  ) {
    if (schema.required) {
      for (const key of schema.required) {
        if (!(key in value)) {
          errors.push({ at, code: "required", detail: `missing required property ${key}` });
        }
      }
    }
  if (schema.additionalProperties === false && schema.properties) {
      for (const key of Object.keys(value)) {
        if (!(key in schema.properties)) {
          errors.push({ at, code: "additionalProperties", detail: `unexpected property ${key}` });
        }
      }
    }
    if (schema.properties) {
      for (const [key, sub] of Object.entries(schema.properties)) {
        if (key in value) {
          errors.push(...validateJsonSchemaSubset(value[key], sub, `${at}.${key}`));
        }
      }
    }
  }

  return errors;
}

export function loadAuthoritySchema(repoRoot = DEFAULT_ROOT) {
  const abs = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(abs)) {
    return { ok: false, code: "AUTHORITY_SCHEMA_MISSING", error: `missing ${SCHEMA_REL}` };
  }
  try {
    const schema = JSON.parse(readFileSync(abs, "utf8"));
    return { ok: true, schema };
  } catch (e) {
    return {
      ok: false,
      code: "AUTHORITY_SCHEMA_INVALID",
      error: `invalid JSON ${SCHEMA_REL}: ${e.message}`,
    };
  }
}

export function validateAuthorityRegistrySchema(registry, repoRoot = DEFAULT_ROOT) {
  const loaded = loadAuthoritySchema(repoRoot);
  if (!loaded.ok) return loaded;
  const errors = validateJsonSchemaSubset(registry, loaded.schema);
  if (errors.length) {
    return {
      ok: false,
      code: "AUTHORITY_SCHEMA_INVALID",
      error: "authority registry failed schema validation",
      schema_errors: errors,
    };
  }
  return { ok: true };
}

export function loadAuthorityRegistry(repoRoot = DEFAULT_ROOT) {
  const sources = loadSourcesYaml(repoRoot);
  if (!sources.ok) return sources;

  const pointerResolved = resolveAuthorityPointer(
    repoRoot,
    sources.cfg.process_repair_authority,
  );
  if (!pointerResolved.ok) {
    return {
      ok: false,
      error: pointerResolved.detail,
      code: pointerResolved.code,
    };
  }

  let registry;
  try {
    registry = JSON.parse(readFileSync(pointerResolved.abs, "utf8"));
  } catch (e) {
    return {
      ok: false,
      error: `invalid JSON ${pointerResolved.pointer}: ${e.message}`,
      code: "AUTHORITY_REGISTRY_INVALID",
    };
  }

  const schemaCheck = validateAuthorityRegistrySchema(registry, repoRoot);
  if (!schemaCheck.ok) {
    return schemaCheck;
  }

  if (registry.schema !== "opsx.process_repair_authority") {
    return {
      ok: false,
      error: "authority registry schema mismatch",
      code: "AUTHORITY_REGISTRY_INVALID",
    };
  }

  return {
    ok: true,
    pointer: pointerResolved.pointer,
    sources: sources.cfg,
    registry,
  };
}

function isHardDenied(rel, denyGlobs) {
  const n = posixPath(rel);
  for (const g of denyGlobs || []) {
    if (matchGlob(n, g)) return true;
  }
  return false;
}

function isCoreAllowlisted(rel, allowlist) {
  const n = posixPath(rel);
  for (const g of allowlist || []) {
    if (matchGlob(n, g)) return true;
  }
  return false;
}

function findConditionalClass(rel, conditionalClasses) {
  const n = posixPath(rel);
  for (const cls of conditionalClasses || []) {
    for (const g of cls.globs || []) {
      if (matchGlob(n, g)) return cls;
    }
  }
  return null;
}

export function fileHasProcessTestMarker(absPath, marker = PROCESS_TEST_MARKER) {
  if (!existsSync(absPath)) return false;
  const head = readFileSync(absPath, "utf8").slice(0, 4000);
  return head.includes(marker);
}

function evaluateProcessTestMarker({
  rel,
  repoRoot,
  registry,
  phase,
  newFile,
  markerMigration,
}) {
  const marker = registry.process_test_marker || PROCESS_TEST_MARKER;
  const abs = resourcePath(repoRoot, rel);
  const hasMarker = fileHasProcessTestMarker(abs, marker);

  if (phase === "POST_WRITE") {
    if (!hasMarker) {
      return {
        ok: false,
        code: "PROCESS_TEST_MARKER_MISSING",
        path: rel,
        detail: `POST_WRITE requires marker ${marker} in file header; flags cannot waive`,
      };
    }
    return { ok: true };
  }

  // PRE_WRITE
  if (hasMarker || newFile || markerMigration) {
    return { ok: true };
  }
  return {
    ok: false,
    code: "PROCESS_TEST_MARKER_MISSING",
    path: rel,
    detail: `PRE_WRITE requires existing marker ${marker}, new_file=true, or marker_migration=true`,
  };
}

/**
 * Classify one FIX_PLAN path.
 */
export function classifyProcessRepairPath({
  path,
  authority,
  packetClass = null,
  coversPaths = [],
  defectBinding = null,
  decisionProof = false,
  repoRoot = DEFAULT_ROOT,
  phase = "POST_WRITE",
  newFile = false,
  markerMigration = false,
} = {}) {
  const rel = posixPath(path);
  if (!authority?.ok) {
    return {
      ok: false,
      code: authority?.code || "AUTHORITY_REGISTRY_MISSING",
      path: rel,
      detail: authority?.error || "authority load failed",
    };
  }
  const { sources, registry } = authority;
  const deny = [...(registry.hard_deny || []), ...(sources.hard_exclude || [])];
  const denyForRepair = deny.filter(
    (g) => posixPath(g) !== "openspec/**" && posixPath(g) !== "openspec",
  );

  if (isHardDenied(rel, denyForRepair)) {
    return {
      ok: false,
      code: "HARD_DENY",
      path: rel,
      detail: "path matches process-repair hard deny",
    };
  }

  if (isCoreAllowlisted(rel, sources.allowlist)) {
    return {
      ok: true,
      path: rel,
      class: "CORE",
      source: "sources.yaml allowlist",
    };
  }

  const cond = findConditionalClass(rel, registry.conditional_classes);
  if (!cond) {
    return {
      ok: false,
      code: "UNCLASSIFIED",
      path: rel,
      detail: "path is neither CORE allowlisted nor a conditional class",
    };
  }

  if (!packetClass || packetClass !== cond.id) {
    return {
      ok: false,
      code: "PACKET_CLASS_REQUIRED",
      path: rel,
      detail: `conditional class ${cond.id} requires packet_class=${cond.id}`,
      expected_class: cond.id,
    };
  }

  const requires = new Set(cond.requires || []);
  if (requires.has("covers_paths")) {
    const covers = (coversPaths || []).map(posixPath).filter(Boolean);
    if (!covers.length) {
      return {
        ok: false,
        code: "COVERS_PATHS_REQUIRED",
        path: rel,
        detail: "PROCESS_TEST requires covers_paths referencing authorized process surfaces",
      };
    }
    for (const c of covers) {
      const coveredOk =
        isCoreAllowlisted(c, sources.allowlist) ||
        Boolean(findConditionalClass(c, registry.conditional_classes));
      if (!coveredOk || isHardDenied(c, denyForRepair)) {
        return {
          ok: false,
          code: "COVERS_PATHS_INVALID",
          path: rel,
          detail: `covers_paths entry not an authorized process surface: ${c}`,
        };
      }
    }
  }

  if (requires.has("marker_opsx_process_test")) {
    const markerResult = evaluateProcessTestMarker({
      rel,
      repoRoot,
      registry,
      phase,
      newFile,
      markerMigration,
    });
    if (!markerResult.ok) return markerResult;
  }

  if (requires.has("opsx_process_decision_proof") && !decisionProof) {
    return {
      ok: false,
      code: "DECISION_PROOF_REQUIRED",
      path: rel,
      detail:
        "PROCESS_DECISION requires opsx_process_decision_proof=true (OPSX process-plane decision only)",
    };
  }

  if (requires.has("defect_binding") && !defectBinding) {
    return {
      ok: false,
      code: "DEFECT_BINDING_REQUIRED",
      path: rel,
      detail: "TEMPORAL_PROCESS_RUNTIME requires defect_binding to an exact FIX_PLAN defect id",
    };
  }

  return {
    ok: true,
    path: rel,
    class: cond.id,
    source: "conditional_class",
  };
}

/**
 * Validate a frozen FIX_PLAN path set for a given phase.
 */
export function validateFixPlanPaths({
  files = [],
  classifications = {},
  repoRoot = DEFAULT_ROOT,
  phase = "POST_WRITE",
} = {}) {
  const authority = loadAuthorityRegistry(repoRoot);
  if (!authority.ok) {
    return {
      ok: false,
      status: "FAIL",
      phase,
      error_count: 1,
      errors: [
        {
          code: authority.code || "AUTHORITY_REGISTRY_MISSING",
          detail: authority.error,
        },
      ],
    };
  }
  const errors = [];
  const results = [];
  const seen = new Set();
  for (const raw of files) {
    const rel = posixPath(raw);
    if (!rel || seen.has(rel)) continue;
    seen.add(rel);
    const meta = classifications[rel] || classifications[raw] || {};
    const one = classifyProcessRepairPath({
      path: rel,
      authority,
      packetClass: meta.packetClass || meta.class || null,
      coversPaths: meta.coversPaths || meta.covers_paths || [],
      defectBinding: meta.defectBinding || meta.defect_binding || null,
      decisionProof: Boolean(meta.decisionProof || meta.decision_proof),
      repoRoot,
      phase,
      newFile: Boolean(meta.newFile || meta.new_file),
      markerMigration: Boolean(meta.markerMigration || meta.marker_migration),
    });
    results.push(one);
    if (!one.ok) {
      errors.push({
        code: one.code,
        path: one.path,
        detail: one.detail,
        expected_class: one.expected_class,
      });
    }
  }
  return {
    ok: errors.length === 0,
    status: errors.length === 0 ? "PASS" : "FAIL",
    phase,
    error_count: errors.length,
    files_checked: results.length,
    results,
    errors,
    authority_pointer: authority.pointer,
  };
}

function canonicalizeReceiptBody(body) {
  const clone = { ...body };
  delete clone.content_hash;
  return JSON.stringify(clone, null, 2);
}

export function computeReceiptContentHash(body) {
  return createHash("sha256").update(canonicalizeReceiptBody(body)).digest("hex");
}

function buildFileEntry(rel, result, classifications, repoRoot) {
  const meta = classifications[rel] || {};
  const abs = resourcePath(repoRoot, rel);
  const entry = {
    path: rel,
    class: result.class,
    sha256: existsSync(abs) ? fileSha256(abs) : null,
    packet_class: meta.packetClass || meta.class || null,
    decision_proof: Boolean(meta.decisionProof || meta.decision_proof),
    covers_paths: (meta.coversPaths || meta.covers_paths || []).map(posixPath),
    defect_binding: meta.defectBinding || meta.defect_binding || null,
    new_file: Boolean(meta.newFile || meta.new_file),
    marker_migration: Boolean(meta.markerMigration || meta.marker_migration),
  };
  return entry;
}

/**
 * Create a hash-bound process-repair APPLY receipt after POST_WRITE validation.
 */
export function createProcessRepairReceipt({
  missionId,
  baseSha,
  files = [],
  classifications = {},
  repoRoot = DEFAULT_ROOT,
  receiptOut,
  status = "APPLY_COMPLETE",
} = {}) {
  if (!missionId || !baseSha) {
    return {
      ok: false,
      code: "RECEIPT_INPUT_MISSING",
      detail: "missionId and baseSha are required",
    };
  }

  const pre = validateFixPlanPaths({
    files,
    classifications,
    repoRoot,
    phase: "PRE_WRITE",
  });
  if (!pre.ok) {
    return {
      ok: false,
      code: "RECEIPT_PRE_WRITE_FAILED",
      detail: "PRE_WRITE validation failed",
      validation: pre,
    };
  }

  const post = validateFixPlanPaths({
    files,
    classifications,
    repoRoot,
    phase: "POST_WRITE",
  });
  if (!post.ok) {
    return {
      ok: false,
      code: "RECEIPT_POST_WRITE_FAILED",
      detail: "POST_WRITE validation failed",
      validation: post,
    };
  }

  const fileEntries = post.results
    .filter((r) => r.ok)
    .map((r) => buildFileEntry(r.path, r, classifications, repoRoot));

  const body = {
    schema: RECEIPT_SCHEMA,
    schema_version: 1,
    mission_id: missionId,
    base_sha: baseSha,
    status,
    created_at: new Date().toISOString(),
    writer_command: "/opsx-meta-fix-command-defects",
    phase_validated: ["PRE_WRITE", "POST_WRITE"],
    authority_pointer: post.authority_pointer,
    files: fileEntries,
  };
  body.content_hash = computeReceiptContentHash(body);

  if (receiptOut) {
    const outAbs = resourcePath(repoRoot, receiptOut);
    mkdirSync(dirname(outAbs), { recursive: true });
    writeFileSync(outAbs, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  }

  return { ok: true, receipt: body, receipt_path: receiptOut || null };
}

/**
 * Validate a durable process-repair receipt for /opsx-meta-commit admission.
 */
export function validateProcessRepairReceipt({
  receiptPath,
  repoRoot = DEFAULT_ROOT,
  chatFiles = null,
} = {}) {
  const abs = resourcePath(repoRoot, receiptPath);
  if (!existsSync(abs)) {
    return {
      ok: false,
      code: "RECEIPT_MISSING",
      detail: `receipt not found: ${receiptPath}`,
    };
  }

  let receipt;
  try {
    receipt = JSON.parse(readFileSync(abs, "utf8"));
  } catch (e) {
    return {
      ok: false,
      code: "RECEIPT_INVALID",
      detail: `invalid receipt JSON: ${e.message}`,
    };
  }

  if (receipt.schema !== RECEIPT_SCHEMA) {
    return {
      ok: false,
      code: "RECEIPT_INVALID",
      detail: "receipt schema mismatch",
    };
  }

  const expectedHash = computeReceiptContentHash(receipt);
  if (receipt.content_hash !== expectedHash) {
    return {
      ok: false,
      code: "RECEIPT_TAMPERED",
      detail: "receipt content_hash does not match body",
    };
  }

  if (!Array.isArray(receipt.files) || !receipt.files.length) {
    return {
      ok: false,
      code: "RECEIPT_INVALID",
      detail: "receipt files list is empty",
    };
  }

  const errors = [];
  const admitted = [];
  const chatSet = chatFiles
    ? new Set(chatFiles.map(posixPath))
    : null;

  const classifications = {};
  for (const f of receipt.files) {
  const rel = posixPath(f.path);
    classifications[rel] = {
      packetClass: f.packet_class,
      decisionProof: f.decision_proof,
      coversPaths: f.covers_paths || [],
      defectBinding: f.defect_binding,
      newFile: f.new_file,
      markerMigration: f.marker_migration,
    };

    const diskAbs = resourcePath(repoRoot, rel);
    if (!existsSync(diskAbs)) {
      errors.push({
        code: "RECEIPT_FILE_MISSING",
        path: rel,
        detail: "receipt-listed file no longer exists on disk",
      });
      continue;
    }

    const currentHash = fileSha256(diskAbs);
    if (f.sha256 && currentHash !== f.sha256) {
      errors.push({
        code: "RECEIPT_HASH_MISMATCH",
        path: rel,
        detail: "on-disk hash does not match receipt binding",
      });
      continue;
    }

    const authority = loadAuthorityRegistry(repoRoot);
    const classified = classifyProcessRepairPath({
      path: rel,
      authority,
      packetClass: f.packet_class,
      coversPaths: f.covers_paths || [],
      defectBinding: f.defect_binding,
      decisionProof: f.decision_proof,
      repoRoot,
      phase: "POST_WRITE",
    });

    if (!classified.ok) {
      errors.push({
        code: classified.code,
        path: rel,
        detail: classified.detail,
      });
      continue;
    }

    const isConditional = CONDITIONAL_CLASSES.has(classified.class);
    if (isConditional) {
      if (chatSet && !chatSet.has(rel)) {
        continue;
      }
      admitted.push(rel);
    }
  }

  return {
    ok: errors.length === 0,
    status: errors.length === 0 ? "PASS" : "FAIL",
    mission_id: receipt.mission_id,
    base_sha: receipt.base_sha,
    admitted_conditional_files: admitted,
    errors,
  };
}

function parseArgs(argv) {
  const out = {
    files: [],
    classifications: {},
    repoRoot: DEFAULT_ROOT,
    phase: "POST_WRITE",
    createReceipt: false,
    validateReceipt: null,
    missionId: null,
    baseSha: null,
    receiptOut: null,
    chatFiles: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") {
      out.repoRoot = resolve(argv[++i] || "");
      continue;
    }
    if (a === "--phase") {
      out.phase = String(argv[++i] || "POST_WRITE").toUpperCase();
      continue;
    }
    if (a === "--create-receipt") {
      out.createReceipt = true;
      continue;
    }
    if (a === "--validate-receipt") {
      out.validateReceipt = argv[++i] || "";
      continue;
    }
    if (a === "--mission") {
      out.missionId = argv[++i] || "";
      continue;
    }
    if (a === "--base-sha") {
      out.baseSha = argv[++i] || "";
      continue;
    }
    if (a === "--receipt-out") {
      out.receiptOut = argv[++i] || "";
      continue;
    }
    if (a === "--chat-files") {
      const raw = argv[++i] || "";
      out.chatFiles = raw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      continue;
    }
    if (a === "--files") {
      const raw = argv[++i] || "";
      out.files.push(
        ...raw
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      );
      continue;
    }
    if (a === "--class") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        const c = raw.slice(eq + 1);
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          packetClass: c,
        };
      }
      continue;
    }
    if (a === "--covers") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        const covers = raw
          .slice(eq + 1)
          .split("|")
          .map((s) => s.trim())
          .filter(Boolean);
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          coversPaths: covers,
        };
      }
      continue;
    }
    if (a === "--defect-binding") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          defectBinding: raw.slice(eq + 1),
        };
      }
      continue;
    }
    if (a === "--decision-proof") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          decisionProof: /^(1|true|yes)$/i.test(raw.slice(eq + 1)),
        };
      }
      continue;
    }
    if (a === "--new-file") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          newFile: /^(1|true|yes)$/i.test(raw.slice(eq + 1)),
        };
      }
      continue;
    }
    if (a === "--marker-migration") {
      const raw = argv[++i] || "";
      const eq = raw.indexOf("=");
      if (eq > 0) {
        const p = posixPath(raw.slice(0, eq));
        out.classifications[p] = {
          ...(out.classifications[p] || {}),
          markerMigration: /^(1|true|yes)$/i.test(raw.slice(eq + 1)),
        };
      }
      continue;
    }
  }
  return out;
}

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);

  if (args.validateReceipt) {
    const result = validateProcessRepairReceipt({
      receiptPath: args.validateReceipt,
      repoRoot: args.repoRoot,
      chatFiles: args.chatFiles,
    });
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
    process.exit(result.ok ? 0 : 1);
  }

  if (args.createReceipt) {
    if (!args.files.length) {
      process.stdout.write(
        JSON.stringify({
          status: "FAIL",
          errors: [{ code: "missing_files", detail: "pass --files a,b,c" }],
        }) + "\n",
      );
      process.exit(1);
    }
    const result = createProcessRepairReceipt({
      missionId: args.missionId,
      baseSha: args.baseSha,
      files: args.files,
      classifications: args.classifications,
      repoRoot: args.repoRoot,
      receiptOut: args.receiptOut,
    });
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
    process.exit(result.ok ? 0 : 1);
  }

  if (!args.files.length) {
    process.stdout.write(
      JSON.stringify({
        status: "FAIL",
        errors: [{ code: "missing_files", detail: "pass --files a,b,c" }],
      }) + "\n",
    );
    process.exit(1);
  }

  const result = validateFixPlanPaths({
    files: args.files,
    classifications: args.classifications,
    repoRoot: args.repoRoot,
    phase: args.phase,
  });
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
