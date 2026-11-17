/**
 * Fill receipt fields the design schema leaves optional or unnamed.
 *
 * APPLY DESIGN does not require repository_identity or protected_context_identity.
 * Slice receipts require 64-hex inspected_digest and protected_context_digest.
 * A missing optional field must not fail SCAFFOLD/RED/IMPLEMENT materialize.
 * A digest already present and 64 hex is left unchanged.
 *
 * RED commands are derived from planned `test_<name>` nodes when present.
 * Bindings that only name `test_path` (plus `red_test_targets_or_seams`) still
 * get one `poetry run pytest -q <path>::<node>` per top-level `def test_*`
 * in that module. Explicit node commands are left as-is. No nodes and no
 * planned command stays `RED_COMMANDS_EMPTY` and names the missing field.
 */

const RECEIPT_CLI = "opsx-record-slice-receipt-decision-cli.mjs";

const IDENTITY_FIELDS = `      planning_digest: design.planning_identity?.planning_digest,
      inspected_digest: design.repository_identity?.inspected_digest,
      protected_context_digest: design.protected_context_identity?.content_digest,`;

const IDENTITY_FIELDS_FILLED = `      planning_digest: receiptDesignHexDigest(
        design.planning_identity?.planning_digest,
        design.planning_identity?.inputs,
      ),
      inspected_digest: receiptDesignHexDigest(
        design.repository_identity?.inspected_digest,
        design.planning_identity?.inputs,
      ),
      protected_context_digest: receiptDesignHexDigest(
        design.protected_context_identity?.content_digest,
        [],
      ),`;

const HELPER = `function receiptDesignHexDigest(raw, entries) {
  if (typeof raw === "string" && /^[0-9a-fA-F]{64}$/.test(raw)) return raw;
  const rows = Array.isArray(entries) ? entries : [];
  const lines = rows
    .map((row) => ({
      path: String((row && row.path) || "").replace(/\\\\/g, "/"),
      sha256: String((row && row.sha256) || ""),
    }))
    .filter((row) => row.path)
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
    .map((row) => \`path=\${row.path}\\0sha256=\${row.sha256}\\n\`)
    .join("");
  return createHash("sha256").update(lines, "utf8").digest("hex");
}

`;

function sourceText(source) {
  if (typeof source === "string") return source;
  if (source == null) return "";
  return Buffer.from(source).toString("utf8");
}

const RED_MODULE_HELPER = String.raw`
function pytestNodeNamesInTestFile(repo, testPath) {
  if (!repo || !testPath) return [];
  const abs = resourcePath(repo, testPath);
  if (!abs || !existsSync(abs)) return [];
  let text = "";
  try {
    text = readFileSync(abs, "utf8");
  } catch {
    return [];
  }
  const nodes = [];
  const re = /^(?:async\s+)?def (test_[A-Za-z0-9_]+)\s*\(/gm;
  let match = re.exec(text);
  while (match) {
    nodes.push(match[1]);
    match = re.exec(text);
  }
  return nodes;
}

function redCommandCoversTestPath(command, testPath) {
  const cmd = String(command || "");
  const path = String(testPath || "");
  if (!path) return false;
  return (
    cmd.includes(" " + path + "::") ||
    cmd.endsWith(" " + path) ||
    cmd.includes(" " + path + " ")
  );
}

function fillRedCommandsFromTestModules(repo, testPaths, commands) {
  for (const testPath of testPaths) {
    let covered = false;
    for (const cmd of commands) {
      if (redCommandCoversTestPath(cmd, testPath)) {
        covered = true;
        break;
      }
    }
    if (covered) continue;
    if (!String(testPath).endsWith(".py")) continue;
    for (const node of pytestNodeNamesInTestFile(repo, testPath)) {
      commands.push("poetry run pytest -q " + testPath + "::" + node);
    }
  }
}

`;

const RED_EMPTY_RETURN = `  if (!test_commands.length) {
    return { ok: false, code: "RED_COMMANDS_EMPTY" };
  }`;

const RED_EMPTY_RETURN_NAMED = `  if (!test_commands.length) {
    return {
      ok: false,
      code: "RED_COMMANDS_EMPTY",
      message:
        "No pytest command: red_oracle_bindings need observed_intended_failures[].command or already_holding_not_failures as test_<name>, or test_path/red_test_targets_or_seams must be a .py module with top-level def test_<name>.",
    };
  }`;

/**
 * @param {string} source
 * @returns {string}
 */
const FILL_RED_ORACLE_FN = `export function fillRedOracleFromDesignBinding(oracle, status, slice, testPath) {
  if (!oracle || oracle.ok === false) return oracle;
  if (oracle.spec != null && oracle.behavior != null) return oracle;
  if (status !== "RED_PROVEN") return oracle;
  const bindings = Array.isArray(slice?.red_oracle_bindings) ? slice.red_oracle_bindings : [];
  const want = String(testPath || "").replace(/\\\\/g, "/");
  const binding = bindings.find(
    (row) => String(row?.test_path || "").replace(/\\\\/g, "/") === want,
  );
  const expected = binding?.authoritative_expected;
  if (expected === undefined || expected === null || String(expected).trim() === "") {
    return oracle;
  }
  return {
    ...oracle,
    spec: oracle.spec ?? String(expected).trim(),
    behavior: oracle.behavior ?? "NOT-MET",
  };
}

`;

const PRODUCT_SENTINEL_BLOCK = `  const productException =
    /(?:NotImplementedError|RuntimeError):\\s*OPSX_SCAFFOLD_NOT_IMPLEMENTED/.test(text);
  const productModuleFrame =
    /(?:^|[\\s'"(])(?:app|src)[\\\\/][\\w./\\\\-]+\\.py/.test(text);
  const legacyStroyFrame =
    /visual_covers\\.py/.test(text) ||
    /src[\\\\/]stroy[\\\\/][\\w./\\\\-]+\\.py/.test(text);
  const productRuntimeSentinel =
    productException && (productModuleFrame || legacyStroyFrame);
  if (testThrowsSentinel && !importsScaffold && !importsProductScaffoldModule) {
    return { qualifies: false, reason: "test_thrown_sentinel" };
  }
  if (
    productRuntimeSentinel &&
    !testThrowsSentinel &&
    (importsProductScaffoldModule || productModuleFrame || legacyStroyFrame)
  ) {
    return { qualifies: true, reason: "python_product_scaffold_runtime" };
  }
`;

/**
 * @param {string} source
 * @returns {string}
 */
export function patchTransitionGateSource(source) {
  if (typeof source !== "string") return source;
  if (source.includes("function fillRedOracleFromDesignBinding")) return source;
  const runtimeRaise =
    "/raise\\s+RuntimeError\\s*\\(\\s*['\"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['\"]\\s*\\)/.test(src);";
  const notImplRaise =
    "/raise\\s+NotImplementedError\\s*\\(\\s*['\"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['\"]\\s*\\)/.test(src) ||\n    " +
    runtimeRaise;
  if (source.includes(runtimeRaise) && !source.includes("raise\\s+NotImplementedError")) {
    source = source.replace(runtimeRaise, notImplRaise);
  }
  const anchor = "export function evaluateRedTransitionGate({";
  const oracleLine = "    const oracle = readRedOracleMarkers(repoRoot, testPath);";
  const sentinelStart = "  const productRuntimeSentinel =";
  const sentinelEnd =
    "  if (importsScaffold || /installSessionResumeHydrate|apply-scaffold/.test(text)) {";
  if (
    !source.includes(anchor) ||
    !source.includes(oracleLine) ||
    !source.includes(sentinelStart) ||
    !source.includes(sentinelEnd)
  ) {
    return source;
  }
  const start = source.indexOf(sentinelStart);
  const end = source.indexOf(sentinelEnd);
  if (start < 0 || end < 0 || start > end) return source;
  return source
    .slice(0, start)
    .concat(PRODUCT_SENTINEL_BLOCK)
    .concat(source.slice(end))
    .replace(
      oracleLine,
      `    let oracle = fillRedOracleFromDesignBinding(
      readRedOracleMarkers(repoRoot, testPath),
      status,
      linkedSlice,
      testPath,
    );`,
    )
    .replace(anchor, `${FILL_RED_ORACLE_FN}${anchor}`);
}

/**
 * @param {string} source
 * @returns {string}
 */
export function patchRedValidatorSpawn(source) {
  const old = 'const validateRun = spawnSync("node", [redValidator, outPath], {';
  if (typeof source !== "string" || !source.includes(old) || source.includes("redValidatorNodeArgs")) {
    return source;
  }
  const neu = `const redValidatorNodeArgs = [redValidator, outPath];
  const redImportFlag = process.execArgv.indexOf("--import");
  if (redImportFlag >= 0 && process.execArgv[redImportFlag + 1]) {
    redValidatorNodeArgs.unshift("--import", process.execArgv[redImportFlag + 1]);
  }
  const validateRun = spawnSync("node", redValidatorNodeArgs, {`;
  return source.replace(old, neu);
}

/**
 * @param {string} source
 * @returns {string}
 */
export function patchRedValidateErrors(source) {
  const old = "detail: (validateJson.errors || []).slice(0, 3),";
  if (typeof source !== "string" || !source.includes(old) || source.includes("errors: validateJson.errors")) {
    return source;
  }
  return source.replace(old, `${old}\n      errors: validateJson.errors || [],`);
}

function patchRedCommandDerivation(source) {
  if (source.includes("function fillRedCommandsFromTestModules")) {
    return source;
  }
  const fn = "function buildRedTestCommands(slice) {";
  const ret =
    "  return { test_paths: [...testPaths], test_commands: [...new Set(commands)] };";
  const call = "const { test_paths, test_commands } = buildRedTestCommands(slice);";
  const mainRead = `function main() {
  const raw = readFileSync(0, "utf8");`;
  if (
    !source.includes(fn) ||
    !source.includes(ret) ||
    !source.includes(call) ||
    !source.includes(RED_EMPTY_RETURN) ||
    !source.includes(mainRead) ||
    !source.includes("\nmain();")
  ) {
    return source;
  }
  return source
    .replace(fn, `${RED_MODULE_HELPER}function buildRedTestCommands(slice, repo) {`)
    .replace(
      ret,
      `  fillRedCommandsFromTestModules(repo, testPaths, commands);\n${ret}`,
    )
    .replace(call, "const { test_paths, test_commands } = buildRedTestCommands(slice, repo);")
    .replace(RED_EMPTY_RETURN, RED_EMPTY_RETURN_NAMED)
    .replace(
      mainRead,
      `function main() {
  if (process.env.OPSX_RECEIPT_CLI_INSPECT === "1") return;
  const raw = readFileSync(0, "utf8");`,
    )
    .replace("\nmain();", "\nmain();\nexport { buildRedTestCommands };");
}

export async function load(url, context, nextLoad) {
  if (typeof url !== "string") {
    return nextLoad(url, context);
  }
  const isReceiptCli = url.includes(RECEIPT_CLI);
  const isTransitionGate = url.includes("opsx-apply-red-transition-gate.mjs");
  if (!isReceiptCli && !isTransitionGate) {
    return nextLoad(url, context);
  }
  const loaded = await nextLoad(url, context);
  const original = sourceText(loaded.source);
  let patched = original;
  if (isTransitionGate) {
    patched = patchTransitionGateSource(original);
  }
  if (isReceiptCli) {
    if (
      patched.includes(IDENTITY_FIELDS) &&
      patched.includes("function buildApplyDesignIdentity") &&
      !patched.includes("function receiptDesignHexDigest")
    ) {
      patched = patched
        .replace(
          "function buildApplyDesignIdentity",
          `${HELPER}function buildApplyDesignIdentity`,
        )
        .replace(IDENTITY_FIELDS, IDENTITY_FIELDS_FILLED);
    }
    patched = patchRedCommandDerivation(patched);
    patched = patchRedValidatorSpawn(patched);
    patched = patchRedValidateErrors(patched);
  }
  if (patched === original) {
    return loaded;
  }
  return {
    format: "module",
    source: patched,
    shortCircuit: true,
  };
}
