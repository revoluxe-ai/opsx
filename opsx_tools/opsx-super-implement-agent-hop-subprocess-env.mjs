/**
 * Strict subprocess environment for agent hop live SDK (untrusted agent process).
 *
 * Owner HMAC verification runs only in the trusted Node controller; signing
 * secrets must never reach the sandboxed Python/SDK subprocess.
 */
import { join } from "node:path";

/** Keys explicitly passed through when present on the host (SDK/runtime only). */
export const AGENT_HOP_SDK_ENV_ALLOWLIST = Object.freeze([
  "PATH",
  "HOME",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "TZ",
  "TMPDIR",
  "USER",
  "LOGNAME",
  "CURSOR_API_KEY",
  "PYTHONUTF8",
]);

/** Never forward to the agent subprocess (exact keys). */
export const AGENT_HOP_SDK_ENV_FORBIDDEN_EXACT = Object.freeze([
  "SUPER_IMPLEMENT_OWNER_AUTHORITY_HMAC_SECRET",
  "OPSX_AGENT_HOP_TEST_HOOKS",
]);

/** Prefixes that must never appear in the agent subprocess environment. */
export const AGENT_HOP_SDK_ENV_FORBIDDEN_PREFIXES = Object.freeze([
  "SUPER_IMPLEMENT_OWNER_",
]);

function isForbiddenEnvKey(key) {
  const k = String(key || "");
  if (AGENT_HOP_SDK_ENV_FORBIDDEN_EXACT.includes(k)) return true;
  for (const prefix of AGENT_HOP_SDK_ENV_FORBIDDEN_PREFIXES) {
    if (k.startsWith(prefix)) return true;
  }
  if (k.includes("HMAC_SECRET") || k.includes("AUTHORITY_HMAC")) return true;
  return false;
}

/**
 * Build allowlisted env for sandboxed SDK subprocess.
 */
/**
 * @param {object} opts
 * @param {boolean} [opts.osSandboxEnforced] Set only when Node controller spawned bwrap sandbox.
 */
export function buildAgentHopSdkSubprocessEnv({
  isolatedRoot,
  hostEnv = process.env,
  osSandboxEnforced = false,
} = {}) {
  const out = {
    PYTHONPATH: join(isolatedRoot, "tools", "cursor_executor"),
    PYTHONUTF8: "1",
  };
  if (osSandboxEnforced) {
    out.AGENT_HOP_OS_SANDBOX_ENFORCED = "bwrap";
  }
  for (const key of AGENT_HOP_SDK_ENV_ALLOWLIST) {
    if (key === "PYTHONUTF8") continue;
    const val = hostEnv[key];
    if (val !== undefined && val !== "") out[key] = val;
  }
  for (const key of Object.keys(out)) {
    if (isForbiddenEnvKey(key)) delete out[key];
  }
  return out;
}

/**
 * Assert no Owner authority / signing material is present in a subprocess env map.
 */
export function assertAgentHopSubprocessEnvSecretSafe(env) {
  for (const key of Object.keys(env || {})) {
    if (isForbiddenEnvKey(key)) {
      return { ok: false, code: "AGENT_HOP_SUBPROCESS_ENV_SECRET_LEAK", key };
    }
  }
  const forbiddenVal = process.env.SUPER_IMPLEMENT_OWNER_AUTHORITY_HMAC_SECRET;
  if (forbiddenVal) {
    for (const [key, val] of Object.entries(env || {})) {
      if (val === forbiddenVal) {
        return { ok: false, code: "AGENT_HOP_SUBPROCESS_ENV_SECRET_LEAK", key, via: "value" };
      }
    }
  }
  return { ok: true };
}
