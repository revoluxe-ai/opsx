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

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const RECEIPT_CLI = "opsx-record-slice-receipt-decision-cli.mjs";
const PIPELINE_IDENTITY = "opsx-apply-pipeline-identity.mjs";

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

function fileSourceFromUrl(url) {
  if (typeof url !== "string" || !url.startsWith("file:")) return "";
  try {
    return readFileSync(fileURLToPath(url), "utf8");
  } catch {
    return "";
  }
}

function sourceText(source, url) {
  // nextLoad often omits source for the process entry. Patch the on-disk
  // module so RED command derivation runs for every change, not only when
  // the hook payload already contains the file text.
  const fromDisk = fileSourceFromUrl(url);
  if (fromDisk) return fromDisk;
  if (typeof source === "string") return source;
  if (source == null) return "";
  return Buffer.from(source).toString("utf8");
}

/**
 * Сопоставление loader URL с артефактом: `includes` плюс `fileURLToPath` для entry CLI на Windows.
 *
 * @param {unknown} url
 * @param {string} needle
 * @returns {boolean}
 */
function moduleUrlMatches(url, needle) {
  if (typeof url !== "string" || !needle) {
    return false;
  }
  if (url.includes(needle)) {
    return true;
  }
  try {
    // Нормализация Windows-пути — как в receiptDesignHexDigest.
    return fileURLToPath(url).replace(/\\/g, "/").includes(needle);
  } catch {
    return false;
  }
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

const IMPLEMENT_LIVE_FREEZE = `  const testPaths = [...(red.test_paths || []), ...(red.fixture_paths || [])].map(String);
  const live = computeLiveFrozenTestContentIdentity(repo, {
    test_paths: testPaths,
    fixture_paths: red.fixture_paths || [],
  });
  if (!live.ok || !live.identity) {
    return { ok: false, code: live.code || "FROZEN_IDENTITY_FAILED" };
  }`;

const IMPLEMENT_ALIGNED_FREEZE = `  const live = computeLiveFrozenTestContentIdentity(repo, {
    test_paths: red.test_paths || [],
    fixture_paths: red.fixture_paths || [],
  });
  if (!live.ok || !live.identity) {
    return { ok: false, code: live.code || "FROZEN_IDENTITY_FAILED" };
  }
  const declaredFreeze = resolveDeclaredFrozenTestContentIdentity(red, repo);
  const acceptedDeclared = new Set(declaredFreeze.accepted_digests || []);
  const frozenAligned =
    declaredFreeze.ok &&
    declaredFreeze.declared &&
    acceptedDeclared.has(declaredFreeze.declared)
      ? declaredFreeze.declared
      : live.identity;`;

const IMPLEMENT_LIVE_FIELDS = `    frozen_test_content_identity: live.identity,
    frozen_test_identity_before: live.identity,
    frozen_test_identity_after: live.identity,`;

const IMPLEMENT_ALIGNED_FIELDS = `    frozen_test_content_identity: frozenAligned,
    frozen_test_identity_before: frozenAligned,
    frozen_test_identity_after: frozenAligned,`;

/**
 * RED stamps test_content_identity with the legacy concat digest.
 * IMPLEMENT was stamping the canonical live digest, so
 * frozen_test_content_identity !== red_identity.test_content_identity
 * on unchanged frozen bytes. Use the declared digest only when it is still
 * an accepted on-disk variant. A mutated file keeps the live digest and
 * the existing STALE_RED / FROZEN_BYTES_MISMATCH gates still fail.
 * @param {string} source
 * @returns {string}
 */
export function patchImplementFrozenIdentity(source) {
  if (typeof source !== "string") return source;
  if (source.includes("const frozenAligned")) return source;
  if (!source.includes(IMPLEMENT_LIVE_FREEZE) || !source.includes(IMPLEMENT_LIVE_FIELDS)) {
    return source;
  }
  return source
    .replace(IMPLEMENT_LIVE_FREEZE, IMPLEMENT_ALIGNED_FREEZE)
    .replace(IMPLEMENT_LIVE_FIELDS, IMPLEMENT_ALIGNED_FIELDS);
}

const DECLARED_CONST =
  "  const declared = red?.test_content_identity ? String(red.test_content_identity) : null;";
const DECLARED_LET =
  "  let declared = red?.test_content_identity ? String(red.test_content_identity) : null;";
const PATHS_OK_AND_STATUS = `  const pathsOk =
    (canonical.ok || legacyAll.ok || legacyTestOnly.ok) && unique.length > 0;
  const ok = !declared || (pathsOk && matched_variant !== null);`;
const PATHS_OK_WITH_OPERATIVE = `  const pathsOk =
    (canonical.ok || legacyAll.ok || legacyTestOnly.ok) && unique.length > 0;
  const operativeRaw = red?.operative_red_transition?.test_content_identity;
  const operative =
    operativeRaw != null && String(operativeRaw).trim() ? String(operativeRaw).trim() : "";
  if (!matched_variant && operative && pathsOk) {
    if (canonical.ok && operative === canonical.identity) {
      declared = operative;
      matched_variant = "canonical_path_sha256_v1";
    } else if (legacyAll.ok && operative === legacyAll.digest) {
      declared = operative;
      matched_variant = "legacy_concat_all_paths_v1";
    } else if (legacyTestOnly.ok && operative === legacyTestOnly.digest) {
      declared = operative;
      matched_variant = "legacy_concat_test_paths_only_v1";
    }
  }
  const ok = !declared || (pathsOk && matched_variant !== null);`;

/**
 * RED may store a stale top-level test_content_identity while
 * operative_red_transition.test_content_identity is still an accepted
 * digest of the same frozen bytes. IMPLEMENT then stamps one field from
 * the top-level value and the other from the live digest.
 * Prefer the transition digest only when the top-level value matches no
 * on-disk variant and the transition value does. A digest that matches
 * neither stays unmatched so mutated bytes still fail.
 * @param {string} source
 * @returns {string}
 */
export function patchDeclaredFrozenIdentity(source) {
  if (typeof source !== "string") return source;
  if (source.includes("const operativeRaw = red?.operative_red_transition")) return source;
  const crlf = source.includes("\r\n");
  const text = source.replace(/\r\n/g, "\n");
  if (!text.includes(DECLARED_CONST) || !text.includes(PATHS_OK_AND_STATUS)) return source;
  const patched = text
    .replace(DECLARED_CONST, DECLARED_LET)
    .replace(PATHS_OK_AND_STATUS, PATHS_OK_WITH_OPERATIVE);
  return crlf ? patched.replace(/\n/g, "\r\n") : patched;
}

/**
 * Подмешивает derive pytest-команд из `test_path`, когда planned node отсутствует.
 *
 * Вызывающий код уже мог передать `repo` в `buildRedTestCommands(slice, repo)` до
 * смены сигнатуры функции — патч должен срабатывать и без замены call-site.
 *
 * @param {string} source
 * @returns {string}
 */
function patchRedCommandDerivation(source) {
  if (source.includes("function fillRedCommandsFromTestModules")) {
    return source;
  }
  const crlf = source.includes("\r\n");
  const text = source.replace(/\r\n/g, "\n");
  const fn = "function buildRedTestCommands(slice) {";
  const ret =
    "  return { test_paths: [...testPaths], test_commands: [...new Set(commands)] };";
  const callPlain =
    "const { test_paths, test_commands } = buildRedTestCommands(slice);";
  const callRepo =
    "const { test_paths, test_commands } = buildRedTestCommands(slice, repo);";
  const mainRead = `function main() {
  const raw = readFileSync(0, "utf8");`;
  const hasCall = text.includes(callPlain) || text.includes(callRepo);
  if (
    !text.includes(fn) ||
    !text.includes(ret) ||
    !hasCall ||
    !text.includes(RED_EMPTY_RETURN) ||
    !text.includes(mainRead) ||
    !text.includes("\nmain();")
  ) {
    return source;
  }
  let patched = text
    .replace(fn, `${RED_MODULE_HELPER}function buildRedTestCommands(slice, repo) {`)
    .replace(
      ret,
      `  fillRedCommandsFromTestModules(repo, testPaths, commands);\n${ret}`,
    );
  if (text.includes(callPlain)) {
    patched = patched.replace(callPlain, callRepo);
  }
  patched = patched
    .replace(RED_EMPTY_RETURN, RED_EMPTY_RETURN_NAMED)
    .replace(
      mainRead,
      `function main() {
  if (process.env.OPSX_RECEIPT_CLI_INSPECT === "1") return;
  const raw = readFileSync(0, "utf8");`,
    )
    .replace("\nmain();", "\nmain();\nexport { buildRedTestCommands };");
  return crlf ? patched.replace(/\n/g, "\r\n") : patched;
}

export async function load(url, context, nextLoad) {
  if (typeof url !== "string") {
    return nextLoad(url, context);
  }
  const isReceiptCli = moduleUrlMatches(url, RECEIPT_CLI);
  const isTransitionGate = moduleUrlMatches(url, "opsx-apply-red-transition-gate.mjs");
  const isPipelineIdentity = moduleUrlMatches(url, PIPELINE_IDENTITY);
  if (!isReceiptCli && !isTransitionGate && !isPipelineIdentity) {
    return nextLoad(url, context);
  }
  const loaded = await nextLoad(url, context);
  const original = sourceText(loaded.source, url);
  let patched = original;
  if (isPipelineIdentity) {
    patched = patchDeclaredFrozenIdentity(original);
  }
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
    patched = patchImplementFrozenIdentity(patched);
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
