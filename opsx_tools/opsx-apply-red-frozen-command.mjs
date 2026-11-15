/**
 * Frozen RED test command parsing and per-command output classification.
 *
 * Extracted from opsx-apply-red.mjs so transition-gate can load without
 * importing the full RED CLI module (ESM cycle / TDZ safety).
 */
import {
  ALREADY_SATISFIED,
  SCAFFOLD_NOT_IMPLEMENTED,
  classifyRedFailureAfterScaffold,
} from "./opsx-apply-pipeline-identity.mjs";

/**
 * Parse a frozen `node --test` command into path + optional title.
 */
export function parseFrozenTestCommand(command) {
  const raw = String(command || "").trim();
  const patternIdx = raw.indexOf("--test-name-pattern");
  let test_title = "";
  let rest = raw;
  if (patternIdx >= 0) {
    const after = raw.slice(patternIdx + "--test-name-pattern".length).trim();
    const m = after.match(/^(\^)?(.+?)(\$)?(?:\s+|$)(.*)$/);
    if (m) {
      test_title = String(m[2] || "").replace(/\\ /g, " ");
      rest = `${raw.slice(0, patternIdx)} ${m[4] || ""}`;
    }
  }
  const withoutNode = rest.replace(/::[\w.[\]]+\s*$/, "").trim();
  const pathMatch = withoutNode.match(/(\S+\.test\.\w+|\S+test_\S+\.py)(?:\s*::[\w.[\]]+)?\s*$/);
  const test_path = pathMatch ? pathMatch[1] : "";
  let test_title_from_cmd = "";
  const nodeMatch = rest.match(/::([\w.[\]]+)\s*$/);
  if (nodeMatch) {
    test_title_from_cmd = nodeMatch[1];
  }
  return {
    command: raw,
    test_path,
    test_title: test_title || test_title_from_cmd,
  };
}

/**
 * Classify one bounded RED command from captured subprocess output.
 * Sibling commands MUST NOT inherit this result.
 */
export function classifyCommandExecutionFromOutput({
  output = "",
  exitCode,
  intendedSeam = "",
} = {}) {
  const text = String(output || "");
  const testsMatch = text.match(/# tests\s+(\d+)/);
  const nodeTestsCount = testsMatch ? Number(testsMatch[1]) : null;
  const pytestCollectedMatch = text.match(/collected\s+(\d+)\s+items?/i);
  const pytestCollected = pytestCollectedMatch ? Number(pytestCollectedMatch[1]) : null;
  const testsCount = nodeTestsCount ?? pytestCollected;
  if (/SyntaxError/i.test(text)) {
    return classifyRedFailureAfterScaffold({ syntaxError: true });
  }
  if (
    /ERR_MODULE_NOT_FOUND|Cannot find module|ModuleNotFoundError|ImportError while importing|ERROR collecting/i.test(
      text,
    )
  ) {
    return classifyRedFailureAfterScaffold({ importError: true });
  }
  if (
    /does not provide an export named|is not a function|Cannot read propert(?:y|ies) of undefined/i.test(
      text,
    )
  ) {
    return classifyRedFailureAfterScaffold({ missingSymbol: true });
  }
  if (
    testsCount === 0 ||
    /collected\s+0\s+items|no tests ran|deselected\s+\d+\s+items?.*0 selected/i.test(text)
  ) {
    return classifyRedFailureAfterScaffold({
      zeroIntendedTestsExecuted: true,
      wrongPattern: true,
    });
  }
  if (exitCode === 0) {
    return {
      proven: false,
      classification: ALREADY_SATISFIED,
      code: ALREADY_SATISFIED,
      red_qualified: false,
      pass_qualified: true,
    };
  }
  const sentinel = text.includes(SCAFFOLD_NOT_IMPLEMENTED);
  const assertion =
    /AssertionError/.test(text) ||
    /\bFAILED\b[\s\S]*(?:assert|AssertionError|E\s+assert)/m.test(text);
  const seamReached =
    Boolean(intendedSeam) && text.includes(intendedSeam)
      ? true
      : sentinel || assertion;
  return classifyRedFailureAfterScaffold({
    reachedIntendedSeam: seamReached,
    assertionMismatch: assertion,
    sentinelReached: sentinel,
  });
}
