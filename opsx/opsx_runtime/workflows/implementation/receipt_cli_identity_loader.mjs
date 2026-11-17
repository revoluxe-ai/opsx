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
 * in that module. When any node carries `opsx_red_behavior_value` or
 * `@opsx-red-behavior-value`, only those obligation nodes are RED commands;
 * unmarked siblings are preconditions and must not share the suite
 * (`RED_MIXED_SUITE`). `already_holding_not_failures` is omitted when the
 * same binding lists an intended failure. Explicit node commands are left
 * as-is. No nodes and no planned command stays `RED_COMMANDS_EMPTY` and
 * names the missing field.
 *
 * Prior RED executions are reusable only when the declared
 * `test_content_identity` is still an accepted digest of the frozen files
 * on disk. A stale digest must not be copied forward: that skips shape and
 * fails RED validate with `test_content_identity does not match on-disk
 * frozen tests` for every change whose tests moved after the last stamp.
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
function redNodeIsBehaviorObligation(text, defIndex) {
  const before = String(text || "").slice(0, defIndex).split(/\n/);
  const decor = [];
  for (let i = before.length - 1; i >= 0; i -= 1) {
    const line = before[i];
    if (line.trim() === "" || /^\s*@/.test(line) || /^\s*#/.test(line)) {
      decor.push(line);
      continue;
    }
    break;
  }
  let after = String(text || "").slice(defIndex);
  const nextFn = after.slice(1).search(/\r?\n(?:async\s+)?def test_[A-Za-z0-9_]+\s*\(/);
  if (nextFn >= 0) {
    after = after.slice(0, nextFn + 1);
  }
  const doc =
    after.match(/:\r?\n\s*"""([\s\S]*?)"""/) ||
    after.match(/:\r?\n\s*'''([\s\S]*?)'''/);
  const window = decor.join("\n") + "\n" + (doc ? doc[1] : "");
  return (
    /opsx_red_behavior_value/.test(window) ||
    /@opsx-red-behavior-value:/.test(window)
  );
}

function pytestNodeRecordsInTestFile(repo, testPath) {
  if (!repo || !testPath) return [];
  const abs = resourcePath(repo, testPath);
  if (!abs || !existsSync(abs)) return [];
  let text = "";
  try {
    text = readFileSync(abs, "utf8");
  } catch {
    return [];
  }
  const re = /^(?:async\s+)?def (test_[A-Za-z0-9_]+)\s*\(/gm;
  const found = [];
  let match = re.exec(text);
  while (match) {
    found.push({ name: match[1], index: match.index });
    match = re.exec(text);
  }
  return found.map((row) => ({
    name: row.name,
    behavior: redNodeIsBehaviorObligation(text, row.index),
  }));
}

function pytestNodeNamesInTestFile(repo, testPath) {
  return pytestNodeRecordsInTestFile(repo, testPath).map((row) => row.name);
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
    const records = pytestNodeRecordsInTestFile(repo, testPath);
    const behavior = records.filter((row) => row.behavior);
    const chosen = behavior.length ? behavior : records;
    for (const node of chosen) {
      commands.push("poetry run pytest -q " + testPath + "::" + node.name);
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
    /(?:NotImplementedError|RuntimeError):\\s*OPSX_SCAFFOLD_NOT_IMPLEMENTED/.test(text) ||
    /(?:NotImplementedError|RuntimeError)\\(\\s*['"]OPSX_SCAFFOLD_NOT_IMPLEMENTED['"]\\s*\\)/.test(text);
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
function receiptHookLiteral() {
  return JSON.stringify(new URL("./receipt_cli_identity_hook.mjs", import.meta.url).href);
}

/**
 * Nested `node opsx-apply-red.mjs` must load the same provenance hook.
 * `process.execArgv` often has no `--import` entry, so the child kept the
 * on-disk gate and downgraded app/ and src/ scaffold sentinels to
 * SCAFFOLD_DEFECT while the receipt said INTENDED_BEHAVIOR_MISSING.
 * @param {string} source
 * @returns {string}
 */
export function patchRedValidatorSpawn(source) {
  const old = 'const validateRun = spawnSync("node", [redValidator, outPath], {';
  if (typeof source !== "string" || !source.includes(old) || source.includes("redValidatorNodeArgs")) {
    return source;
  }
  const hook = receiptHookLiteral();
  const neu = `const redValidatorNodeArgs = ["--import", ${hook}, redValidator, outPath];
  const validateRun = spawnSync("node", redValidatorNodeArgs, {`;
  return source.replace(old, neu);
}

const SHAPE_SPAWN_OLD =
  '[shapeScript, "--repo", repo, "--change", changeId, "--slice", sliceId],';

/**
 * Shape stamps command_executions before the transition gate re-runs them.
 * It has to load the same hook or the two classifiers diverge.
 * @param {string} source
 * @returns {string}
 */
export function patchShapeCliSpawn(source) {
  if (typeof source !== "string" || !source.includes(SHAPE_SPAWN_OLD)) return source;
  const hook = receiptHookLiteral();
  const neu = `["--import", ${hook}, shapeScript, "--repo", repo, "--change", changeId, "--slice", sliceId],`;
  return source.replace(SHAPE_SPAWN_OLD, neu);
}

const SHAPE_IMPORT = 'import { runFrozenRedCommand } from "./opsx-apply-green.mjs";';
const SHAPE_IMPORT_WITH_GATE = `import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { classifyCommandExecutionFromOutputWithGate } from "./opsx-apply-red-transition-gate.mjs";`;

const SHAPE_CLASS_OLD = `    const live = runFrozenRedCommand(command, { cwd: repo, timeoutMs: 120000 });
    const pass = live.exit_code === 0;
    const classification =
      status === ALREADY_SATISFIED
        ? pass
          ? "ALREADY_SATISFIED"
          : "INTENDED_BEHAVIOR_MISSING"
        : pass
          ? "ALREADY_SATISFIED"
          : RED_PROVEN_CLASSIFICATION;
    commandExecutions.push({
      command,
      final_exit_code: live.exit_code,
      final_classification: classification,
      red_qualified: !pass && classification === RED_PROVEN_CLASSIFICATION,
      pass_qualified: pass && classification === ALREADY_SATISFIED,`;

const SHAPE_CLASS_NEW = String.raw`    const live = runFrozenRedCommand(command, { cwd: repo, timeoutMs: 120000 });
    const testPathMatch = String(command).match(/(\S+test_\S+\.py)(?:::|\s|$)/);
    const testPath = testPathMatch ? testPathMatch[1] : "";
    let testSource = "";
    if (testPath) {
      try {
        const abs = resourcePath(repo, testPath);
        if (abs) testSource = readFileSync(abs, "utf8");
      } catch {
        testSource = "";
      }
    }
    const liveClass = classifyCommandExecutionFromOutputWithGate({
      output: live.relevant_output,
      exitCode: live.exit_code,
      testSource,
    });
    const classification = String(liveClass.classification || "");
    commandExecutions.push({
      command,
      final_exit_code: live.exit_code,
      final_classification: classification,
      red_qualified: liveClass.red_qualified === true,
      pass_qualified: liveClass.pass_qualified === true,`;

/**
 * Shape must not label every non-zero exit INTENDED_BEHAVIOR_MISSING.
 * The transition gate reclassifies the same command; a blanket stamp
 * fails RED validate with classification mismatch for any other class.
 * @param {string} source
 * @returns {string}
 */
export function patchShapeRedClassification(source) {
  if (typeof source !== "string") return source;
  if (source.includes("classifyCommandExecutionFromOutputWithGate")) return source;
  const crlf = source.includes("\r\n");
  const text = source.replace(/\r\n/g, "\n");
  if (!text.includes(SHAPE_IMPORT) || !text.includes(SHAPE_CLASS_OLD)) return source;
  const patched = text
    .replace(SHAPE_IMPORT, SHAPE_IMPORT_WITH_GATE)
    .replace(SHAPE_CLASS_OLD, SHAPE_CLASS_NEW);
  return crlf ? patched.replace(/\n/g, "\r\n") : patched;
}

const FINALIZE_GATE_IMPORT =
  'import { runFrozenRedCommand } from "./opsx-apply-green.mjs";';
const FINALIZE_GATE_IMPORT_WITH_CLASSIFIER = `import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { classifyCommandExecutionFromOutputWithGate } from "./opsx-apply-red-transition-gate.mjs";`;

const FINALIZE_CLASSIFY_OLD = `    const cls = classifyCommandExecutionFromOutput({
      exitCode: exec.final_exit_code,
      output: String(exec.attempts?.[0]?.relevant_output || ""),
    });`;

const FINALIZE_CLASSIFY_NEW = String.raw`    const command = String(exec.command || "");
    const testPathMatch = command.match(/(\S+test_\S+\.py)(?:::|\s|$)/);
    const testPath = testPathMatch ? testPathMatch[1] : "";
    let testSource = "";
    if (testPath) {
      try {
        const abs = resourcePath(repo, testPath);
        if (abs) testSource = readFileSync(abs, "utf8");
      } catch {
        testSource = "";
      }
    }
    const cls = classifyCommandExecutionFromOutputWithGate({
      exitCode: exec.final_exit_code,
      output: String(exec.attempts?.[0]?.relevant_output || ""),
      testSource,
    });`;

/**
 * finalizeRedReceiptFromExecutions must use the same gate classifier as shape
 * and the transition gate. The legacy classifier downgrades app/ scaffold
 * RuntimeError to SCAFFOLD_DEFECT and materialize aborts with that code.
 *
 * @param {string} source
 * @returns {string}
 */
export function patchFinalizeRedExecutionGate(source) {
  if (typeof source !== "string") return source;
  if (!source.includes(FINALIZE_CLASSIFY_OLD)) return source;
  if (source.includes("finalizeRedReceiptFromExecutions") &&
      source.includes("classifyCommandExecutionFromOutputWithGate") &&
      source.includes("function finalizeRedReceiptFromExecutions")) {
    const finalizeIdx = source.indexOf("function finalizeRedReceiptFromExecutions");
    const gateIdx = source.indexOf("classifyCommandExecutionFromOutputWithGate", finalizeIdx);
    if (gateIdx !== -1 && gateIdx < source.indexOf("function writeScaffoldDeterministic", finalizeIdx)) {
      return source;
    }
  }
  const crlf = source.includes("\r\n");
  const text = source.replace(/\r\n/g, "\n");
  if (!text.includes(FINALIZE_CLASSIFY_OLD)) return source;
  let patched = text;
  if (!text.includes('from "./opsx-apply-red-transition-gate.mjs"')) {
    patched = patched.replace(
      FINALIZE_GATE_IMPORT,
      FINALIZE_GATE_IMPORT_WITH_CLASSIFIER,
    );
  }
  patched = patched.replace(FINALIZE_CLASSIFY_OLD, FINALIZE_CLASSIFY_NEW);
  return crlf ? patched.replace(/\n/g, "\r\n") : patched;
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
const HOLDING_RED_LOOP = `    for (const hold of binding.already_holding_not_failures || []) {
      const node = String(
        typeof hold === "object" && hold ? hold.command : hold,
      ).trim();
      if (!isPlannedPytestNode(node)) {
        continue;
      }
      commands.push(\`poetry run pytest -q \${testPath}::\${node}\`);
    }`;

const HOLDING_RED_LOOP_WHEN_NO_FAILURE = `    const bindingHasIntendedFailure = (binding.observed_intended_failures || []).some(
      (obs) => isPlannedPytestNode(String(obs?.command || "").trim()),
    );
    for (const hold of binding.already_holding_not_failures || []) {
      if (bindingHasIntendedFailure) {
        break;
      }
      const node = String(
        typeof hold === "object" && hold ? hold.command : hold,
      ).trim();
      if (!isPlannedPytestNode(node)) {
        continue;
      }
      commands.push(\`poetry run pytest -q \${testPath}::\${node}\`);
    }`;

function patchRedCommandDerivation(source) {
  if (typeof source !== "string") return source;
  const crlf = source.includes("\r\n");
  let text = source.replace(/\r\n/g, "\n");
  if (
    !text.includes("bindingHasIntendedFailure") &&
    text.includes(HOLDING_RED_LOOP)
  ) {
    text = text.replace(HOLDING_RED_LOOP, HOLDING_RED_LOOP_WHEN_NO_FAILURE);
  }
  if (text.includes("function fillRedCommandsFromTestModules")) {
    return crlf ? text.replace(/\n/g, "\r\n") : text;
  }
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
    return crlf ? text.replace(/\n/g, "\r\n") : text;
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

const PRIOR_RED_REUSE_FN = "function priorRedExecutionsReusable(prior, testCommands) {";
const PRIOR_RED_REUSE_TAIL = `    if (!Array.isArray(exec.attempts) || !exec.attempts.length) {
      return false;
    }
  }
  return true;
}`;
const PRIOR_RED_REUSE_TAIL_ALIGNED = `    if (!Array.isArray(exec.attempts) || !exec.attempts.length) {
      return false;
    }
  }
  const declared =
    prior.test_content_identity != null && String(prior.test_content_identity).trim()
      ? String(prior.test_content_identity).trim()
      : "";
  if (!repo || !receiptPaths || !declared) return false;
  const resolved = resolveDeclaredFrozenTestContentIdentity(
    {
      test_paths: receiptPaths.test_paths || [],
      fixture_paths: receiptPaths.fixture_paths || [],
      test_content_identity: declared,
    },
    repo,
  );
  const accepted = new Set(resolved.accepted_digests || []);
  if (!accepted.has(declared)) return false;
  return true;
}`;
const PRIOR_RED_REUSE_CALL = "priorRedExecutionsReusable(priorParsed, test_commands)";
const PRIOR_RED_REUSE_CALL_WITH_PATHS =
  "priorRedExecutionsReusable(priorParsed, test_commands, repo, body)";

/**
 * Reuse of prior RED pytest rows must not keep a digest of older test bytes.
 *
 * `writeRedSuccess` copies `test_content_identity` and skips shape when
 * commands still match. After the frozen modules change, that declared
 * digest is not an accepted on-disk variant and RED validate fails for any
 * change. Require the declared digest to be in `accepted_digests` of the
 * paths about to be written; otherwise shape re-runs and stamps the live
 * digest. An operative stamp is not copied, so only the top-level digest
 * counts.
 *
 * @param {string} source
 * @returns {string}
 */
export function patchPriorRedFrozenIdentityReuse(source) {
  if (typeof source !== "string") return source;
  if (source.includes("if (!accepted.has(declared)) return false;")) return source;
  const crlf = source.includes("\r\n");
  const text = source.replace(/\r\n/g, "\n");
  if (!text.includes(PRIOR_RED_REUSE_FN) || !text.includes(PRIOR_RED_REUSE_TAIL)) {
    return source;
  }
  if (!text.includes(PRIOR_RED_REUSE_CALL)) return source;
  let patched = text
    .replace(
      PRIOR_RED_REUSE_FN,
      "function priorRedExecutionsReusable(prior, testCommands, repo, receiptPaths) {",
    )
    .replace(PRIOR_RED_REUSE_TAIL, PRIOR_RED_REUSE_TAIL_ALIGNED);
  if (!patched.includes(PRIOR_RED_REUSE_CALL_WITH_PATHS)) {
    patched = patched.replaceAll(PRIOR_RED_REUSE_CALL, PRIOR_RED_REUSE_CALL_WITH_PATHS);
  }
  if (
    patched.includes("export { buildRedTestCommands };") &&
    !patched.includes("priorRedExecutionsReusable }") &&
    !patched.includes("priorRedExecutionsReusable,")
  ) {
    patched = patched.replace(
      "export { buildRedTestCommands };",
      "export { buildRedTestCommands, priorRedExecutionsReusable };",
    );
  } else if (
    !patched.includes("export { priorRedExecutionsReusable };") &&
    !patched.includes("priorRedExecutionsReusable,")
  ) {
    patched = patched.replace(
      "\nmain();",
      "\nmain();\nexport { priorRedExecutionsReusable };",
    );
  }
  return crlf ? patched.replace(/\n/g, "\r\n") : patched;
}

export async function load(url, context, nextLoad) {
  if (typeof url !== "string") {
    return nextLoad(url, context);
  }
  const isReceiptCli = moduleUrlMatches(url, RECEIPT_CLI);
  const isTransitionGate = moduleUrlMatches(url, "opsx-apply-red-transition-gate.mjs");
  const isPipelineIdentity = moduleUrlMatches(url, PIPELINE_IDENTITY);
  const isShapeCli = moduleUrlMatches(url, "opsx-shape-slice-red-receipt-cli.mjs");
  if (!isReceiptCli && !isTransitionGate && !isPipelineIdentity && !isShapeCli) {
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
  if (isShapeCli) {
    patched = patchShapeRedClassification(original);
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
    patched = patchPriorRedFrozenIdentityReuse(patched);
    patched = patchShapeCliSpawn(patched);
    patched = patchRedValidatorSpawn(patched);
    patched = patchFinalizeRedExecutionGate(patched);
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
