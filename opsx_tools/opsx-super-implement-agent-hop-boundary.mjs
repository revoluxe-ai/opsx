import { resourcePath } from "./opsx-repo-root.mjs";
/**
 * Physical workspace boundary checks for agent hop V0.
 *
 * Copy-based isolation is not an OS sandbox; live SDK hops require proven OS isolation
 * plus post-authoring escape detection (symlinks, path traversal).
 */
import { existsSync, lstatSync, readdirSync, realpathSync } from "node:fs";
import { join, resolve } from "node:path";
import { evaluateOsWriteIsolationAvailable } from "./opsx-super-implement-os-sandbox.mjs";
import { proveOsSandboxEnforcement } from "./opsx-super-implement-agent-hop-os-enforcement.mjs";

/**
 * Live SDK MUTATING hops: require bwrap enforcement proof (not probe-only).
 */
export function enforceAgentHopLiveRuntimeBoundary({ isolatedRoot, canonicalRepoRoot } = {}) {
  const osIso = evaluateOsWriteIsolationAvailable();
  if (!osIso.ok || osIso.mode !== "bwrap") {
    return {
      ok: false,
      code: "AGENT_HOP_OS_ISOLATION_UNPROVEN",
      os_isolation: osIso,
      recovery_contract: osIso.recovery_contract || {
        code: "AGENT_HOP_OS_ISOLATION_UNPROVEN",
        next_action: "Owner host must provide bubblewrap-enforced sandbox",
        resume_requires: ["bwrap_sandbox_enforced"],
      },
    };
  }
  if (isolatedRoot && canonicalRepoRoot) {
    const proof = proveOsSandboxEnforcement({ isolatedRoot, canonicalRepoRoot });
    if (!proof.ok) {
      return {
        ok: false,
        code: proof.code,
        os_isolation: osIso,
        recovery_contract: proof.recovery_contract,
      };
    }
    return { ok: true, os_isolation: osIso, enforcement: proof };
  }
  return {
    ok: false,
    code: "OS_SANDBOX_ENFORCEMENT_NOT_PROVEN",
    recovery_contract: {
      code: "OS_SANDBOX_ENFORCEMENT_NOT_PROVEN",
      next_action: "enforce sandbox with isolated workspace before SDK launch",
      resume_requires: ["bwrap_sandbox_enforced"],
    },
  };
}

/**
 * Fail closed if workspace contains symlinks or resolves outside isolated root.
 */
export function scanWorkspaceEscapes(isolatedRoot, { changeRoot } = {}) {
  const root = resolve(isolatedRoot);
  const violations = [];
  const relRoots = [];
  const cr = changeRoot ? changeRoot.replace(/\\/g, "/") : null;
  if (cr && existsSync(resourcePath(root, cr))) relRoots.push(cr);
  if (existsSync(resourcePath(root, "app"))) relRoots.push("app");
  if (existsSync(resourcePath(root, "tests"))) relRoots.push("tests");
  if (existsSync(resourcePath(root, "opsx"))) relRoots.push("opsx");
  if (existsSync(resourcePath(root, "tools"))) relRoots.push("tools");

  function check(abs, rel) {
    let st;
    try {
      st = lstatSync(abs);
    } catch {
      return;
    }
    if (st.isSymbolicLink()) {
      violations.push({ code: "WORKSPACE_SYMLINK_FORBIDDEN", path: rel });
      return;
    }
    if (st.isDirectory()) {
      for (const name of readdirSync(abs)) {
        check(join(abs, name), `${rel}/${name}`);
      }
      return;
    }
    try {
      const rp = realpathSync(abs);
      if (!rp.startsWith(root + "/") && rp !== root) {
        violations.push({ code: "WORKSPACE_REALPATH_ESCAPE", path: rel, realpath: rp });
      }
    } catch {
      violations.push({ code: "WORKSPACE_REALPATH_UNRESOLVED", path: rel });
    }
  }

  for (const relRoot of relRoots) {
    check(resourcePath(root, relRoot), relRoot);
  }
  if (violations.length) {
    return {
      ok: false,
      code: "WORKSPACE_PHYSICAL_BOUNDARY_VIOLATED",
      violations,
      recovery_contract: {
        code: "WORKSPACE_PHYSICAL_BOUNDARY_VIOLATED",
        next_action: "discard hop workspace; agent escaped disposable boundary",
        resume_requires: ["agent_hop_restarted"],
      },
    };
  }
  return { ok: true, scanned_roots: relRoots };
}

/**
 * Ensure workspace was created without canonical TARGET symlinks (immutable toolchain copies only).
 */
export function assertToolchainNotCanonicalSymlink(isolatedRoot, canonicalRepoRoot) {
  const root = resolve(isolatedRoot);
  const canonical = resolve(canonicalRepoRoot);
  const forbidden = [".cursor", "opsx", "tools"];
  const violations = [];
  for (const name of forbidden) {
    const abs = resourcePath(root, name);
    if (!existsSync(abs)) continue;
    try {
      const st = lstatSync(abs);
      if (!st.isSymbolicLink()) continue;
      const target = realpathSync(abs);
      if (target.startsWith(canonical + "/") || target === canonical) {
        violations.push({ code: "CANONICAL_SYMLINK_TOOLCHAIN", path: name, target });
      }
    } catch {
      violations.push({ code: "CANONICAL_SYMLINK_UNRESOLVED", path: name });
    }
  }
  if (violations.length) {
    return { ok: false, code: "TARGET_ISOLATION_SYMLINK_DETECTED", violations };
  }
  return { ok: true };
}
