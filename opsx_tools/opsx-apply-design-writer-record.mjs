import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Запись APPLY-DESIGN-WRITER-RECORD после авторизованной мутации /opsx-apply-design.
 *
 * Потребляется recovery cohort (`assertRecoveryDesignSupersessionAuthorized`).
 * Не обходит HMAC owner decisions и не пишет APPLY-DESIGN-RECOVERY-SUPERSESSION.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  computeCanonicalApplyDesignIdentity,
  fileSha256,
  identityDigest,
} from "./opsx-apply-pipeline-identity.mjs";

export const APPLY_DESIGN_WRITER_RECORD_SCHEMA = "opsx.apply_design_writer_record";
export const AUTHORIZED_APPLY_DESIGN_COMMAND = "/opsx-apply-design";

export function applyDesignWriterRecordPath(repoRoot, changeRoot) {
  return join(resourcePath(repoRoot, changeRoot), "apply-design", "APPLY-DESIGN-WRITER-RECORD.json");
}

/**
 * Digest подтверждённого writer transition (привязка к canonical APPLY-DESIGN identity).
 *
 * @param {{ content_identity: string }} canonicalIdentity
 * @returns {string}
 */
export function computeApplyDesignWriterTransitionDigest(canonicalIdentity) {
  const ci = canonicalIdentity?.content_identity;
  if (!ci) {
    throw new TypeError("computeApplyDesignWriterTransitionDigest requires content_identity");
  }
  return identityDigest([{ path: "apply_design_content_identity", sha256: ci }]);
}

function atomicWriteJson(absPath, body) {
  mkdirSync(dirname(absPath), { recursive: true });
  const tmp = `${absPath}.${process.pid}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(body, null, 2)}\n`);
  renameSync(tmp, absPath);
}

/**
 * Идемпотентно зафиксировать writer transition после успешной мутации DESIGN.
 *
 * @param {{
 *   repoRoot: string,
 *   changeRoot: string,
 *   changeId: string,
 *   designArtifactAbsPath: string,
 *   design: object,
 *   primitive?: string,
 * }} opts
 */
export function persistApplyDesignWriterRecord({
  repoRoot,
  changeRoot,
  changeId,
  designArtifactAbsPath,
  design,
  primitive = "APPLY_DESIGN_MUTATION",
} = {}) {
  if (!repoRoot || !changeRoot || !designArtifactAbsPath || !design) {
    return { ok: false, code: "WRITER_RECORD_INPUT_MISSING" };
  }
  const canonical = computeCanonicalApplyDesignIdentity(design, {
    repoRoot,
    artifactSha256: fileSha256(designArtifactAbsPath),
  });
  if (!canonical.ok) {
    return { ok: false, code: "WRITER_RECORD_CANONICAL_IDENTITY_FAILED", detail: canonical.code };
  }
  const applyDesignSha256 = canonical.identity.artifact_sha256;
  const writerTransitionDigest = computeApplyDesignWriterTransitionDigest(canonical.identity);
  const path = applyDesignWriterRecordPath(repoRoot, changeRoot);
  if (existsSync(path)) {
    const existing = JSON.parse(readFileSync(path, "utf8"));
    if (
      existing.schema === APPLY_DESIGN_WRITER_RECORD_SCHEMA &&
      existing.apply_design_sha256 === applyDesignSha256 &&
      existing.writer_transition_confirmed === true &&
      existing.writer_transition_command === AUTHORIZED_APPLY_DESIGN_COMMAND
    ) {
      return { ok: true, idempotent: true, path, record: existing };
    }
    if (
      existing.apply_design_sha256 &&
      existing.apply_design_sha256 !== applyDesignSha256 &&
      existing.writer_transition_confirmed === true
    ) {
      return {
        ok: false,
        code: "APPLY_DESIGN_WRITER_RECORD_COLLISION",
        prior_sha256: existing.apply_design_sha256,
        next_sha256: applyDesignSha256,
      };
    }
  }
  const body = {
    schema: APPLY_DESIGN_WRITER_RECORD_SCHEMA,
    schema_version: 1,
    authorized_command: AUTHORIZED_APPLY_DESIGN_COMMAND,
    writer_transition_confirmed: true,
    writer_transition_command: AUTHORIZED_APPLY_DESIGN_COMMAND,
    writer_transition_digest: writerTransitionDigest,
    writer_transition_primitive: primitive,
    change_id: changeId || design.change_id,
    apply_design_sha256: applyDesignSha256,
    apply_design_content_identity: canonical.identity.content_identity,
    recorded_at: new Date().toISOString(),
  };
  atomicWriteJson(resolve(path), body);
  return { ok: true, path, record: body, canonical_identity: canonical.identity };
}
