#!/usr/bin/env node
/**
 * OPSX fork of vendor `cursor-sdk-bridge`: async sqlite store bind via SqliteLocalAgentStore.open.
 */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { configureSqliteLocalStoreFromProto } from "./configure-sqlite-local-store-opsx.mjs";

const vendorRoot = process.env.OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT;
const vendorDist = process.env.OPSX_CURSOR_SDK_BRIDGE_VENDOR_DIST;
if (!vendorRoot?.trim() || !vendorDist?.trim()) {
  process.stderr.write(
    "cursor-sdk-bridge-opsx: OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT and OPSX_CURSOR_SDK_BRIDGE_VENDOR_DIST are required\n",
  );
  process.exit(1);
}

function vendorModule(subpath) {
  return pathToFileURL(join(vendorRoot, "node_modules", subpath)).href;
}

const { LocalAgentStoreConfig } = await import(
  vendorModule("@anysphere/proto/dist/generated/sdk/v1/sdk_messages_pb.js"),
);
const { configureCursorSdk } = await import(
  vendorModule("@cursor/sdk/dist/esm/index.js"),
);

const { createLocalAgentStoreFromProtoConfig } = await import(
  pathToFileURL(join(vendorDist, "bridge-local-agent-store.js")).href,
);
const { parseArgs, parsePort } = await import(
  pathToFileURL(join(vendorDist, "cli-args.js")).href,
);
const {
  installProcessErrorSurvivors,
  SURVIVE_UNCAUGHT_ENV_VAR,
  shouldInstallProcessErrorSurvivors,
} = await import(pathToFileURL(join(vendorDist, "process-error-survivors.js")).href);
const { RPC_DEBUG_LOG_ENV_VAR, shouldEnableRpcDebugLog } = await import(
  pathToFileURL(join(vendorDist, "rpc-debug-log.js")).href
);
const { startCursorSdkBridgeServer } = await import(
  pathToFileURL(join(vendorDist, "server.js")).href,
);
const {
  readBridgeStoreCallbackConfigFromEnv,
  setBridgeStoreCallbackConfig,
} = await import(pathToFileURL(join(vendorDist, "store-callback-config.js")).href);
const {
  readBridgeToolCallbackConfigFromEnv,
  setBridgeToolCallbackConfig,
} = await import(pathToFileURL(join(vendorDist, "tool-callback-config.js")).href);

const READY_LINE_PREFIX = "cursor-sdk-bridge ready ";

async function main() {
  if (shouldInstallProcessErrorSurvivors(process.env)) {
    installProcessErrorSurvivors();
    process.stderr.write(
      `cursor-sdk-bridge: ${SURVIVE_UNCAUGHT_ENV_VAR}=1 — installing process-level error survivors (band-aid mode)\n`,
    );
  }
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(helpText());
    return;
  }
  const port = parsePort(args.port ?? process.env.CURSOR_SDK_BRIDGE_PORT, "port");
  const host = args.host ?? process.env.CURSOR_SDK_BRIDGE_HOST ?? "127.0.0.1";
  const workspaceRef = args.workspace
    ? resolve(args.workspace)
    : process.env.CURSOR_SDK_BRIDGE_WORKSPACE
      ? resolve(process.env.CURSOR_SDK_BRIDGE_WORKSPACE)
      : undefined;
  const cliStateRoot = args.stateRoot
    ? resolve(args.stateRoot)
    : process.env.CURSOR_SDK_BRIDGE_STATE_ROOT
      ? resolve(process.env.CURSOR_SDK_BRIDGE_STATE_ROOT)
      : undefined;

  setBridgeStoreCallbackConfig(
    readStoreCallbackConfigFromArgs(args) ?? readBridgeStoreCallbackConfigFromEnv(),
  );
  setBridgeToolCallbackConfig(
    readToolCallbackConfigFromArgs(args) ?? readBridgeToolCallbackConfigFromEnv(),
  );

  await configureDefaultLocalStore(args.localStore ?? process.env.CURSOR_SDK_LOCAL_STORE, {
    workspaceRef,
    cliStateRoot,
  });

  const server = await startCursorSdkBridgeServer({
    port,
    host,
    workspaceRef,
    stateRoot: cliStateRoot,
    maxConcurrentAgents: args.maxConcurrentAgents,
    maxMessageBytes: args.maxMessageBytes,
    logRpcs: shouldEnableRpcDebugLog(process.env, args.verbose),
  });
  const authTokenFile = writeAuthTokenFile(server.address.authToken);
  const readyAddress = {
    schemaVersion: server.address.schemaVersion,
    serverVersion: server.address.serverVersion,
    pid: server.address.pid,
    transport: server.address.transport,
    protocol: server.address.protocol,
    host: server.address.host,
    port: server.address.port,
    url: server.address.url,
    authTokenFile,
    workspaceRef: server.address.workspaceRef,
    stateRoot: server.address.stateRoot,
    maxConcurrentAgents: server.address.maxConcurrentAgents,
    maxMessageBytes: server.address.maxMessageBytes,
  };
  process.stderr.write(`${READY_LINE_PREFIX}${JSON.stringify(readyAddress)}\n`);

  const shutdown = async (signal) => {
    process.stderr.write(`cursor-sdk-bridge ${signal} received, shutting down\n`);
    try {
      await server.close();
    } catch (err) {
      process.stderr.write(
        `cursor-sdk-bridge ${signal} shutdown error: ${err instanceof Error ? err.stack || err.message : String(err)}\n`,
      );
      process.exit(1);
    }
    process.exit(0);
  };
  process.once("SIGINT", () => {
    void shutdown("SIGINT");
  });
  process.once("SIGTERM", () => {
    void shutdown("SIGTERM");
  });
}

main().catch((error) => {
  process.stderr.write(
    `cursor-sdk-bridge failed: ${error instanceof Error ? error.stack || error.message : String(error)}\n`,
  );
  process.exit(1);
});

function readStoreCallbackConfigFromArgs(args) {
  const url = args.storeCallbackUrl?.trim() ?? process.env.CURSOR_SDK_STORE_CALLBACK_URL?.trim();
  const authToken =
    args.storeCallbackAuthToken?.trim() ??
    process.env.CURSOR_SDK_STORE_CALLBACK_AUTH_TOKEN?.trim() ??
    process.env.CURSOR_SDK_STORE_CALLBACK_TOKEN?.trim();
  if (!url && !authToken) {
    return undefined;
  }
  if (!url || !authToken) {
    throw new Error(
      "Store callback configuration requires both URL and auth token",
    );
  }
  return { url, authToken };
}

function readToolCallbackConfigFromArgs(args) {
  const url = args.toolCallbackUrl?.trim() ?? process.env.CURSOR_SDK_TOOL_CALLBACK_URL?.trim();
  const authToken =
    args.toolCallbackAuthToken?.trim() ??
    process.env.CURSOR_SDK_TOOL_CALLBACK_AUTH_TOKEN?.trim() ??
    process.env.CURSOR_SDK_TOOL_CALLBACK_TOKEN?.trim();
  if (!url && !authToken) {
    return undefined;
  }
  if (!url || !authToken) {
    throw new Error(
      "Tool callback configuration requires both URL and auth token",
    );
  }
  return { url, authToken };
}

/**
 * @param {string | undefined} raw
 * @param {{ workspaceRef?: string, cliStateRoot?: string }} ctx
 */
async function configureDefaultLocalStore(raw, ctx) {
  const trimmed = raw?.trim();
  if (!trimmed) {
    return;
  }
  let config;
  try {
    config = LocalAgentStoreConfig.fromJsonString(trimmed, {
      ignoreUnknownFields: true,
    });
  } catch (error) {
    throw new Error(
      `Invalid --local-store JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (config.type === "sqlite") {
    await configureSqliteLocalStoreFromProto(config, {
      workspaceRef: ctx.workspaceRef ?? "",
      cliStateRoot: ctx.cliStateRoot,
    });
    return;
  }
  const store = createLocalAgentStoreFromProtoConfig(config);
  if (store) {
    configureCursorSdk({ local: { store } });
  }
}

function writeAuthTokenFile(authToken) {
  const dir = mkdtempSync(join(tmpdir(), "cursor-sdk-bridge-"));
  const path = join(dir, "auth-token");
  writeFileSync(path, authToken, { mode: 0o600 });
  return path;
}

function helpText() {
  return `Usage: cursor-sdk-bridge-opsx [options]

Same options as cursor-sdk-bridge; sqlite local.store rootDir binds SqliteLocalAgentStore.
`;
}
