import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * JSON Schema validation for Owner attestation and execution grant documents.
 *
 * Uses registry schemas (draft 2020-12 subset): oneOf, const, pattern, required,
 * additionalProperties, $ref to $defs.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const OWNER_ATTESTATION_SCHEMA_REL =
  "opsx_tools/registry/super_implement_owner_attestation.schema.json";
export const OWNER_EXECUTION_GRANT_SCHEMA_REL =
  "opsx_tools/registry/super_implement_owner_execution_grant.schema.json";

function schemaTypeOf(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

function resolveRef(rootSchema, ref) {
  if (!ref.startsWith("#/")) return null;
  const parts = ref.slice(2).split("/");
  let node = rootSchema;
  for (const p of parts) {
    node = node?.[p];
    if (node === undefined) return null;
  }
  return node;
}

/**
 * Validate a JSON value against a schema document (bounded draft 2020-12 subset).
 */
export function validateJsonSchemaDocument(value, schema, rootSchema = schema, at = "$") {
  const errors = [];
  if (schema.$ref) {
    const target = resolveRef(rootSchema, schema.$ref);
    if (!target) {
      errors.push({ at, code: "ref", detail: `unresolved ${schema.$ref}` });
      return errors;
    }
    return validateJsonSchemaDocument(value, target, rootSchema, at);
  }
  if (schema.oneOf) {
    const branchErrors = [];
    let anyPass = false;
    for (let i = 0; i < schema.oneOf.length; i += 1) {
      const branch = schema.oneOf[i];
      const errs = validateJsonSchemaDocument(value, branch, rootSchema, `${at}/oneOf[${i}]`);
      if (errs.length === 0) {
        anyPass = true;
        break;
      }
      branchErrors.push(...errs);
    }
    if (!anyPass) {
      errors.push({
        at,
        code: "oneOf",
        detail: "value did not match any permitted authority scope variant",
        branches: branchErrors.slice(0, 8),
      });
    }
    return errors;
  }

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
    errors.push({ at, code: "enum", detail: "value not in enum" });
  }

  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push({ at, code: "minLength", detail: `minLength ${schema.minLength}` });
    }
    if (schema.pattern) {
      const re = new RegExp(schema.pattern);
      if (!re.test(value)) {
        errors.push({ at, code: "pattern", detail: `pattern ${schema.pattern}` });
      }
    }
  }

  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push({ at, code: "minItems", detail: `minItems ${schema.minItems}` });
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push({ at, code: "maxItems", detail: `maxItems ${schema.maxItems}` });
    }
    if (schema.items) {
      value.forEach((item, i) => {
        errors.push(...validateJsonSchemaDocument(item, schema.items, rootSchema, `${at}[${i}]`));
      });
    }
  }

  if (value && typeof value === "object" && !Array.isArray(value)) {
    if (schema.minProperties !== undefined && Object.keys(value).length < schema.minProperties) {
      errors.push({ at, code: "minProperties", detail: `minProperties ${schema.minProperties}` });
    }
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
          errors.push(...validateJsonSchemaDocument(value[key], sub, rootSchema, `${at}.${key}`));
        }
      }
    }
  }

  return errors;
}

function loadSchema(repoRoot, rel) {
  const abs = resourcePath(repoRoot, rel);
  if (!existsSync(abs)) {
    return { ok: false, code: "OWNER_SCHEMA_FILE_MISSING", path: rel };
  }
  try {
    return { ok: true, schema: JSON.parse(readFileSync(abs, "utf8")) };
  } catch (e) {
    return { ok: false, code: "OWNER_SCHEMA_PARSE_ERROR", error: String(e?.message || e) };
  }
}

export function validateOwnerAttestationDocument(doc, repoRoot = DEFAULT_ROOT) {
  const loaded = loadSchema(repoRoot, OWNER_ATTESTATION_SCHEMA_REL);
  if (!loaded.ok) return loaded;
  const errors = validateJsonSchemaDocument(doc, loaded.schema);
  if (errors.length) {
    return {
      ok: false,
      code: "OWNER_ATTESTATION_SCHEMA_INVALID",
      schema_errors: errors,
    };
  }
  return { ok: true };
}

export function validateOwnerExecutionGrantDocument(grant, repoRoot = DEFAULT_ROOT) {
  const loaded = loadSchema(repoRoot, OWNER_EXECUTION_GRANT_SCHEMA_REL);
  if (!loaded.ok) return loaded;
  const errors = validateJsonSchemaDocument(grant, loaded.schema);
  if (errors.length) {
    return {
      ok: false,
      code: "OWNER_EXECUTION_GRANT_SCHEMA_INVALID",
      schema_errors: errors,
    };
  }
  return { ok: true };
}
