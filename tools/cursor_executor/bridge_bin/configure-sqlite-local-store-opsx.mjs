/**
 * OPSX: wire bridge `local.store` type sqlite with explicit stateRoot (rootDir on wire).
 */
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const vendorRoot = process.env.OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT;
if (!vendorRoot?.trim()) {
  throw new Error("OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT is required");
}

function vendorModule(subpath) {
  return pathToFileURL(join(vendorRoot, "node_modules", subpath)).href;
}

const { configureCursorSdk } = await import(
  vendorModule("@cursor/sdk/dist/esm/index.js"),
);
const { SqliteLocalAgentStore } = await import(
  vendorModule("@cursor/sdk/dist/esm/sqlite.js"),
);

/**
 * @param {import("@anysphere/proto/sdk/v1/sdk_messages_pb.js").LocalAgentStoreConfig} config
 * @param {{ workspaceRef: string, cliStateRoot?: string }} ctx
 */
export async function configureSqliteLocalStoreFromProto(config, ctx) {
  if (!config?.type || config.type !== "sqlite") {
    return;
  }
  const workspaceRef = ctx.workspaceRef;
  if (!workspaceRef) {
    throw new Error(
      "OPSX sqlite local.store requires a resolved --workspace for SqliteLocalAgentStore.open",
    );
  }
  const rootDir = config.rootDir?.trim() || config.root_dir?.trim();
  const stateRoot = rootDir || ctx.cliStateRoot?.trim();
  if (!stateRoot) {
    return;
  }
  const store = await SqliteLocalAgentStore.open({
    workspaceRef,
    stateRoot,
  });
  configureCursorSdk({ local: { store } });
  process.stderr.write(
    `cursor-sdk-bridge-opsx: sqlite store stateRoot=${stateRoot} workspaceRef=${workspaceRef}\n`,
  );
}
