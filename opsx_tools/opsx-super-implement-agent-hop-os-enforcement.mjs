/**
 * OS-enforced subprocess execution for agent hop live SDK (bubblewrap).
 *
 * Availability probe alone is insufficient; this module runs enforcement probes
 * and wraps SDK subprocess invocation when bwrap is configured.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { evaluateOsWriteIsolationAvailable } from "./opsx-super-implement-os-sandbox.mjs";

function bwrapBinary() {
  const forced = process.env.SUPER_IMPLEMENT_OS_SANDBOX_MODE;
  if (forced !== "bwrap") return null;
  const which = spawnSync("which", ["bwrap"], { encoding: "utf8" });
  if (which.status !== 0 || !which.stdout?.trim()) return null;
  return which.stdout.trim();
}

/**
 * Network inside bwrap: default isolates (--unshare-all). Owner host may opt into
 * `SUPER_IMPLEMENT_OS_SANDBOX_NETWORK=share_net` for SDK API egress only; requires
 * separate host connectivity proof (`proveSandboxNetworkConnectivity`).
 */
export function resolveAgentHopSandboxNetworkMode() {
  const mode = String(process.env.SUPER_IMPLEMENT_OS_SANDBOX_NETWORK || "isolated");
  return mode === "share_net" ? "share_net" : "isolated";
}

/**
 * Build bubblewrap argv: read-only host paths, rw isolated workspace only.
 */
export function buildAgentHopBwrapArgv({
  isolatedRoot,
  canonicalRepoRoot,
  command,
  commandArgs = [],
  networkMode = resolveAgentHopSandboxNetworkMode(),
}) {
  const iso = resolve(isolatedRoot);
  const canon = resolve(canonicalRepoRoot);
  const argv = [
    "--unshare-all",
    "--die-with-parent",
    "--new-session",
    `--chdir`,
    iso,
    "--ro-bind",
    "/usr",
    "/usr",
    "--ro-bind",
    "/lib",
    "/lib",
  ];
  if (existsSync("/lib64")) {
    argv.push("--ro-bind", "/lib64", "/lib64");
  }
  if (existsSync("/bin")) {
    argv.push("--ro-bind", "/bin", "/bin");
  }
  if (existsSync("/etc")) {
    argv.push("--ro-bind", "/etc", "/etc");
  }
  argv.push("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp");
  if (networkMode === "share_net") {
    argv.push("--share-net");
  }
  argv.push("--bind", iso, iso);
  argv.push("--");
  argv.push(command, ...commandArgs);
  return { binary: bwrapBinary(), argv, isolated_root: iso, canonical_repo_root: canon };
}

/**
 * Prove writable isolated path and prohibited canonical write outside bind.
 */
export function proveOsSandboxEnforcement({ isolatedRoot, canonicalRepoRoot }) {
  const binary = bwrapBinary();
  if (!binary) {
    return {
      ok: false,
      code: "OS_SANDBOX_ENFORCEMENT_UNAVAILABLE",
      recovery_contract: {
        code: "OS_SANDBOX_ENFORCEMENT_UNAVAILABLE",
        next_action: "set SUPER_IMPLEMENT_OS_SANDBOX_MODE=bwrap on Owner host with bubblewrap installed",
        resume_requires: ["bwrap_sandbox_enforced"],
      },
    };
  }
  const iso = resolve(isolatedRoot);
  mkdirSync(iso, { recursive: true });
  const allowed = join(iso, "opsx-sandbox-probe-allowed.txt");
  const forbidden = join(resolve(canonicalRepoRoot), "opsx-sandbox-probe-forbidden.txt");
  const allowedRel = "opsx-sandbox-probe-allowed.txt";
  const allowedProbe = buildAgentHopBwrapArgv({
    isolatedRoot: iso,
    canonicalRepoRoot,
    command: "touch",
    commandArgs: [allowedRel],
  });
  const allowRun = spawnSync(allowedProbe.binary, allowedProbe.argv, { encoding: "utf8" });
  if (allowRun.status !== 0) {
    return {
      ok: false,
      code: "OS_SANDBOX_ALLOWED_WRITE_FAILED",
      stderr: allowRun.stderr,
    };
  }
  if (!existsSync(allowed)) {
    return { ok: false, code: "OS_SANDBOX_ALLOWED_WRITE_NOT_PERSISTED" };
  }
  try {
    unlinkSync(allowed);
  } catch {
    /* ignore */
  }
  const forbidProbe = buildAgentHopBwrapArgv({
    isolatedRoot: iso,
    canonicalRepoRoot,
    command: "touch",
    commandArgs: [forbidden],
  });
  const forbidRun = spawnSync(forbidProbe.binary, forbidProbe.argv, { encoding: "utf8" });
  if (forbidRun.status === 0 && existsSync(forbidden)) {
    try {
      unlinkSync(forbidden);
    } catch {
      /* ignore */
    }
    return { ok: false, code: "OS_SANDBOX_PROHIBITED_WRITE_NOT_BLOCKED" };
  }
  return {
    ok: true,
    mode: "bwrap",
    enforcement_proven: true,
    network_mode: resolveAgentHopSandboxNetworkMode(),
  };
}

/**
 * Host-only: prove SDK egress when share_net is explicitly enabled. Not run in CI.
 */
export function proveSandboxNetworkConnectivity({ isolatedRoot, canonicalRepoRoot } = {}) {
  if (resolveAgentHopSandboxNetworkMode() !== "share_net") {
    return {
      ok: false,
      code: "SANDBOX_NETWORK_NOT_CONFIGURED",
      network_mode: "isolated",
      note: "--unshare-all blocks network unless SUPER_IMPLEMENT_OS_SANDBOX_NETWORK=share_net",
    };
  }
  const binary = bwrapBinary();
  if (!binary) {
    return { ok: false, code: "OS_SANDBOX_ENFORCEMENT_UNAVAILABLE" };
  }
  const probe = buildAgentHopBwrapArgv({
    isolatedRoot,
    canonicalRepoRoot,
    command: "python3",
    commandArgs: ["-c", "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)"],
    networkMode: "share_net",
  });
  const run = spawnSync(probe.binary, probe.argv, { encoding: "utf8", timeout: 15_000 });
  if (run.status !== 0) {
    return {
      ok: false,
      code: "SANDBOX_NETWORK_HOST_TEST_FAILED",
      stderr: run.stderr,
      network_mode: "share_net",
    };
  }
  return { ok: true, network_mode: "share_net", host_proven: true };
}

/**
 * Run subprocess inside OS sandbox; fail closed when enforcement unavailable.
 */
export function spawnSdkInOsSandbox({ isolatedRoot, canonicalRepoRoot, command, commandArgs, env }) {
  const avail = evaluateOsWriteIsolationAvailable();
  if (!avail.ok || avail.mode !== "bwrap") {
    return {
      ok: false,
      code: "OS_SANDBOX_ENFORCEMENT_UNAVAILABLE",
      subprocess_invoked: false,
    };
  }
  const proof = proveOsSandboxEnforcement({ isolatedRoot, canonicalRepoRoot });
  if (!proof.ok) {
    return { ok: false, code: proof.code, subprocess_invoked: false, proof };
  }
  const wrap = buildAgentHopBwrapArgv({
    isolatedRoot,
    canonicalRepoRoot,
    command,
    commandArgs,
  });
  if (!wrap.binary) {
    return { ok: false, code: "OS_SANDBOX_ENFORCEMENT_UNAVAILABLE", subprocess_invoked: false };
  }
  const proc = spawnSync(wrap.binary, wrap.argv, {
    encoding: "utf8",
    env,
    cwd: isolatedRoot,
  });
  return { ok: true, subprocess_invoked: true, proc, os_sandbox_enforced: true };
}
