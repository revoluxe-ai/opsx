import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Append-only break-glass debt / obligation journal (universal V2).
 *
 * Activation is not considered durable until DEBT_RECORDED and incident CONTINUE
 * rows exist; corrupt tails are compacted to valid JSON lines before append.
 *
 * Lock recovery: if `opsx/break-glass/debt.lock` remains after a crashed writer,
 * verify the owning PID is dead, then remove the lock file before retrying.
 * Never delete `debt.jsonl` to "fix" partial activation — replay universal activate.
 */
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
import { dirname, join } from "node:path";

export const DEBT_LEDGER_REL = "opsx/break-glass/debt.jsonl";
export const DEBT_LOCK_REL = "opsx/break-glass/debt.lock";
export const DEBT_EVENT_SCHEMA = "opsx.break_glass_debt_event";
export const ACTIVATION_STATE_DEBT_RECORDED = "DEBT_RECORDED";
export const ACTIVATION_STATE_TRANSACTION_COMPLETE = "ACTIVATION_TRANSACTION_COMPLETE";

export function debtLedgerPath(repoRoot) {
  return resourcePath(repoRoot, DEBT_LEDGER_REL);
}

function lockPath(repoRoot) {
  return resourcePath(repoRoot, DEBT_LOCK_REL);
}

function acquireLock(repoRoot, { timeoutMs = 5000 } = {}) {
  const lp = lockPath(repoRoot);
  try {
    mkdirSync(dirname(lp), { recursive: true });
  } catch {
    return { ok: false, code: "DEBT_LEDGER_LOCK_FAILED" };
  }
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
  return { ok: false, code: "DEBT_LEDGER_LOCK_TIMEOUT" };
}

function releaseLock(lock) {
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
 * Parse debt ledger lines; drops corrupt non-terminal lines and truncates corrupt tail.
 */
export function parseDebtLedgerContent(raw) {
  const lines = String(raw || "").split("\n");
  const events = [];
  let corruptLines = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line));
    } catch {
      corruptLines += 1;
    }
  }
  return { events, corruptLines };
}

/**
 * @returns {{ events: object[], corruptLines: number }}
 */
export function readDebtLedgerParsed(repoRoot) {
  const ledger = debtLedgerPath(repoRoot);
  if (!existsSync(ledger)) {
    return { events: [], corruptLines: 0 };
  }
  return parseDebtLedgerContent(readFileSync(ledger, "utf8"));
}

function ledgerHasDedupeKey(events, dedupeKey) {
  if (!dedupeKey) return false;
  return events.some((ev) => ev.dedupe_key === dedupeKey);
}

function lastNonEmptyLine(raw) {
  const lines = String(raw || "").split("\n");
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (lines[i].trim()) return lines[i];
  }
  return null;
}

function lineParsesAsJson(line) {
  if (!line) return true;
  try {
    JSON.parse(line);
    return true;
  } catch {
    return false;
  }
}

/**
 * True when on-disk ledger must be rewritten before append (corrupt tail, missing newline).
 */
export function debtLedgerNeedsCompaction(raw, parsed) {
  const text = String(raw || "");
  if ((parsed?.corruptLines || 0) > 0) return true;
  if (!text.trim()) return false;
  if (!text.endsWith("\n")) return true;
  const last = lastNonEmptyLine(text);
  return !lineParsesAsJson(last);
}

/**
 * Atomically replace debt.jsonl with serialized valid events (each line JSON + newline).
 */
export function writeDebtLedgerEventsAtomic(repoRoot, events) {
  const ledger = debtLedgerPath(repoRoot);
  mkdirSync(dirname(ledger), { recursive: true });
  const content =
    events.length > 0 ? `${events.map((ev) => JSON.stringify(ev)).join("\n")}\n` : "";
  const tmp = `${ledger}.tmp.${process.pid}`;
  try {
    const fd = openSync(tmp, "w");
    writeFileSync(fd, content, "utf8");
    fsyncSync(fd);
    closeSync(fd);
    renameSync(tmp, ledger);
    return { ok: true, path: ledger };
  } catch (err) {
    try {
      unlinkSync(tmp);
    } catch {
      /* ignore */
    }
    return { ok: false, code: "DEBT_LEDGER_REPAIR_FAILED", detail: String(err) };
  }
}

/**
 * Append one debt journal line under exclusive lock.
 */
export function appendDebtEvent(repoRoot, event, { dedupeKey = null } = {}) {
  const ledger = debtLedgerPath(repoRoot);
  const lock = acquireLock(repoRoot);
  if (!lock.ok) {
    return { ok: false, code: "DEBT_LEDGER_LOCK_FAILED" };
  }
  try {
    mkdirSync(dirname(ledger), { recursive: true });
    const raw = existsSync(ledger) ? readFileSync(ledger, "utf8") : "";
    const parsed = parseDebtLedgerContent(raw);
    const { events } = parsed;
    const needsCompact = debtLedgerNeedsCompaction(raw, parsed);
    if (dedupeKey && ledgerHasDedupeKey(events, dedupeKey)) {
      if (needsCompact) {
        const repaired = writeDebtLedgerEventsAtomic(repoRoot, events);
        if (!repaired.ok) return repaired;
      }
      const prior = events.find((ev) => ev.dedupe_key === dedupeKey);
      return {
        ok: true,
        path: ledger,
        event_id: prior?.event_id || event.event_id,
        deduped: true,
        ledger_repaired: needsCompact,
      };
    }
    const nextEvent = { ...event, dedupe_key: dedupeKey || event.event_id };
    const written = writeDebtLedgerEventsAtomic(repoRoot, [...events, nextEvent]);
    if (!written.ok) return written;
    return {
      ok: true,
      path: ledger,
      event_id: event.event_id,
      ledger_repaired: needsCompact,
    };
  } catch (err) {
    return { ok: false, code: "DEBT_LEDGER_WRITE_FAILED", detail: String(err) };
  } finally {
    releaseLock(lock);
  }
}

export function readDebtLedger(repoRoot) {
  return readDebtLedgerParsed(repoRoot).events;
}

/**
 * True when universal activation debt row exists for idempotency key.
 */
export function universalActivationDebtRecorded(repoRoot, idempotencyKey) {
  return readDebtLedger(repoRoot).some(
    (ev) =>
      ev.idempotency_key === idempotencyKey &&
      ev.activation_state === ACTIVATION_STATE_DEBT_RECORDED,
  );
}

/**
 * Durable activation transaction marker (debt + incident CONTINUE both succeeded).
 */
export function universalActivationTransactionComplete(repoRoot, idempotencyKey) {
  return readDebtLedger(repoRoot).some(
    (ev) =>
      ev.idempotency_key === idempotencyKey &&
      ev.activation_state === ACTIVATION_STATE_TRANSACTION_COMPLETE,
  );
}
