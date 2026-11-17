"""RED receipt commands from test_path bindings, not only planned node names."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "receipt_cli_identity_hook.mjs"
_CLI = Path(__file__).resolve().parents[5] / "opsx_tools" / "opsx-record-slice-receipt-decision-cli.mjs"

_CHECKER = r"""
const cliUrl = process.argv[2];
const repo = process.argv[3];
const withNodes = process.argv[4];
const withoutNodes = process.argv[5];
const { buildRedTestCommands } = await import(cliUrl);

const fromPath = buildRedTestCommands(
  {
    red_oracle_bindings: [
      { test_path: withNodes, authoritative_expected: "0" },
    ],
    red_test_targets_or_seams: [withNodes],
  },
  repo,
);
const cmds = fromPath.test_commands || [];
if (cmds.length !== 2) {
  console.log(JSON.stringify({ case: "from_path", cmds }));
  process.exit(1);
}
if (!cmds.some((cmd) => cmd === `poetry run pytest -q ${withNodes}::test_scenario_red`)) {
  console.log(JSON.stringify({ case: "node_missing", cmds }));
  process.exit(1);
}
if (!cmds.some((cmd) => cmd === `poetry run pytest -q ${withNodes}::test_other_case`)) {
  console.log(JSON.stringify({ case: "second_node_missing", cmds }));
  process.exit(1);
}

const explicit = buildRedTestCommands(
  {
    red_oracle_bindings: [
      {
        test_path: withNodes,
        authoritative_expected: "1",
        observed_intended_failures: [{ command: "test_scenario_red" }],
      },
    ],
  },
  repo,
);
if (explicit.test_commands.length !== 1) {
  console.log(JSON.stringify({ case: "explicit", cmds: explicit.test_commands }));
  process.exit(2);
}
if (explicit.test_commands[0] !== `poetry run pytest -q ${withNodes}::test_scenario_red`) {
  console.log(JSON.stringify({ case: "explicit_text", cmds: explicit.test_commands }));
  process.exit(2);
}

const empty = buildRedTestCommands(
  {
    red_oracle_bindings: [
      { test_path: withoutNodes, authoritative_expected: "2" },
    ],
  },
  repo,
);
if ((empty.test_commands || []).length !== 0) {
  console.log(JSON.stringify({ case: "empty", cmds: empty.test_commands }));
  process.exit(3);
}
if (!(empty.test_paths || []).includes(withoutNodes)) {
  console.log(JSON.stringify({ case: "path_kept", paths: empty.test_paths }));
  process.exit(3);
}
"""


@pytest.mark.unit
def test_binding_test_path_without_command_yields_pytest_node(tmp_path: Path) -> None:
    """test_path + authoritative_expected still produces pytest node commands.

    Planned ``observed_intended_failures[].command`` stays the only command
    for that path. A module with no ``def test_*`` stays an empty command list.
    """
    assert _HOOK.is_file()
    assert _CLI.is_file()
    rel = "tests/scenarios/example_change/test_sample.py"
    module = tmp_path / rel
    module.parent.mkdir(parents=True)
    _ = module.write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.unit\n"
        "async def test_other_case():\n"
        "    assert False\n"
        "\n"
        "@pytest.mark.workflow_deferred\n"
        "def test_scenario_red() -> None:\n"
        "    assert False\n"
        "\n"
        "def helper_not_a_test():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    bare = "tests/scenarios/example_change/test_no_nodes.py"
    bare_path = tmp_path / bare
    _ = bare_path.write_text("VALUE = 1\n", encoding="utf-8")
    checker = tmp_path / "check_red_commands.mjs"
    _ = checker.write_text(_CHECKER, encoding="utf-8")
    env = os.environ.copy()
    env["OPSX_RECEIPT_CLI_INSPECT"] = "1"
    run = subprocess.run(
        [
            "node",
            "--import",
            _HOOK.as_uri(),
            str(checker),
            _CLI.as_uri(),
            str(tmp_path),
            rel,
            bare,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
        env=env,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.unit
def test_receipt_cli_entrypoint_patches_red_commands(tmp_path: Path) -> None:
    """``node --import hook cli.mjs`` must patch the process entry, not only import().

    Slice receipt materialize spawns the record CLI as the entrypoint. A loader
    that patches only a later import leaves ``RED_COMMANDS_EMPTY`` with no
    message when the design names ``test_path`` and no planned node.
    """
    assert _HOOK.is_file()
    assert _CLI.is_file()
    change_id = "example_change"
    slice_id = "slice-a"
    rel = "tests/scenarios/example_change/test_sample.py"
    design = {
        "schema": "opsx.apply_design",
        "slices": [
            {
                "slice_id": slice_id,
                "red_oracle_bindings": [
                    {"test_path": rel, "authoritative_expected": "0"},
                ],
                "red_test_targets_or_seams": [rel],
            }
        ],
    }
    design_path = (
        tmp_path / "openspec" / "changes" / change_id / "apply-design" / "APPLY-DESIGN.json"
    )
    design_path.parent.mkdir(parents=True)
    _ = design_path.write_text(json.dumps(design), encoding="utf-8")
    payload = json.dumps(
        {
            "repo": str(tmp_path),
            "change_id": change_id,
            "decision": {
                "stage": "APPLY_RED",
                "slice_id": slice_id,
                "hop_status": "APPLY_COMPLETE",
            },
        }
    )
    env = os.environ.copy()
    env.pop("OPSX_RECEIPT_CLI_INSPECT", None)
    run = subprocess.run(
        ["node", "--import", _HOOK.as_uri(), str(_CLI)],
        input=payload,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
        env=env,
    )
    line = (run.stdout or "").strip().splitlines()
    body = json.loads(line[-1] if line else "{}")
    # No module on disk: patched entry names the missing field; unpatched entry
    # returns the bare code and the next hop cannot fill commands.
    assert body.get("code") == "RED_COMMANDS_EMPTY", run.stdout + run.stderr
    assert "def test_" in str(body.get("message") or ""), run.stdout + run.stderr
