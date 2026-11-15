import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * OPSX Process Tech Debt V0 — PROCESS_DEBT_CAPTURE only.
 *
 * Долговой журнал: `opsx/runs/process-tech-debt/registry.jsonl` (RUNS_LOCAL_DURABLE,
 * локально в текущем worktree; не входит в process source snapshot).
 *
 * WORKFLOW_SELF_GUIDANCE — вне scope V0.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
export const DEFAULT_REPO_ROOT = defaultRepoRootFromToolFile(import.meta.url);

export const EVENT_SCHEMA = "opsx.process_tech_debt_event.v0";
export const REGISTRY_REL = "opsx/runs/process-tech-debt/registry.jsonl";
export const LOCK_REL = "opsx/runs/process-tech-debt/.registry.lock";
export const QUARANTINE_REL = "opsx/runs/process-tech-debt/registry.quarantine.jsonl";
export const PERSISTENCE_POLICY = "RUNS_LOCAL_DURABLE";

export const DEBT_STATES = Object.freeze({
  SUSPECTED: "SUSPECTED",
  OPEN: "OPEN",
  ROUTED: "ROUTED",
  IN_REPAIR: "IN_REPAIR",
  CLOSED_VERIFIED: "CLOSED_VERIFIED",
  PAUSED: "PAUSED",
  NO_DEBT_RECORDED: "NO_DEBT_RECORDED",
});

export const HANDOFF_MODES = Object.freeze({
  READ_ONLY_ANALYSIS: "READ_ONLY_ANALYSIS",
  APPLY_AUTHORIZED: "APPLY_AUTHORIZED",
});

const PLACEHOLDER_PATTERNS = [
  /ALLOWED_RUN_BLOCK_SHA256:\s*<[^>]+>/i,
  /ALLOWED_RUN_BLOCK_SHA256:\s*<unset>/i,
  /ALLOWED_RUN_BLOCK_SHA256:\s*$/i,
  /<PLACEHOLDER>/i,
  /\{\{[A-Z0-9_]+\}\}/,
];

const CONSUMER_CONTRACTS = Object.freeze({
  "/opsx-meta-fix-command-defects": {
    packet_status: ["FIX_PLAN_READY", "BLOCKED_NEEDS_EVIDENCE"],
    read_only_min: ["STATUS", "DEFECTS_TO_FIX"],
    apply_min: ["STATUS", "DEFECTS_TO_FIX", "FILES"],
    apply_status: ["FIX_PLAN_READY"],
    files_non_empty_for_apply: true,
  },
  "/opsx-meta-design-command": {
    packet_status: ["DESIGN_BRIEF_READY", "DESIGN_PROPOSAL_READY"],
    read_only_min: ["STATUS", "problem_statement"],
    apply_min: null,
    apply_status: [],
    files_non_empty_for_apply: false,
  },
  "/opsx-meta-analyze-pipeline": {
    packet_status: ["ANALYSIS_READY"],
    read_only_min: ["TOPIC_INTENT", "evidence_refs"],
    apply_min: null,
    apply_status: [],
    files_non_empty_for_apply: false,
  },
});

/**
 * @param {string} repoRoot
 */
export function registryPath(repoRoot) {
  return resourcePath(repoRoot, REGISTRY_REL);
}

function lockPath(repoRoot) {
  return resourcePath(repoRoot, LOCK_REL);
}

function quarantinePath(repoRoot) {
  return resourcePath(repoRoot, QUARANTINE_REL);
}

/**
 * @param {string} repoRoot
 * @param {{ timeoutMs?: number }} [opts]
 */
export function acquireRegistryLock(repoRoot, { timeoutMs = 8000 } = {}) {
  const lp = lockPath(repoRoot);
  mkdirSync(dirname(lp), { recursive: true });
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const fd = openSync(lp, "wx");
      writeFileSync(fd, `${process.pid}:${Date.now()}\n`);
      return { ok: true, fd, path: lp };
    } catch {
      /* spin */
    }
  }
  return { ok: false, code: "PTD_REGISTRY_LOCK_TIMEOUT" };
}

/** @param {{ ok?: boolean, fd?: number, path?: string }} lock */
export function releaseRegistryLock(lock) {
  if (!lock?.ok) return;
  try {
    closeSync(lock.fd);
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(lock.path);
  } catch {
    /* ignore */
  }
}

/**
 * @param {string} raw
 * @returns {{ events: object[], corruptLines: { lineNo: number, raw: string }[] }}
 */
export function parseRegistryContent(raw) {
  const lines = String(raw || "").split("\n");
  const events = [];
  const corruptLines = [];
  let lineNo = 0;
  for (const line of lines) {
    lineNo += 1;
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line));
    } catch {
      corruptLines.push({ lineNo, raw: line });
    }
  }
  return { events, corruptLines };
}

/**
 * @param {string} repoRoot
 */
export function readRegistry(repoRoot) {
  const path = registryPath(repoRoot);
  if (!existsSync(path)) {
    return { events: [], corruptLines: [], path };
  }
  const parsed = parseRegistryContent(readFileSync(path, "utf8"));
  return { ...parsed, path };
}

function nextSeq(events) {
  if (!events.length) return 0;
  return Math.max(...events.map((e) => Number(e.seq) || 0)) + 1;
}

function hasDedupeKey(events, dedupeKey) {
  if (!dedupeKey) return false;
  return events.some((ev) => ev.dedupe_key === dedupeKey);
}

/**
 * Атомарная запись полного содержимого журнала (fsync + rename + fsync).
 *
 * @param {string} repoRoot
 * @param {string} content
 */
export function writeRegistryAtomic(repoRoot, content) {
  const journal = registryPath(repoRoot);
  mkdirSync(dirname(journal), { recursive: true });
  const tmp = `${journal}.tmp.${process.pid}`;
  writeFileSync(tmp, content, "utf8");
  const tfd = openSync(tmp, "r+");
  fsyncSync(tfd);
  closeSync(tfd);
  renameSync(tmp, journal);
  const jfd = openSync(journal, "r+");
  fsyncSync(jfd);
  closeSync(jfd);
  return { ok: true, path: journal };
}

/**
 * @param {string} repoRoot
 * @param {object[]} events
 * @param {object} newEvent
 */
function appendEventUnderLock(repoRoot, events, newEvent) {
  const line = `${JSON.stringify(newEvent)}\n`;
  const prior = events.map((e) => JSON.stringify(e)).join("\n");
  const body = prior ? `${prior}\n${line.trim()}\n` : line;
  return writeRegistryAtomic(repoRoot, body);
}

/**
 * @param {string} s
 */
export function normalizeFingerprintPart(s) {
  return String(s || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

/**
 * @param {object} input
 */
export function computeRootFingerprint(input) {
  const parts = [
    normalizeFingerprintPart(input.violated_contract_id),
    normalizeFingerprintPart(input.primary_symptom),
    normalizeFingerprintPart(input.primary_affected_command_id),
    normalizeFingerprintPart(input.classification_primary),
  ];
  const digest = createHash("sha256").update(parts.join("|"), "utf8").digest("hex");
  return { root_fingerprint: digest, debt_id: `ptd-${digest.slice(0, 16)}` };
}

/**
 * @param {string[]} lines
 * @returns {{ facts: object[], inferences: object[], placeholders: boolean }}
 */
export function extractFactsAndInferences(lines) {
  const facts = [];
  const inferences = [];
  let placeholders = false;
  for (const raw of lines) {
    const line = String(raw);
    if (PLACEHOLDER_PATTERNS.some((re) => re.test(line))) {
      placeholders = true;
      facts.push({ kind: "placeholder_pattern", line });
    }
    if (/^STATUS:\s*/i.test(line)) {
      facts.push({ kind: "status_line", line });
    }
    if (/^NEXT_ACTION:\s*/i.test(line)) {
      facts.push({ kind: "next_action_line", line });
    }
    if (/^Command:\s*/i.test(line)) {
      facts.push({ kind: "command_line", line });
    }
  }
  const nextAction = facts.find((f) => f.kind === "next_action_line");
  const commandLine = facts.find((f) => f.kind === "command_line");
  if (placeholders) {
    inferences.push({
      kind: "violated_contract_id",
      value: "OPSX-CONSUMER-HANDOFF-PLACEHOLDER-01",
      confidence: "HIGH",
    });
    inferences.push({
      kind: "classification_primary",
      value: "PROCESS_DEFECT",
      confidence: "HIGH",
    });
  }
  if (nextAction?.line?.includes("/opsx-close-external-gap")) {
    inferences.push({
      kind: "primary_affected_command_id",
      value: "/opsx-close-external-gap",
      confidence: "MEDIUM",
    });
    inferences.push({
      kind: "primary_symptom",
      value: "stale_or_invalid_external_gap_handoff",
      confidence: "MEDIUM",
    });
  }
  if (commandLine) {
    const m = commandLine.line.match(/Command:\s*(\S+)/i);
    if (m) {
      inferences.push({
        kind: "primary_affected_command_id",
        value: m[1],
        confidence: "MEDIUM",
      });
    }
  }
  return { facts, inferences, placeholders };
}

/**
 * @param {object[]} inferences
 * @param {string} kind
 */
function inferenceValue(inferences, kind, fallback = "unknown") {
  const hit = inferences.find((i) => i.kind === kind);
  return hit?.value || fallback;
}

/**
 * @param {object} input
 */
export function classifyRegistration(input) {
  const { facts, inferences, placeholders } = extractFactsAndInferences(
    input.cited_output || [],
  );
  const force = Boolean(input.force_register);
  const statusLine = facts.find((f) => f.kind === "status_line")?.line || "";
  const isBlocked = /STATUS:\s*BLOCKED/i.test(statusLine);

  if (!placeholders && isBlocked && !force) {
    return {
      decision: "NO_DEBT_RECORDED",
      reason: "intentional_blocked_without_placeholder",
      facts,
      inferences,
    };
  }

  const hasHigh =
    inferences.some((i) => i.confidence === "HIGH") ||
    (placeholders && facts.length > 0);
  const hasMedium = inferences.some((i) => i.confidence === "MEDIUM");

  if (!hasHigh && !hasMedium && !force) {
    return {
      decision: "PAUSED_NEEDS_EVIDENCE",
      reason: "insufficient_fact_or_inference",
      facts,
      inferences,
    };
  }

  if (!hasHigh && !force) {
    return {
      decision: "SUSPECTED",
      reason: "owner_suspicion_low_confidence",
      facts,
      inferences,
    };
  }

  return {
    decision: "REGISTER",
    reason: force ? "owner_force_register" : "fact_backed",
    facts,
    inferences,
  };
}

/**
 * @param {object[]} events
 * @param {string} debtId
 */
export function deriveDebtSnapshot(events, debtId) {
  const related = events.filter((e) => e.debt_id === debtId);
  let state = DEBT_STATES.SUSPECTED;
  let root_fingerprint = null;
  let occurrences = 0;
  let lastHandoff = null;
  let closed = false;

  for (const ev of related) {
    if (ev.event_type === "debt_opened") {
      state = DEBT_STATES.OPEN;
      root_fingerprint = ev.payload?.root_fingerprint ?? root_fingerprint;
    }
    if (ev.event_type === "occurrence") occurrences += 1;
    if (ev.event_type === "handoff_emitted") {
      state = DEBT_STATES.ROUTED;
      lastHandoff = ev.payload?.handoff ?? null;
    }
    if (ev.event_type === "close_verified") {
      state = DEBT_STATES.CLOSED_VERIFIED;
      closed = true;
    }
    if (ev.event_type === "close_rejected") state = DEBT_STATES.OPEN;
  }

  return {
    debt_id: debtId,
    state,
    root_fingerprint,
    occurrence_count: occurrences,
    last_handoff: lastHandoff,
    closed,
  };
}

/**
 * @param {object} packet
 * @param {string} route
 * @param {string} mode
 */
export function validateConsumerHandoff(packet, route, mode) {
  const contract = CONSUMER_CONTRACTS[route];
  if (!contract) {
    return {
      ok: false,
      handoff_status: "PAUSED_CONSUMER_GAP",
      mode,
      recovery_contract: {
        code: "UNKNOWN_CONSUMER_ROUTE",
        next_action: "REQUIRED_EXTERNAL_FIX: register consumer contract",
      },
    };
  }

  const status = packet?.STATUS;
  if (!status || !contract.packet_status.includes(status)) {
    return {
      ok: false,
      handoff_status: "PAUSED_CONSUMER_GAP",
      mode,
      recovery_contract: {
        code: "INVALID_PACKET_STATUS",
        required_status: contract.packet_status,
      },
    };
  }

  const minFields =
    mode === HANDOFF_MODES.APPLY_AUTHORIZED ? contract.apply_min : contract.read_only_min;
  if (!minFields) {
    return {
      ok: true,
      handoff_status: "EXECUTABLE_READY",
      mode,
      executable_apply: false,
    };
  }

  for (const field of minFields) {
    if (packet[field] === undefined || packet[field] === null) {
      return {
        ok: false,
        handoff_status: "PAUSED_CONSUMER_GAP",
        mode,
        recovery_contract: {
          code: "MISSING_REQUIRED_FIELD",
          field,
          mode,
        },
      };
    }
  }

  if (mode === HANDOFF_MODES.APPLY_AUTHORIZED) {
    if (!contract.apply_status.includes(status)) {
      return {
        ok: false,
        handoff_status: "PAUSED_CONSUMER_GAP",
        mode,
        executable_apply: false,
        recovery_contract: {
          code: "APPLY_NOT_AUTHORIZED_FOR_STATUS",
          status,
        },
      };
    }
    if (contract.files_non_empty_for_apply) {
      const files = packet.FILES;
      if (!Array.isArray(files) || files.length === 0) {
        return {
          ok: false,
          handoff_status: "PAUSED_CONSUMER_GAP",
          mode,
          executable_apply: false,
          recovery_contract: {
            code: "APPLY_FILES_REQUIRED",
            next_action: "/opsx-meta-analyze-pipeline",
          },
        };
      }
    }
    return {
      ok: true,
      handoff_status: "EXECUTABLE_READY",
      mode,
      executable_apply: true,
    };
  }

  return {
    ok: true,
    handoff_status: "EXECUTABLE_READY",
    mode,
    executable_apply: false,
  };
}

/**
 * @param {object} ctx
 */
export function buildHandoffPacket(ctx) {
  const route = ctx.repair_route || "/opsx-meta-fix-command-defects";
  const defectId = ctx.debt_id || "ptd-unknown";

  if (route === "/opsx-meta-analyze-pipeline") {
    return {
      STATUS: "ANALYSIS_READY",
      TOPIC_INTENT: "PROCESS_PIPELINE",
      evidence_refs: ctx.facts?.map((f) => f.line) || [],
      debt_id: defectId,
    };
  }

  if (route === "/opsx-meta-design-command") {
    return {
      STATUS: "DESIGN_BRIEF_READY",
      problem_statement: ctx.primary_symptom || "process debt",
      debt_id: defectId,
    };
  }

  const files = ctx.bounded_files || [];
  if (files.length === 0) {
    return {
      STATUS: "BLOCKED_NEEDS_EVIDENCE",
      DEFECTS_TO_FIX: [
        {
          id: defectId,
          CURRENT: ctx.primary_symptom,
          PROPOSED: "bounded FILES from analyze",
        },
      ],
      FILES: [],
      debt_id: defectId,
      NEXT_ACTION: "/opsx-meta-analyze-pipeline",
    };
  }

  return {
    STATUS: "FIX_PLAN_READY",
    DEFECTS_TO_FIX: [
      {
        id: defectId,
        CURRENT: ctx.primary_symptom,
        PROPOSED: ctx.proposed_behavior || "repair per defect packet",
      },
    ],
    FILES: files,
    debt_id: defectId,
  };
}

/**
 * Перенос повреждённых строк в quarantine (не silent delete).
 *
 * @param {string} repoRoot
 * @param {{ owner_recovery_token?: string }} [opts]
 */
export function recoverRegistryQuarantine(repoRoot, opts = {}) {
  if (opts.owner_recovery_token !== "OWNER_RECOVER_REGISTRY") {
    return {
      ok: false,
      code: "RECOVERY_TOKEN_REQUIRED",
      recovery_contract: {
        required_token: "OWNER_RECOVER_REGISTRY",
        quarantine_path: QUARANTINE_REL,
      },
    };
  }

  const lock = acquireRegistryLock(repoRoot);
  if (!lock.ok) return { ok: false, code: lock.code };

  try {
    const { events, corruptLines, path } = readRegistry(repoRoot);
    if (!corruptLines.length) {
      return { ok: true, recovered: false, message: "no_corrupt_lines" };
    }

    const qPath = quarantinePath(repoRoot);
    const qPrior = existsSync(qPath) ? readFileSync(qPath, "utf8") : "";
    const qAppend = corruptLines.map((c) => JSON.stringify({ ...c, quarantined_at: new Date().toISOString() })).join("\n");
    writeFileSync(qPath, qPrior + (qPrior ? "\n" : "") + qAppend + "\n", "utf8");

    const cleanBody = events.map((e) => JSON.stringify(e)).join("\n");
    writeRegistryAtomic(repoRoot, cleanBody ? `${cleanBody}\n` : "");

    const recoveryEvent = {
      schema: EVENT_SCHEMA,
      event_type: "registry_recovery",
      ts: new Date().toISOString(),
      seq: nextSeq(events),
      payload: {
        corrupt_line_count: corruptLines.length,
        quarantine_path: QUARANTINE_REL,
      },
    };
    appendEventUnderLock(repoRoot, events, recoveryEvent);

    return {
      ok: true,
      recovered: true,
      corrupt_line_count: corruptLines.length,
      path,
    };
  } finally {
    releaseRegistryLock(lock);
  }
}

/**
 * @param {string} repoRoot
 * @param {object} input
 */
export function registerProcessDebt(repoRoot, input) {
  const lock = acquireRegistryLock(repoRoot);
  if (!lock.ok) {
    return {
      ok: false,
      status: "REGISTRATION_PAUSED",
      code: lock.code,
      recovery_contract: { next_action: "retry after lock released" },
    };
  }

  try {
    const { events, corruptLines } = readRegistry(repoRoot);
    if (corruptLines.length > 0) {
      return {
        ok: false,
        status: "REGISTRY_PAUSED",
        code: "CORRUPT_REGISTRY_LINES",
        corrupt_line_count: corruptLines.length,
        recovery_contract: {
          code: "REGISTRY_RECOVERY_REQUIRED",
          message: "Corrupt lines must not be silently removed",
          action: "recoverRegistryQuarantine with OWNER_RECOVER_REGISTRY",
          corrupt_preview: corruptLines.slice(0, 3),
        },
      };
    }

    const classification = classifyRegistration(input);
    if (classification.decision === "NO_DEBT_RECORDED") {
      return { ok: true, status: "NO_DEBT_RECORDED", classification };
    }
    if (classification.decision === "PAUSED_NEEDS_EVIDENCE") {
      return {
        ok: true,
        status: "PAUSED_NEEDS_EVIDENCE",
        classification,
        recovery_contract: { next_action: "provide cited_output FACT lines or force_register" },
      };
    }

    const violated_contract_id = inferenceValue(
      classification.inferences,
      "violated_contract_id",
      input.violated_contract_id || "OPSX-PROCESS-DEBT-UNCLASSIFIED",
    );
    const primary_symptom = inferenceValue(
      classification.inferences,
      "primary_symptom",
      input.primary_symptom || "unspecified_symptom",
    );
    const primary_affected_command_id = inferenceValue(
      classification.inferences,
      "primary_affected_command_id",
      input.primary_affected_command_id || "unknown",
    );
    const classification_primary = inferenceValue(
      classification.inferences,
      "classification_primary",
      input.classification_primary || "PROCESS_DEFECT",
    );

    const { root_fingerprint, debt_id } = computeRootFingerprint({
      violated_contract_id,
      primary_symptom,
      primary_affected_command_id,
      classification_primary,
    });

    const registration_nonce = input.registration_nonce;
    const nonceKey = registration_nonce ? `nonce:${registration_nonce}` : null;
    if (nonceKey && hasDedupeKey(events, nonceKey)) {
      const prior = events.find((e) => e.dedupe_key === nonceKey);
      const priorDebtId = prior?.debt_id || debt_id;
      const snap = deriveDebtSnapshot(events, priorDebtId);
      return {
        ok: true,
        status: "REGISTERED",
        idempotent: true,
        debt_id: priorDebtId,
        snapshot: snap,
      };
    }

    const existingOpened = events.some(
      (e) => e.debt_id === debt_id && e.event_type === "debt_opened",
    );
    const seqBase = nextSeq(events);
    const ts = new Date().toISOString();
    const occurrencePayload = {
      change_id: input.change_id || null,
      execution_identity: input.execution_identity || null,
      revision_identity: input.revision_identity || null,
      fact_snapshot_hash: createHash("sha256")
        .update(JSON.stringify(classification.facts), "utf8")
        .digest("hex"),
    };

    const toAppend = [];
    if (!existingOpened) {
      toAppend.push({
        schema: EVENT_SCHEMA,
        event_type: "debt_opened",
        ts,
        seq: seqBase,
        debt_id,
        dedupe_key: `open:${debt_id}`,
        payload: {
          root_fingerprint,
          violated_contract_id,
          primary_symptom,
          primary_affected_command_id,
          classification_primary,
          revision_identity: input.revision_identity || null,
          facts: classification.facts,
          inferences: classification.inferences,
        },
      });
    }

    const occDedupe = nonceKey || `occ:${debt_id}:${occurrencePayload.fact_snapshot_hash}:${input.change_id || "na"}`;
    if (!hasDedupeKey(events, occDedupe)) {
      toAppend.push({
        schema: EVENT_SCHEMA,
        event_type: "occurrence",
        ts,
        seq: seqBase + (toAppend.length ? 1 : 0),
        debt_id,
        dedupe_key: occDedupe,
        payload: occurrencePayload,
      });
    }

    let working = [...events];
    for (const ev of toAppend) {
      appendEventUnderLock(repoRoot, working, ev);
      working = readRegistry(repoRoot).events;
    }

    const repair_route =
      input.repair_route ||
      (primary_affected_command_id.includes("close-external")
        ? "/opsx-meta-analyze-pipeline"
        : "/opsx-meta-fix-command-defects");

    const handoffPacket = buildHandoffPacket({
      debt_id,
      repair_route,
      primary_symptom,
      facts: classification.facts,
      bounded_files: input.bounded_files || [],
    });

    const readOnlyValidation = validateConsumerHandoff(
      handoffPacket,
      repair_route,
      HANDOFF_MODES.READ_ONLY_ANALYSIS,
    );
    const applyValidation = validateConsumerHandoff(
      handoffPacket,
      repair_route,
      HANDOFF_MODES.APPLY_AUTHORIZED,
    );

    const handoff = {
      repair_route,
      packet: handoffPacket,
      read_only: readOnlyValidation,
      apply: applyValidation,
      executable_ready:
        readOnlyValidation.ok &&
        readOnlyValidation.handoff_status === "EXECUTABLE_READY",
      executable_apply: applyValidation.ok && applyValidation.executable_apply === true,
    };

    const handoffEv = {
      schema: EVENT_SCHEMA,
      event_type: "handoff_emitted",
      ts: new Date().toISOString(),
      seq: nextSeq(readRegistry(repoRoot).events),
      debt_id,
      dedupe_key: `handoff:${debt_id}:${handoffPacket.STATUS}`,
      payload: { handoff },
    };
    appendEventUnderLock(repoRoot, readRegistry(repoRoot).events, handoffEv);

    const snapshot = deriveDebtSnapshot(readRegistry(repoRoot).events, debt_id);

    return {
      ok: true,
      status: "REGISTERED",
      debt_id,
      root_fingerprint,
      opened: !existingOpened,
      classification,
      handoff,
      snapshot,
      persistence_policy: PERSISTENCE_POLICY,
    };
  } finally {
    releaseRegistryLock(lock);
  }
}

/**
 * @param {string} repoRoot
 * @param {string} debtId
 */
export function readDebtById(repoRoot, debtId) {
  const { events, corruptLines } = readRegistry(repoRoot);
  if (corruptLines.length) {
    return {
      ok: false,
      code: "CORRUPT_REGISTRY_LINES",
      recovery_contract: { action: "OWNER_RECOVER_REGISTRY" },
    };
  }
  const snap = deriveDebtSnapshot(events, debtId);
  if (!events.some((e) => e.debt_id === debtId)) {
    return { ok: false, code: "DEBT_NOT_FOUND" };
  }
  return { ok: true, snapshot: snap, events: events.filter((e) => e.debt_id === debtId) };
}

/**
 * @param {string} repoRoot
 * @param {string} debtId
 * @param {object} closeEvidence
 */
export function attemptCloseDebt(repoRoot, debtId, closeEvidence) {
  if (closeEvidence?.self_report_fixed) {
    return {
      ok: false,
      code: "CLOSE_FORBIDDEN",
      reason: "self_report_fixed",
    };
  }

  if (closeEvidence?.independent === true && !closeEvidence?.provenance_ref) {
    return {
      ok: false,
      code: "CLOSE_FORBIDDEN",
      reason: "independent_flag_without_provenance",
    };
  }

  const lock = acquireRegistryLock(repoRoot);
  if (!lock.ok) return { ok: false, code: lock.code };

  try {
    const { events, corruptLines } = readRegistry(repoRoot);
    if (corruptLines.length) {
      return { ok: false, code: "CORRUPT_REGISTRY_LINES" };
    }

    const opened = events.find((e) => e.debt_id === debtId && e.event_type === "debt_opened");
    if (!opened) return { ok: false, code: "DEBT_NOT_FOUND" };

    const debtRevision = opened.payload?.revision_identity?.head_sha || opened.ts;
    const evidenceRevision = closeEvidence?.revision_identity?.head_sha || closeEvidence?.contract_ref?.blob_sha;
    if (
      evidenceRevision &&
      debtRevision &&
      evidenceRevision !== debtRevision &&
      !closeEvidence?.revision_identity?.newer_than_debt
    ) {
      const reject = {
        schema: EVENT_SCHEMA,
        event_type: "close_rejected",
        ts: new Date().toISOString(),
        seq: nextSeq(events),
        debt_id: debtId,
        payload: { reason: "CLOSE_REJECTED_STALE", closeEvidence },
      };
      appendEventUnderLock(repoRoot, events, reject);
      return { ok: false, code: "CLOSE_REJECTED_STALE" };
    }

    if (!closeEvidence?.regression_results?.all_pass) {
      const reject = {
        schema: EVENT_SCHEMA,
        event_type: "close_rejected",
        ts: new Date().toISOString(),
        seq: nextSeq(events),
        debt_id: debtId,
        payload: { reason: "MISSING_REGRESSION_PROOF", closeEvidence },
      };
      appendEventUnderLock(repoRoot, events, reject);
      return { ok: false, code: "CLOSE_FORBIDDEN", reason: "missing_regression_results" };
    }

    if (!closeEvidence?.contract_ref?.path) {
      return { ok: false, code: "CLOSE_FORBIDDEN", reason: "missing_contract_ref" };
    }

    const verified = {
      schema: EVENT_SCHEMA,
      event_type: "close_verified",
      ts: new Date().toISOString(),
      seq: nextSeq(events),
      debt_id: debtId,
      payload: {
        close_evidence: closeEvidence,
        contract_ref: closeEvidence.contract_ref,
        regression_results: closeEvidence.regression_results,
        provenance_ref: closeEvidence.provenance_ref,
      },
    };
    appendEventUnderLock(repoRoot, events, verified);
    return { ok: true, code: "CLOSED_VERIFIED" };
  } finally {
    releaseRegistryLock(lock);
  }
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--json" && argv[i + 1]) {
      out.json = argv[++i];
    } else if (a === "--debt-id" && argv[i + 1]) {
      out.debt_id = argv[++i];
    } else if (a === "--nonce" && argv[i + 1]) {
      out.nonce = argv[++i];
    } else if (!a.startsWith("-")) {
      out._.push(a);
    }
  }
  return out;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0];
  const repoRoot = process.cwd();

  if (cmd === "register") {
    const input = args.json ? JSON.parse(args.json) : {};
    if (args.nonce) input.registration_nonce = args.nonce;
    const result = registerProcessDebt(repoRoot, input);
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.ok ? 0 : 1);
  }

  if (cmd === "read") {
    const result = readDebtById(repoRoot, args.debt_id);
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.ok ? 0 : 1);
  }

  if (cmd === "recover") {
    const result = recoverRegistryQuarantine(repoRoot, {
      owner_recovery_token: "OWNER_RECOVER_REGISTRY",
    });
    console.log(JSON.stringify(result, null, 2));
    process.exit(result.ok ? 0 : 1);
  }

  console.error("Usage: register --json '{}' | read --debt-id ptd-... | recover");
  process.exit(2);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main();
}

/**
 * Hermetic helper: concurrent register spawn (tests).
 *
 * @param {string} repoRoot
 * @param {object} input
 */
export function spawnConcurrentRegister(repoRoot, input) {
  const script = fileURLToPath(import.meta.url);
  const child = spawnSync(
    process.execPath,
    [script, "register", "--json", JSON.stringify(input)],
    { cwd: repoRoot, encoding: "utf8" },
  );
  return {
    status: child.status,
    stdout: child.stdout,
    stderr: child.stderr,
  };
}
