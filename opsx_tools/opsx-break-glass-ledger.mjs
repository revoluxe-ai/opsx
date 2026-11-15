import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Durable append-only incident ledger with exclusive lock (P1).
 */
import { randomUUID } from "node:crypto";
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

export const DEFAULT_INCIDENT_LEDGER_REL = "opsx/break-glass/incidents.jsonl";
export const LEDGER_LOCK_REL = "opsx/break-glass/incidents.lock";

export function incidentLedgerPath(repoRoot) {
  return resourcePath(repoRoot, DEFAULT_INCIDENT_LEDGER_REL);
}

function lockPath(repoRoot) {
  return resourcePath(repoRoot, LEDGER_LOCK_REL);
}

function acquireLedgerLock(repoRoot, { timeoutMs = 5000 } = {}) {
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
  return { ok: false, code: "LEDGER_LOCK_TIMEOUT" };
}

function releaseLedgerLock(lock) {
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

export function appendIncidentEvent(repoRoot, event, { dedupeKey = null } = {}) {
  const ledger = incidentLedgerPath(repoRoot);
  const lock = acquireLedgerLock(repoRoot);
  if (!lock.ok) {
    return { ok: false, code: "INCIDENT_LEDGER_LOCK_FAILED" };
  }
  try {
    mkdirSync(dirname(ledger), { recursive: true });
    if (dedupeKey && existsSync(ledger)) {
      const existing = readFileSync(ledger, "utf8");
      if (existing.includes(`"dedupe_key":"${dedupeKey}"`)) {
        return { ok: true, path: ledger, event_id: event.event_id, deduped: true };
      }
    }
    const line = `${JSON.stringify({ ...event, dedupe_key: dedupeKey || event.event_id })}\n`;
    const fd = openSync(ledger, existsSync(ledger) ? "a" : "w");
    writeFileSync(fd, line, "utf8");
    fsyncSync(fd);
    closeSync(fd);
    return { ok: true, path: ledger, event_id: event.event_id };
  } catch (err) {
    return { ok: false, code: "INCIDENT_LEDGER_WRITE_FAILED", detail: String(err) };
  } finally {
    releaseLedgerLock(lock);
  }
}

export function readIncidentLedger(repoRoot) {
  const ledger = incidentLedgerPath(repoRoot);
  if (!existsSync(ledger)) return [];
  return readFileSync(ledger, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export const STATE_CLOSED_VERIFIED = "CLOSED_VERIFIED";
export const STATE_CONTINUE_WITH_EXCEPTION = "CONTINUE_WITH_EXCEPTION";

export function findOpenIncidents(repoRoot, { changeId = null, executionId = null } = {}) {
  const events = reconstructIncidentState(repoRoot);
  return events.filter((ev) => {
    if (ev.incident_status === STATE_CLOSED_VERIFIED) return false;
    if (changeId && ev.change_id !== changeId) return false;
    if (executionId && ev.execution_id !== executionId) return false;
    return true;
  });
}

export function incidentAlreadyApplied(repoRoot, idempotencyKey) {
  const events = readIncidentLedger(repoRoot);
  return events.some(
    (ev) =>
      ev.idempotency_key === idempotencyKey &&
      ev.exception_state === STATE_CONTINUE_WITH_EXCEPTION,
  );
}

export function reconstructIncidentState(repoRoot) {
  const events = readIncidentLedger(repoRoot);
  const byIncident = new Map();
  for (const ev of events) {
    const prev = byIncident.get(ev.incident_id);
    if (!prev || String(ev.timestamp) >= String(prev.timestamp)) {
      byIncident.set(ev.incident_id, ev);
    }
  }
  return [...byIncident.values()].sort((a, b) =>
    String(a.timestamp).localeCompare(String(b.timestamp)),
  );
}
