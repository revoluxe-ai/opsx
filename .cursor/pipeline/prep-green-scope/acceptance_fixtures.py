"""Fixtures + static-check handoff builders for prep-green acceptance."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
_HANDOFF_DIR = REPO_ROOT / ".cursor" / "static-check" / "contracts"
if str(_HANDOFF_DIR) not in sys.path:
    sys.path.insert(0, str(_HANDOFF_DIR))

import prep_green_full_handoff as pgfh  # noqa: E402

_CONTRACTS = REPO_ROOT / ".cursor" / "pipeline" / "contracts"
if str(_CONTRACTS) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS))

from required_test_ids_gate import test_id_is_skipped  # noqa: E402

KNOWN_FAILING_NODES = (
    "tests/test_unit_fail.py::test_fail",
    "tests/test_integration_fail.py::test_integration_fail",
    "tests/test_wave2_fail.py::test_wave2_only",
    "tests/test_param.py::test_param[2]",
)


def write_mini_repo(root: Path, *, destructive_policy: str | None = None) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "openspec" / "changes" / "demo" / "scenario-coverage").mkdir(
        parents=True, exist_ok=True
    )
    (root / "pyrightconfig_strict.json").write_text(
        '{"include":["src"],"ignore":[]}\n',
        encoding="utf-8",
    )
    (root / "src" / "bad_pyright.py").write_text(
        "def bad() -> int:\n    return 'x'\n",
        encoding="utf-8",
    )
    (root / "src" / "bad_mypy.py").write_text(
        "def bad_mypy() -> int:\n    return 'y'\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [tool.poetry]
            name = "prep-fixture"
            version = "0.0.0"

            [tool.mypy]
            python_version = "3.11"

            [[tool.mypy.overrides]]
            module = "legacy.unrelated"
            ignore_errors = true

            [tool.ruff]
            line-length = 100

            [tool.pytest.ini_options]
            markers = [
                "unit: unit tests",
                "integration: integration tests",
                "destructive_local: destructive tests",
            ]
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (root / "tests" / "test_unit_fail.py").write_text(
        "def test_fail():\n    assert False\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_integration_fail.py").write_text(
        "import pytest\n\n@pytest.mark.integration\ndef test_integration_fail():\n    assert False\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_wave2_fail.py").write_text(
        "def test_wave2_only():\n    assert False\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_param.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('v', [1, 2, 3])\n"
        "def test_param(v):\n    if v == 2:\n        assert False\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_unrelated_skip.py").write_text(
        'import pytest\n\n@pytest.mark.skip(reason="prep-green-scope: unrelated legacy")\n'
        "def test_skipped_legacy():\n    assert False\n",
        encoding="utf-8",
    )
    (root / "src" / "legacy_ignored.py").write_text(
        "def legacy_only() -> int:\n    return 'z'\n",
        encoding="utf-8",
    )
    (root / "openspec" / "changes" / "demo" / "tasks.md").write_text(
        "# demo change tasks\n",
        encoding="utf-8",
    )
    (root / "openspec" / "changes" / "demo" / "scenario-coverage" / "plan.md").write_text(
        textwrap.dedent(
            """
            ## SCENARIO_COVERAGE_PLAN

            | SCENARIO_ID | REQUIRED_EVIDENCE |
            |-------------|-------------------|
            | demo-a | unit proof |
            | demo-b | integration proof |
            """
        ).lstrip(),
        encoding="utf-8",
    )
    caps: dict = {
        "quality": {"test": {"status": "resolved"}},
        "product": {"source_roots": ["src/"], "test_roots": ["tests/"]},
    }
    if destructive_policy:
        caps.setdefault("quality", {})["static_check"] = {
            "full": {"pytest_destructive": destructive_policy}
        }
    (root / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    _ = (root / "opsx" / "project" / "capabilities.yaml").write_text(
        yaml.safe_dump(caps, sort_keys=False),
        encoding="utf-8",
    )


def init_git_baseline(root: Path) -> None:
    """Minimal git repo so bounded-verify manifest sees worktree/branch deltas."""

    _ = subprocess.run(["git", "init", "-b", "main"], cwd=root, capture_output=True, check=True)
    _ = subprocess.run(
        ["git", "config", "user.email", "prep-green@test"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "prep-green"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    _ = subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=root,
        capture_output=True,
        check=True,
    )


def touch_product_file(root: Path, rel: str) -> None:
    """Record an in-policy product change in the worktree (uncommitted delta)."""

    path = root / rel
    path.write_text(path.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")


def build_handoff(
    root: Path,
    *,
    status: str,
    fingerprint: str,
    packets: list[dict],
    destructive_policy: str = "not_applicable",
) -> dict:
    """Canonical serializer used by static-check fan-in."""

    return pgfh.build_full_handoff(
        repo_root=REPO_ROOT,
        overall_status=status,
        mode="FULL",
        worktree_fingerprint=fingerprint,
        check_packets=packets,
        destructive_policy=destructive_policy,
        execution_id="acceptance-exec-stable",
    )


def iteration0_packets() -> list[dict]:
    return [
        {
            "CHECK_ID": "pyright-strict",
            "STATUS": "FAIL",
            "FINDINGS": [
                {
                    "filepath": "src/bad_pyright.py",
                    "line": 2,
                    "symbol_or_node": "n/a",
                    "severity": "typing",
                }
            ],
        },
        {
            "CHECK_ID": "mypy",
            "STATUS": "FAIL",
            "FINDINGS": [
                {
                    "filepath": "src/bad_mypy.py",
                    "line": 1,
                    "symbol_or_node": "n/a",
                    "severity": "typing",
                }
            ],
        },
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"},
        {
            "CHECK_ID": "pytest-unit",
            "STATUS": "FAIL",
            "executed_count": 2,
            "FINDINGS": [
                {
                    "filepath": "n/a",
                    "symbol_or_node": "tests/test_unit_fail.py::test_fail",
                    "severity": "test-failure",
                }
            ],
        },
        {
            "CHECK_ID": "pytest-integration",
            "STATUS": "FAIL",
            "executed_count": 1,
            "FINDINGS": [
                {
                    "filepath": "n/a",
                    "symbol_or_node": "tests/test_integration_fail.py::test_integration_fail",
                    "severity": "test-failure",
                }
            ],
        },
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
        },
    ]


def iteration1_packets() -> list[dict]:
    return [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "FINDINGS": []},
        {
            "CHECK_ID": "pytest-unit",
            "STATUS": "FAIL",
            "executed_count": 1,
            "FINDINGS": [
                {
                    "filepath": "n/a",
                    "symbol_or_node": "tests/test_wave2_fail.py::test_wave2_only",
                    "severity": "test-failure",
                }
            ],
        },
        {"CHECK_ID": "pytest-integration", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
        },
    ]


def iteration2_packets() -> list[dict]:
    return [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "FINDINGS": []},
        {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {"CHECK_ID": "pytest-integration", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
        },
    ]


def worktree_input_fingerprint(root: Path) -> str:
    """INPUT_SNAPSHOT_IDENTITY source: product/worktree files that affect probes."""

    parts: list[str] = []
    for rel in (
        "pyrightconfig_strict.json",
        "pyproject.toml",
        "src/bad_pyright.py",
        "src/bad_mypy.py",
        "tests/test_unit_fail.py",
        "tests/test_integration_fail.py",
        "tests/test_wave2_fail.py",
        "tests/test_param.py",
    ):
        path = root / rel
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"wt-{digest}"


def _pyright_ignored(root: Path, rel: str) -> bool:
    data = json.loads((root / "pyrightconfig_strict.json").read_text(encoding="utf-8"))
    ignore = data.get("ignore") or []
    return rel in ignore


def _mypy_module_ignored(root: Path, module_guess: str) -> bool:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return f'module = "{module_guess}"' in text and "ignore_errors = true" in text


def _active_unit_failures(root: Path) -> list[str]:
    active: list[str] = []
    for node in KNOWN_FAILING_NODES:
        if "integration" in node:
            continue
        if test_id_is_skipped(root, node) is None:
            active.append(node)
    return active


def _integration_active(root: Path) -> str | None:
    node = "tests/test_integration_fail.py::test_integration_fail"
    if test_id_is_skipped(root, node) is None:
        return node
    return None


def derive_packets_from_worktree(root: Path) -> list[dict]:
    """Simulate canonical FULL worker packets from current worktree disable state."""

    packets: list[dict] = []
    if _pyright_ignored(root, "src/bad_pyright.py"):
        packets.append({"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []})
    else:
        packets.append(
            {
                "CHECK_ID": "pyright-strict",
                "STATUS": "FAIL",
                "FINDINGS": [
                    {
                        "filepath": "src/bad_pyright.py",
                        "line": 2,
                        "symbol_or_node": "n/a",
                        "severity": "typing",
                    }
                ],
            }
        )
    if _mypy_module_ignored(root, "bad_mypy"):
        packets.append({"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []})
    else:
        packets.append(
            {
                "CHECK_ID": "mypy",
                "STATUS": "FAIL",
                "FINDINGS": [
                    {
                        "filepath": "src/bad_mypy.py",
                        "line": 1,
                        "symbol_or_node": "n/a",
                        "severity": "typing",
                    }
                ],
            }
        )
    packets.append({"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"})
    unit_active = _active_unit_failures(root)
    if unit_active:
        packets.append(
            {
                "CHECK_ID": "pytest-unit",
                "STATUS": "FAIL",
                "executed_count": len(unit_active),
                "FINDINGS": [
                    {
                        "filepath": "n/a",
                        "symbol_or_node": node,
                        "severity": "test-failure",
                    }
                    for node in unit_active
                ],
            }
        )
    else:
        packets.append(
            {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []}
        )
    integ = _integration_active(root)
    if integ:
        packets.append(
            {
                "CHECK_ID": "pytest-integration",
                "STATUS": "FAIL",
                "executed_count": 1,
                "FINDINGS": [
                    {
                        "filepath": "n/a",
                        "symbol_or_node": integ,
                        "severity": "test-failure",
                    }
                ],
            }
        )
    else:
        packets.append(
            {
                "CHECK_ID": "pytest-integration",
                "STATUS": "PASS",
                "executed_count": 1,
                "FINDINGS": [],
            }
        )
    packets.append(
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
        }
    )
    return packets


def overall_status_from_packets(packets: list[dict]) -> str:
    for packet in packets:
        if packet.get("STATUS") in {"FAIL", "ERROR"}:
            return "FAIL"
    return "PASS"


def build_handoff_from_worktree(
    root: Path,
    *,
    destructive_policy: str = "not_applicable",
) -> dict:
    """Fan-in handoff derived from worktree state (post-apply rerun simulation)."""

    packets = derive_packets_from_worktree(root)
    return pgfh.build_full_handoff(
        repo_root=REPO_ROOT,
        overall_status=overall_status_from_packets(packets),
        mode="FULL",
        worktree_fingerprint=worktree_input_fingerprint(root),
        check_packets=packets,
        destructive_policy=destructive_policy,
        execution_id="acceptance-exec-stable",
    )


def write_ambiguous_param_test(root: Path) -> None:
    """Tuple parametrize + explicit id — unsupported disable shape."""

    (root / "tests" / "test_ambiguous.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('a,b', [(1, 2), (3, 4)], ids=['case-a', 'dup'])\n"
        "def test_tuple_param(a, b):\n    assert a != b\n",
        encoding="utf-8",
    )


def write_complex_marks_param_test(root: Path) -> None:
    """Explicit id with existing marks — unsupported (fail-closed)."""

    (root / "tests" / "test_complex_marks.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('v', [pytest.param(1, marks=pytest.mark.xfail(reason='x'))], ids=['case-a'])\n"
        "def test_complex(v):\n    assert v\n",
        encoding="utf-8",
    )


def pytest_unit_packet_with_nodes(nodes: list[str]) -> dict:
    """Build pytest-unit FAIL packet with one finding per node id."""

    return {
        "CHECK_ID": "pytest-unit",
        "STATUS": "FAIL",
        "executed_count": len(nodes),
        "FINDINGS": [
            {
                "filepath": "n/a",
                "symbol_or_node": node,
                "severity": "test-failure",
            }
            for node in nodes
        ],
        "FINDINGS_TOTAL": len(nodes),
    }
