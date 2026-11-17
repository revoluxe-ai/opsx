"""RED gate: design-bound scenario tests and app/ scaffold sentinels."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "receipt_cli_identity_hook.mjs"
_GATE = (
    Path(__file__).resolve().parents[5]
    / "opsx_tools"
    / "opsx-apply-red-transition-gate.mjs"
)

_CHECKER = r"""
const gateUrl = process.argv[2];
const {
  fillRedOracleFromDesignBinding,
  sentinelHasScaffoldProvenance,
  classifyCommandExecutionFromOutputWithGate,
} = await import(gateUrl);

const output = [
  "app\\modules\\billing\\managed_articles.py:24: in managed_article_count",
  "    raise NotImplementedError(_OPSX_SCAFFOLD_NOT_IMPLEMENTED)",
  "E   NotImplementedError: OPSX_SCAFFOLD_NOT_IMPLEMENTED",
].join("\n");
const caller = "count = managed_article_count(adv_settings)\nassert count == authoritative_expected\n";
const product = sentinelHasScaffoldProvenance({
  output,
  testSource: caller,
});
if (!product.qualifies || product.reason !== "python_product_scaffold_runtime") {
  console.log(JSON.stringify({ case: "app_sentinel", product }));
  process.exit(1);
}
const live = classifyCommandExecutionFromOutputWithGate({
  output,
  exitCode: 1,
  testSource: caller,
});
if (live.red_qualified !== true || live.classification !== "INTENDED_BEHAVIOR_MISSING") {
  console.log(JSON.stringify({ case: "classify", live }));
  process.exit(1);
}

const thrown = sentinelHasScaffoldProvenance({
  output,
  testSource: 'raise NotImplementedError("OPSX_SCAFFOLD_NOT_IMPLEMENTED")\n',
});
if (thrown.qualifies || thrown.reason !== "test_thrown_sentinel") {
  console.log(JSON.stringify({ case: "thrown", thrown }));
  process.exit(2);
}

const bare = sentinelHasScaffoldProvenance({
  output: "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
  testSource: caller,
});
if (bare.qualifies || bare.reason !== "sentinel_without_scaffold_stack") {
  console.log(JSON.stringify({ case: "bare", bare }));
  process.exit(3);
}

const stroy = sentinelHasScaffoldProvenance({
  output: "src/stroy/channels/visual_covers.py\nRuntimeError: OPSX_SCAFFOLD_NOT_IMPLEMENTED",
  testSource: "from stroy.channels.telegram.invest_yalta.visual_covers import cover_for_ux_state\n",
});
if (!stroy.qualifies || stroy.reason !== "python_product_scaffold_runtime") {
  console.log(JSON.stringify({ case: "stroy", stroy }));
  process.exit(4);
}

const filled = fillRedOracleFromDesignBinding(
  { ok: true, spec: null, behavior: null, source: caller },
  "RED_PROVEN",
  {
    red_oracle_bindings: [
      { test_path: "tests/scenarios/example/test_sample.py", authoritative_expected: "0" },
    ],
  },
  "tests/scenarios/example/test_sample.py",
);
if (filled.spec !== "0" || filled.behavior !== "NOT-MET") {
  console.log(JSON.stringify({ case: "fill", filled }));
  process.exit(5);
}
const kept = fillRedOracleFromDesignBinding(
  { ok: true, spec: "1", behavior: "0", source: "" },
  "RED_PROVEN",
  { red_oracle_bindings: [{ test_path: "t.py", authoritative_expected: "9" }] },
  "t.py",
);
if (kept.spec !== "1" || kept.behavior !== "0") {
  console.log(JSON.stringify({ case: "kept", kept }));
  process.exit(6);
}
const already = fillRedOracleFromDesignBinding(
  { ok: true, spec: null, behavior: null, source: "" },
  "ALREADY_SATISFIED",
  { red_oracle_bindings: [{ test_path: "t.py", authoritative_expected: "1" }] },
  "t.py",
);
if (already.spec !== null || already.behavior !== null) {
  console.log(JSON.stringify({ case: "already", already }));
  process.exit(7);
}
console.log(JSON.stringify({ ok: true }));
"""


@pytest.mark.unit
def test_design_binding_oracle_and_app_scaffold_sentinel(tmp_path: Path) -> None:
    """Missing comment markers use the design binding; app/ NotImplementedError is RED.

    A scenario test that asserts ``authoritative_expected`` and fails in a product
    module under ``app/`` or ``src/`` with ``NotImplementedError:
    OPSX_SCAFFOLD_NOT_IMPLEMENTED`` is intended-behavior evidence. A test that
    raises that sentinel itself, or a bare sentinel string, stays unqualified.
    ``ALREADY_SATISFIED`` still requires an explicit behavior marker.
    """
    assert _HOOK.is_file()
    assert _GATE.is_file()
    checker = tmp_path / "check_red_sentinel.mjs"
    _ = checker.write_text(_CHECKER, encoding="utf-8")
    run = subprocess.run(
        ["node", "--import", _HOOK.as_uri(), str(checker), _GATE.as_uri()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    body = json.loads((run.stdout or "").strip().splitlines()[-1])
    assert body.get("ok") is True
