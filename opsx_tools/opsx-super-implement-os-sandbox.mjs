/**
 * OS-level write isolation probe for Super Implement live dispatch.
 *
 * Cloud/dev VMs without bubblewrap/mount namespaces fail closed for LIVE.
 */
import { spawnSync } from "node:child_process";

/**
 * @returns {{ ok: true, mode: string } | { ok: false, code: string, recovery_contract: object }}
 */
export function evaluateOsWriteIsolationAvailable() {
  const forced = process.env.SUPER_IMPLEMENT_OS_SANDBOX_MODE;
  if (forced === "bwrap") {
    const which = spawnSync("which", ["bwrap"], { encoding: "utf8" });
    if (which.status === 0 && which.stdout?.trim()) {
      return { ok: true, mode: "bwrap", binary: which.stdout.trim() };
    }
    return {
      ok: false,
      code: "OS_SANDBOX_BWRAP_UNAVAILABLE",
      recovery_contract: {
        code: "OS_SANDBOX_BWRAP_UNAVAILABLE",
        next_action: "install bubblewrap or run on Owner host with proven sandbox",
        resume_requires: ["os_mount_isolation_proven"],
      },
    };
  }
  const mount = spawnSync("unshare", ["-m", "true"], { encoding: "utf8" });
  if (mount.status === 0) {
    return { ok: true, mode: "unshare_mount" };
  }
  return {
    ok: false,
    code: "OS_MOUNT_ISOLATION_UNAVAILABLE",
    node_hook_only: true,
    recovery_contract: {
      code: "OS_MOUNT_ISOLATION_UNAVAILABLE",
      next_action:
        "Super Implement LIVE requires OS-level per-stage writable mounts (bwrap/unshare); node fs hook is not sufficient",
      resume_requires: ["os_mount_isolation_proven"],
    },
  };
}
