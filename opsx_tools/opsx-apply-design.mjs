import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * APPLY DESIGN artifact validator and identity helpers.
 *
 * Does not invent architecture. Does not write product/tests. Does not run
 * RED/IMPLEMENT/GREEN. Agents produce APPLY-DESIGN.json; this tool checks
 * schema + semantic gates used by command-contract tests.
 *
 * Usage:
 *   node opsx_tools/opsx-apply-design.mjs <APPLY-DESIGN.json>
 *   node opsx_tools/opsx-apply-design.mjs --schema <schema> <APPLY-DESIGN.json>
 *   node opsx_tools/opsx-apply-design.mjs --freeze [--repo <root>] <APPLY-DESIGN.json>
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
  collectPipelineMutablePaths,
  collectProtectedContextPaths,
  isOpsxProcessContractPath,
  hashRepoRelativePaths,
  refreshApplyDesignProtectedContext,
  rebindReceiptApplyDesignIdentity,
  fileSha256,
  applyReceiptRelPaths,
} from "./opsx-apply-pipeline-identity.mjs";
import {
  APPLY_DESIGN_REVIEW,
  designCompositionSemanticErrors,
  missingDogfoodFailureClassCoverage,
} from "./opsx-apply-composition.mjs";
import {
  injectKnowledgeIntoDesign,
  validateDesignKnowledgeObligations,
} from "./opsx-bug-learning-apply.mjs";
import {
  assertManualWriterLeaseForMutation,
  finishManualWriterMutationGuard,
  inferChangeRootFromArtifactPath,
} from "./opsx-apply-change-writer.mjs";
import { persistApplyDesignWriterRecord } from "./opsx-apply-design-writer-record.mjs";
import { publishApplyDesignWriterRecord } from "./opsx-apply-design-writer-transition.mjs";
import { validateDesignSliceTechnicalContracts } from "./opsx-apply-red-oracle-authority.mjs";

export {
  injectKnowledgeIntoDesign,
  validateDesignKnowledgeObligations,
} from "./opsx-bug-learning-apply.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);
const SCHEMA_REL = "opsx_tools/registry/apply_design.schema.json";

export {
  identityDigest,
  freezePlanningIdentity,
  computeCurrentPlanningIdentity,
  classifyApplyDesignPlanningFreshness,
  evaluateApplyDesignFreshnessFromDisk,
};

export {
  designCompositionSemanticErrors,
  compositionContractsCoverEdges,
  collectRedOwnedCompositionObligations,
  collectGreenOwnedCompositionObligations,
  COMPOSITION_FAILURE_CLASS_IDS,
  APPLY_DESIGN_REVIEW,
} from "./opsx-apply-composition.mjs";

export function refreshApplyDesignIdentityFromDisk({
  repoRoot = DEFAULT_ROOT,
  artifactPath,
  rebindDownstream = false,
  writerSessionId = null,
} = {}) {
  const abs = resolve(artifactPath);
  const changeRoot = inferChangeRootFromArtifactPath(abs);
  const previous = JSON.parse(readFileSync(abs, "utf8"));
  const changeRootResolved =
    changeRoot || previous.planning_identity?.change_root || inferChangeRootFromArtifactPath(abs);
  let writerGuard = null;
  if (changeRootResolved) {
    writerGuard = assertManualWriterLeaseForMutation({
      repoRoot,
      changeRoot: changeRootResolved,
      writerSessionId,
      changeId: previous.change_id,
      planningDigest: previous.planning_identity?.planning_digest,
      primitive: "APPLY_DESIGN_REFRESH",
    });
    if (!writerGuard.ok) return writerGuard;
  }
  try {
  const productPaths = collectProtectedContextPaths(previous);
  const processPaths = previous.repository_identity?.inspected_paths || [];
  const pathSha256 = hashRepoRelativePaths(repoRoot, [
    ...productPaths,
    ...processPaths,
  ]);
  const refreshed = refreshApplyDesignProtectedContext(previous, pathSha256);
  const next = {
    ...refreshed.artifact,
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
  writeFileSync(abs, `${JSON.stringify(next, null, 2)}\n`);
  const mdPath = abs.replace(/\.json$/i, ".md");
  if (existsSync(mdPath)) {
    let md = readFileSync(mdPath, "utf8");
    md = md.replace(
      /(\*\*protected content_digest \(strict\):\*\* `)[0-9a-f]+(`)/i,
      `$1${next.protected_context_identity.content_digest}$2`,
    );
    md = md.replace(
      /(\*\*Generated at:\*\* `)[^`]+(`)/i,
      `$1${next.generated_at}$2`,
    );
    if (!/process_contract_identity/.test(md) && next.process_contract_identity?.paths?.length) {
      md = md.replace(
        /Authorized SCAFFOLD\/RED\/IMPLEMENT\/GREEN writes on `pipeline_mutable_paths` do not stale DESIGN via `inspected_digest`\./,
        `OPSX process-contract paths (${next.process_contract_identity.paths.join(", ")}) are provenance only and are not product protected context. Authorized SCAFFOLD/RED/IMPLEMENT/GREEN writes on \`pipeline_mutable_paths\` do not stale DESIGN via \`inspected_digest\`.`,
      );
    }
    writeFileSync(mdPath, md);
  }
  const newDesignSha = fileSha256(abs);
  const rebound = [];
  const writerRecord = changeRootResolved
    ? persistApplyDesignWriterRecord({
        repoRoot,
        changeRoot: changeRootResolved,
        changeId: next.change_id,
        designArtifactAbsPath: abs,
        design: next,
        primitive: "APPLY_DESIGN_REFRESH",
      })
    : null;
  if (writerRecord && writerRecord.ok === false) {
    return writerRecord;
  }
  if (rebindDownstream && refreshed.delta.rebind_safe) {
    const changeRoot = next.planning_identity?.change_root;
    const sliceIds = (next.slice_order?.nodes || next.slices || []).map(
      (s) => (typeof s === "string" ? s : s.slice_id),
    );
    for (const sliceId of sliceIds) {
      const rel = applyReceiptRelPaths(changeRoot, sliceId);
      const greenAbs = resourcePath(repoRoot, rel.green);
      if (!existsSync(greenAbs)) continue;
      const green = JSON.parse(readFileSync(greenAbs, "utf8"));
      const updated = rebindReceiptApplyDesignIdentity(green, next, newDesignSha);
      if (JSON.stringify(updated.apply_design_identity) !== JSON.stringify(green.apply_design_identity)) {
        writeFileSync(greenAbs, `${JSON.stringify(updated, null, 2)}\n`);
        rebound.push(rel.green);
      }
    }
  }
  return {
    ok: true,
    delta: refreshed.delta,
    artifact_path: posixRel(repoRoot, abs),
    design_artifact_sha256: newDesignSha,
    protected_context_identity: next.protected_context_identity,
    process_contract_identity: next.process_contract_identity,
    rebound,
    writer_record: writerRecord?.ok ? writerRecord : null,
  };
  } finally {
    if (changeRootResolved) {
      finishManualWriterMutationGuard({
        repoRoot,
        changeRoot: changeRootResolved,
        guard: writerGuard,
      });
    }
  }
}

function posixRel(repoRoot, abs) {
  return String(abs)
    .replace(/\\/g, "/")
    .replace(String(repoRoot).replace(/\\/g, "/").replace(/\/$/, "") + "/", "");
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function typeOk(value, schemaType) {
  if (schemaType === "object") return isObject(value);
  if (schemaType === "array") return Array.isArray(value);
  if (schemaType === "string") return typeof value === "string";
  if (schemaType === "integer") return Number.isInteger(value);
  if (schemaType === "boolean") return typeof value === "boolean";
  return true;
}

function resolveRef(rootSchema, ref) {
  if (!ref || !ref.startsWith("#/")) return null;
  const parts = ref.slice(2).split("/");
  let cur = rootSchema;
  for (const p of parts) {
    if (!isObject(cur) || !(p in cur)) return null;
    cur = cur[p];
  }
  return cur;
}

function validate(value, schema, rootSchema, path, errors) {
  if (!schema) return;
  if (schema.$ref) {
    validate(value, resolveRef(rootSchema, schema.$ref), rootSchema, path, errors);
    return;
  }
  if (schema.oneOf) {
    let matched = 0;
    let lastLocal = [];
    for (const branch of schema.oneOf) {
      const local = [];
      validate(value, branch, rootSchema, path, local);
      if (local.length === 0) matched += 1;
      else lastLocal = local;
    }
    if (matched !== 1) {
      errors.push({
        code: "oneOf_mismatch",
        message: `${path}: must match exactly one APPLY DESIGN status branch`,
      });
      if (matched === 0) {
        for (const e of lastLocal) errors.push(e);
      }
    }
    return;
  }
  if (schema.type && !typeOk(value, schema.type)) {
    errors.push({
      code: "type",
      message: `${path}: expected ${schema.type}`,
    });
    return;
  }
  if (schema.const !== undefined && value !== schema.const) {
    errors.push({
      code: "const",
      message: `${path}: must be ${JSON.stringify(schema.const)}`,
    });
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push({
      code: "enum",
      message: `${path}: invalid enum value`,
    });
  }
  if (schema.type === "string") {
    if (schema.minLength && value.length < schema.minLength) {
      errors.push({ code: "minLength", message: `${path}: too short` });
    }
    if (schema.pattern) {
      const re = new RegExp(schema.pattern);
      if (!re.test(value)) {
        errors.push({ code: "pattern", message: `${path}: pattern failed` });
      }
    }
  }
  if (schema.type === "array") {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push({ code: "minItems", message: `${path}: too few items` });
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push({ code: "maxItems", message: `${path}: too many items` });
    }
    if (schema.items) {
      value.forEach((item, i) =>
        validate(item, schema.items, rootSchema, `${path}[${i}]`, errors),
      );
    }
  }
  if (
    isObject(value) &&
    (schema.type === "object" ||
      schema.properties ||
      schema.required ||
      schema.additionalProperties === false)
  ) {
    if (Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (!(key in value)) {
          errors.push({
            code: "required",
            message: `${path}: missing required field ${key}`,
          });
        }
      }
    }
    if (schema.additionalProperties === false) {
      const allowed = new Set(Object.keys(schema.properties || {}));
      for (const key of Object.keys(value)) {
        if (!allowed.has(key)) {
          errors.push({
            code: "additionalProperties",
            message: `${path}: unexpected field ${key}`,
          });
        }
      }
    }
    for (const [key, propSchema] of Object.entries(schema.properties || {})) {
      if (key in value) {
        validate(value[key], propSchema, rootSchema, `${path}.${key}`, errors);
      }
    }
  }
}

function semanticErrors(artifact, { repoRoot = DEFAULT_ROOT } = {}) {
  const errors = [];
  if (!isObject(artifact)) return errors;
  if (artifact.chat_history_required !== false) {
    errors.push({
      code: "chat_history_required",
      message: "chat_history_required must be false (no chat-history authority)",
    });
  }
  const wb = artifact.write_boundary;
  if (isObject(wb)) {
    if (wb.product_code !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.product_code must be false",
      });
    }
    if (wb.tests !== false) {
      errors.push({
        code: "write_boundary",
        message: "write_boundary.tests must be false",
      });
    }
  }
  if (artifact.status === "APPLY_DESIGN_READY") {
    if (artifact.next_stage !== APPLY_DESIGN_REVIEW) {
      errors.push({
        code: "next_stage",
        message: "APPLY_DESIGN_READY requires next_stage=APPLY_DESIGN_REVIEW",
      });
    }
    const slicesReady = Array.isArray(artifact.slices) ? artifact.slices : [];
    for (const slice of slicesReady) {
      if (!slice || !slice.scaffold_contract) {
        errors.push({
          code: "scaffold_contract",
          message: `slice ${slice && slice.slice_id} missing scaffold_contract`,
        });
      }
    }
    const tech = validateDesignSliceTechnicalContracts(artifact, { repoRoot });
    for (const e of tech.errors) {
      errors.push(e);
    }
    const pci = artifact.protected_context_identity;
    if (!pci || !Array.isArray(pci.paths) || !pci.content_digest) {
      errors.push({
        code: "protected_context_identity",
        message: "APPLY_DESIGN_READY requires protected_context_identity",
      });
    } else {
      const expectedProtected = collectProtectedContextPaths(artifact);
      for (const p of expectedProtected) {
        if (!pci.paths.includes(p) && !pci.paths.some((x) => x === p)) {
          errors.push({
            code: "protected_context_identity",
            message: `protected path ${p} missing from protected_context_identity.paths`,
          });
        }
      }
      const mutable = collectPipelineMutablePaths(artifact);
      for (const p of pci.paths) {
        if (isOpsxProcessContractPath(p)) {
          errors.push({
            code: "protected_context_identity",
            message: `process-contract path ${p} must not be in protected_context_identity`,
          });
        }
        if (mutable.includes(p)) {
          errors.push({
            code: "protected_context_identity",
            message: `path ${p} cannot be both pipeline-mutable and protected`,
          });
        }
      }
    }
    const unresolved = Array.isArray(artifact.unresolved_items)
      ? artifact.unresolved_items
      : [];
    for (const item of unresolved) {
      if (item && item.severity === "architecture") {
        errors.push({
          code: "incomplete_architecture",
          message: `READY forbids architecture unresolved_items (${item.id})`,
        });
      }
    }
    const slices = Array.isArray(artifact.slices) ? artifact.slices : [];
    const order = artifact.slice_order;
    if (isObject(order)) {
      const nodeSet = new Set(order.nodes || []);
      const sliceIds = slices.map((s) => s.slice_id);
      for (const id of sliceIds) {
        if (!nodeSet.has(id)) {
          errors.push({
            code: "slice_order",
            message: `slice ${id} missing from slice_order.nodes`,
          });
        }
      }
      for (const id of nodeSet) {
        if (!sliceIds.includes(id)) {
          errors.push({
            code: "slice_order",
            message: `slice_order node ${id} has no slice`,
          });
        }
      }
      for (const edge of order.edges || []) {
        if (!nodeSet.has(edge.from) || !nodeSet.has(edge.to)) {
          errors.push({
            code: "slice_order",
            message: `edge ${edge.from}->${edge.to} references unknown node`,
          });
        }
      }
    }
    const reuse = Array.isArray(artifact.reuse_decisions)
      ? artifact.reuse_decisions
      : [];
    if (reuse.length < 1) {
      errors.push({
        code: "reuse_decisions",
        message: "APPLY_DESIGN_READY requires explicit reuse_decisions",
      });
    }
    const planning = artifact.planning_identity;
    if (isObject(planning) && Array.isArray(planning.inputs)) {
      const expected = freezePlanningIdentity(planning).planning_digest;
      if (planning.planning_digest !== expected) {
        errors.push({
          code: "planning_digest",
          message:
            "planning_digest does not match freezePlanningIdentity (canonical APPLY DESIGN planning identity)",
        });
      }
    }
    for (const e of designCompositionSemanticErrors(artifact)) {
      errors.push(e);
    }
    const ko = validateDesignKnowledgeObligations(artifact);
    for (const e of ko.errors) errors.push(e);
    // When race/lifecycle applicable, every dogfood failure class must be
    // explicitly classified applicable | not_applicable.
    const missingClasses = missingDogfoodFailureClassCoverage(artifact);
    for (const cls of missingClasses) {
      errors.push({
        code: "incomplete_composition",
        message: `failure_class_applicability missing classification for ${cls}`,
      });
    }
  }
  if (artifact.status === "BLOCKED") {
    if (artifact.next_stage !== "NONE") {
      errors.push({
        code: "next_stage",
        message: "BLOCKED requires next_stage=NONE",
      });
    }
    const blockers = Array.isArray(artifact.blockers) ? artifact.blockers : [];
    if (blockers.length < 1) {
      errors.push({
        code: "blockers",
        message: "BLOCKED requires blockers[]",
      });
    }
  }
  return errors;
}

export function validateApplyDesignArtifact({ artifact, schema, repoRoot = DEFAULT_ROOT }) {
  const errors = [];
  if (!isObject(artifact)) {
    return {
      ok: false,
      errors: [{ code: "type", message: "artifact must be an object" }],
    };
  }
  if (!isObject(schema)) {
    return {
      ok: false,
      errors: [{ code: "schema_missing", message: "schema missing" }],
    };
  }
  validate(artifact, schema, schema, "$", errors);
  for (const e of semanticErrors(artifact, { repoRoot })) errors.push(e);
  return { ok: errors.length === 0, errors, status: artifact.status };
}

export function loadApplyDesignSchema(repoRoot = DEFAULT_ROOT) {
  const schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  if (!existsSync(schemaPath)) return null;
  return JSON.parse(readFileSync(schemaPath, "utf8"));
}

/**
 * Inject CURRENT seam obligations into DESIGN and persist APPLY-DESIGN.json.
 * Sole write path that must call injectKnowledgeIntoDesign before freeze.
 */
export function freezeApplyDesignWithKnowledge({
  repoRoot = DEFAULT_ROOT,
  artifactPath,
  writerSessionId = null,
} = {}) {
  const abs = resolve(artifactPath);
  const artifact = JSON.parse(readFileSync(abs, "utf8"));
  const changeRoot =
    artifact.planning_identity?.change_root ||
    inferChangeRootFromArtifactPath(abs);
  let writerGuard = null;
  if (changeRoot) {
    writerGuard = assertManualWriterLeaseForMutation({
      repoRoot,
      changeRoot,
      writerSessionId,
      changeId: artifact.change_id,
      planningDigest: artifact.planning_identity?.planning_digest,
      primitive: "APPLY_DESIGN_FREEZE",
    });
    if (!writerGuard.ok) return writerGuard;
  }
  try {
  const injected = injectKnowledgeIntoDesign(artifact, repoRoot);
  const next = injected.design;
  writeFileSync(abs, `${JSON.stringify(next, null, 2)}\n`);
  const writerRecord = changeRoot
    ? persistApplyDesignWriterRecord({
        repoRoot,
        changeRoot,
        changeId: next.change_id,
        designArtifactAbsPath: abs,
        design: next,
        primitive: "APPLY_DESIGN_FREEZE",
      })
    : null;
  if (writerRecord && writerRecord.ok === false) {
    return writerRecord;
  }
  return {
    ok: true,
    artifact_path: posixRel(repoRoot, abs),
    knowledge_obligations: injected.knowledge_obligations,
    matched_seam_ids: injected.matched_seam_ids,
    writer_record: writerRecord?.ok ? writerRecord : null,
  };
  } finally {
    if (changeRoot) {
      finishManualWriterMutationGuard({
        repoRoot,
        changeRoot,
        guard: writerGuard,
      });
    }
  }
}

function main(argv) {
  const args = [...argv];
  let repoRoot = DEFAULT_ROOT;
  const repoFlag = args.indexOf("--repo");
  if (repoFlag >= 0) {
    repoRoot = resolve(args[repoFlag + 1]);
    args.splice(repoFlag, 2);
  }
  let schemaPath;
  const schemaFlag = args.indexOf("--schema");
  if (schemaFlag >= 0) {
    schemaPath = resolve(args[schemaFlag + 1]);
    args.splice(schemaFlag, 2);
  } else {
    schemaPath = resourcePath(repoRoot, SCHEMA_REL);
  }
  let writerSessionId = process.env.OPSX_CHANGE_WRITER_SESSION || null;
  const writerFlag = args.indexOf("--writer-session");
  if (writerFlag >= 0) {
    writerSessionId = args[writerFlag + 1];
    args.splice(writerFlag, 2);
  }
  const freezeFlag = args.indexOf("--freeze");
  if (freezeFlag >= 0) {
    args.splice(freezeFlag, 1);
    const artifactPath = args[0];
    if (!artifactPath || !existsSync(artifactPath)) {
      process.stdout.write(
        JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) + "\n",
      );
      process.exit(1);
    }
    const frozen = freezeApplyDesignWithKnowledge({
      repoRoot,
      artifactPath,
      writerSessionId,
    });
    if (!frozen.ok) {
      process.stdout.write(JSON.stringify({ status: "FAIL", ...frozen }, null, 2) + "\n");
      process.exit(1);
    }
    const schema = existsSync(schemaPath)
      ? JSON.parse(readFileSync(schemaPath, "utf8"))
      : loadApplyDesignSchema(repoRoot);
    const artifact = JSON.parse(readFileSync(resolve(artifactPath), "utf8"));
    const validated = validateApplyDesignArtifact({ artifact, schema, repoRoot });
    process.stdout.write(
      JSON.stringify(
        {
          status: "PASS",
          mode: "freeze",
          validation_status: validated.ok ? "PASS" : "FAIL",
          knowledge_obligations: frozen.knowledge_obligations,
          matched_seam_ids: frozen.matched_seam_ids,
          error_count: validated.errors.length,
          errors: validated.errors,
        },
        null,
        2,
      ) + "\n",
    );
    process.exit(0);
  }
  const writerTransitionFlag = args.indexOf("--writer-transition");
  if (writerTransitionFlag >= 0) {
    args.splice(writerTransitionFlag, 1);
    let cohortManifestId = process.env.OPSX_COHORT_MANIFEST_ID || null;
    const manifestFlag = args.indexOf("--cohort-manifest-id");
    if (manifestFlag >= 0) {
      cohortManifestId = args[manifestFlag + 1];
      args.splice(manifestFlag, 2);
    }
    let changeRoot = null;
    const changeRootFlag = args.indexOf("--change-root");
    if (changeRootFlag >= 0) {
      changeRoot = args[changeRootFlag + 1];
      args.splice(changeRootFlag, 2);
    }
    const artifactPath = args[0];
    const published = publishApplyDesignWriterRecord({
      repoRoot,
      changeRoot,
      designPath: artifactPath,
      cohortManifestId,
      writerSessionId,
    });
    if (!published.ok) {
      process.stdout.write(JSON.stringify({ status: "FAIL", mode: "writer-transition", ...published }, null, 2) + "\n");
      process.exit(1);
    }
    process.stdout.write(
      JSON.stringify(
        {
          status: "PASS",
          mode: "writer-transition",
          phase: published.phase,
          idempotent: published.idempotent || false,
          path: published.path,
          writer_transition_digest: published.writer_record?.writer_transition_digest,
        },
        null,
        2,
      ) + "\n",
    );
    process.exit(0);
  }
  const refreshFlag = args.indexOf("--refresh-identity");
  if (refreshFlag >= 0) {
    args.splice(refreshFlag, 1);
    const rebind = args.includes("--rebind-downstream");
    if (rebind) args.splice(args.indexOf("--rebind-downstream"), 1);
    const artifactPath = args[0];
    if (!artifactPath || !existsSync(artifactPath)) {
      process.stdout.write(
        JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) +
          "\n",
      );
      process.exit(1);
    }
    const result = refreshApplyDesignIdentityFromDisk({
      repoRoot,
      artifactPath,
      rebindDownstream: rebind,
      writerSessionId,
    });
    if (result?.ok === false) {
      process.stdout.write(JSON.stringify({ status: "FAIL", ...result }, null, 2) + "\n");
      process.exit(1);
    }
    const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
    const artifact = JSON.parse(readFileSync(resolve(artifactPath), "utf8"));
    const validated = validateApplyDesignArtifact({ artifact, schema, repoRoot });
    process.stdout.write(
      JSON.stringify(
        {
          status: validated.ok ? "PASS" : "FAIL",
          mode: "refresh-identity",
          delta: result.delta,
          rebound: result.rebound,
          design_artifact_sha256: result.design_artifact_sha256,
          protected_context_identity: result.protected_context_identity,
          process_contract_identity: result.process_contract_identity,
          error_count: validated.errors.length,
          errors: validated.errors,
        },
        null,
        2,
      ) + "\n",
    );
    process.exit(validated.ok ? 0 : 1);
  }
  const artifactPath = args[0];
  if (!artifactPath) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "missing_path" }] }) +
        "\n",
    );
    process.exit(1);
  }
  if (!existsSync(schemaPath)) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "schema_missing" }] }) +
        "\n",
    );
    process.exit(1);
  }
  if (!existsSync(artifactPath)) {
    process.stdout.write(
      JSON.stringify({ status: "FAIL", errors: [{ code: "artifact_missing" }] }) +
        "\n",
    );
    process.exit(1);
  }
  const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
  const result = validateApplyDesignArtifact({ artifact, schema, repoRoot });
  const out = {
    status: result.ok ? "PASS" : "FAIL",
    artifact_status: artifact.status,
    error_count: result.errors.length,
    errors: result.errors,
  };
  process.stdout.write(JSON.stringify(out, null, 2) + "\n");
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
