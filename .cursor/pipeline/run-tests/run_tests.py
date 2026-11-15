"""Manual explicit pytest runner for target-native /opsx-run-tests.

The helper accepts only mode=manual with exact existing Poetry/pytest node
ids. It does not discover a suite, write a verification receipt, or execute a
frozen S6 assignment. Destructive selections fail closed before any runner
call because this repository has no scoped destructive planner API.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


CONTRACT_ID = "OPSX-WB-RUN-TESTS-01"
PLANNER_RELATIVE_PATH = Path(".cursor/static-check/bin/destructive_cache_plan.py")
FILE_NAME_PATTERN = re.compile(
    r"^(?:test_[A-Za-z0-9_]+|[A-Za-z0-9_]+_test|tests)\.py$"
)
PATH_PART_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SELECTOR_PART_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PARAM_PATTERN = re.compile(r"^[A-Za-z0-9_.=,-]+$")
RUNNER = Callable[[Sequence[str], Path], int]


@dataclass(frozen=True)
class RunResult:
    """Chat-only execution report. No field is a durable receipt path."""

    mode: str
    status: str
    reason: str
    test_ids: tuple[str, ...]
    resolved_commands: tuple[tuple[str, ...], ...]
    exit_codes: tuple[int, ...]
    detail: str = ""

    @property
    def runner_invoked(self) -> int:
        """Return how many explicit node commands were started."""

        return len(self.exit_codes)


def build_pytest_argv(node_id: str) -> tuple[str, ...]:
    """Build one shell-free Poetry/pytest argv for a single exact node id."""

    return (
        "poetry",
        "run",
        "pytest",
        node_id,
        "-vv",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        "--override-ini",
        "addopts=",
    )


def poetry_pytest_runner(argv: Sequence[str], cwd: Path) -> int:
    """Run one already-validated argv with shell disabled."""

    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        shell=False,
        check=False,
    )
    return int(completed.returncode)


def evaluate_manual_run(
    repo_root: Path,
    arguments: str,
    runner: RUNNER | None = None,
) -> RunResult:
    """Validate explicit ids and run only a proven non-destructive selection."""

    root = repo_root.resolve()
    parsed = _parse_arguments(arguments)
    if isinstance(parsed, RunResult):
        return parsed
    mode, lane, raw_ids = parsed
    if mode == "bounded":
        return _blocked("bounded", "BINDING_GAP", raw_ids, "S6_TEST_CAPABILITY_UNREGISTERED")
    if mode == "":
        return _blocked("rejected", "MODE_REQUIRED", raw_ids)
    if mode != "manual":
        return _blocked("rejected", "UNKNOWN_MODE", raw_ids)
    if lane != "pytest":
        return _blocked("manual", "CROSS_LANE", raw_ids, "LANE_NOT_PYTEST")
    if not raw_ids:
        return _blocked("manual", "MISSING_TEST_ID", ())

    structural: list[str] = []
    for raw_id in raw_ids:
        reason = _structural_reason(raw_id)
        if reason is not None:
            return _blocked("manual", reason, tuple(raw_ids))
        structural.append(raw_id)
    if len(structural) != len(set(structural)):
        return _blocked("manual", "DUPLICATE_TEST_ID", tuple(structural))

    resolved: list[tuple[str, Path, ast.Module, ast.AST]] = []
    for node_id in structural:
        located = _locate_node(root, node_id)
        if isinstance(located, str):
            return _blocked("manual", located, tuple(structural))
        resolved.append(located)
    for node_id, file_path, tree, node in resolved:
        if _selection_is_destructive(root, file_path, tree, node):
            return _blocked(
                "manual",
                "DESTRUCTIVE_LOCAL_BLOCKED",
                tuple(structural),
                _destructive_detail(root, node_id),
            )

    active_runner = poetry_pytest_runner if runner is None else runner
    commands: list[tuple[str, ...]] = []
    exit_codes: list[int] = []
    for node_id, _, _, _ in resolved:
        argv = build_pytest_argv(node_id)
        commands.append(argv)
        try:
            exit_code = active_runner(argv, root)
        except OSError as error:
            exit_codes.append(127)
            return RunResult(
                mode="manual",
                status="BLOCKED",
                reason="EXECUTION_ERROR",
                test_ids=tuple(structural),
                resolved_commands=tuple(commands),
                exit_codes=tuple(exit_codes),
                detail=str(error),
            )
        exit_codes.append(exit_code)
        if exit_code not in {0, 1}:
            reason = "INVALID_TEST_ID" if exit_code in {4, 5} else "EXECUTION_ERROR"
            return RunResult(
                mode="manual",
                status="BLOCKED",
                reason=reason,
                test_ids=tuple(structural),
                resolved_commands=tuple(commands),
                exit_codes=tuple(exit_codes),
            )
    if any(code == 1 for code in exit_codes):
        return RunResult(
            mode="manual",
            status="FAIL",
            reason="TEST_FAILED",
            test_ids=tuple(structural),
            resolved_commands=tuple(commands),
            exit_codes=tuple(exit_codes),
        )
    return RunResult(
        mode="manual",
        status="PASS",
        reason="",
        test_ids=tuple(structural),
        resolved_commands=tuple(commands),
        exit_codes=tuple(exit_codes),
    )


def format_run_result(result: RunResult) -> str:
    """Render the chat-only RUN_RESULT. This text is not verification evidence."""

    commands = [list(command) for command in result.resolved_commands]
    lines = [
        f"CONTRACT_ID: {CONTRACT_ID}",
        f"MODE: {result.mode}",
        f"STATUS: {result.status}",
        f"reason: {result.reason}",
        f"detail: {result.detail}",
        "test_ids: " + ",".join(result.test_ids),
        "resolved_commands: " + json.dumps(commands, ensure_ascii=True),
        "exit_codes: " + ",".join(str(code) for code in result.exit_codes),
        "CACHE_MODE: n/a",
        "DESTRUCTIVE_PLANNER_CALL: NO",
        "DURABLE_RECEIPT: NONE",
        "RUN_RESULT: chat-only",
        "GREEN_WRITER: NO",
        "LEDGER_WRITER: NO",
        "RESULT_WRITER: NO",
    ]
    return "\n".join(lines)


def _blocked(
    mode: str,
    reason: str,
    test_ids: tuple[str, ...],
    detail: str = "",
) -> RunResult:
    return RunResult(
        mode=mode,
        status="BLOCKED",
        reason=reason,
        test_ids=test_ids,
        resolved_commands=(),
        exit_codes=(),
        detail=detail,
    )


def _parse_arguments(
    arguments: str,
) -> tuple[str, str, tuple[str, ...]] | RunResult:
    """Parse key=value tokens. Any explicit bounded mode wins over manual ids."""

    tokens = arguments.split()
    if not tokens:
        return _blocked("rejected", "MODE_REQUIRED", ())
    values: dict[str, list[str]] = {"mode": [], "lane": [], "test_ids": []}
    for token in tokens:
        if "=" not in token:
            return _blocked("rejected", "INVALID_TEST_ID", ())
        key, value = token.split("=", 1)
        if key not in values:
            if key == "mode" and value == "bounded":
                return _blocked(
                    "bounded",
                    "BINDING_GAP",
                    (),
                    "S6_TEST_CAPABILITY_UNREGISTERED",
                )
            return _blocked("rejected", "UNKNOWN_MODE", (), key)
        values[key].append(value)
    modes = values["mode"]
    if any(mode == "bounded" for mode in modes):
        return _blocked(
            "bounded",
            "BINDING_GAP",
            _ids_from_values(values["test_ids"]),
            "S6_TEST_CAPABILITY_UNREGISTERED",
        )
    if len(modes) > 1 or len(values["lane"]) > 1 or len(values["test_ids"]) > 1:
        return _blocked("rejected", "DUPLICATE_TEST_ID", ())
    mode = modes[0] if modes else ""
    lane = values["lane"][0] if values["lane"] else ""
    return mode, lane, _ids_from_values(values["test_ids"])


def _ids_from_values(values: list[str]) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(_split_test_ids(values[0]))


def _split_test_ids(raw: str) -> list[str]:
    """Split commas that are outside pytest parameter brackets."""

    if raw == "":
        return []
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for character in raw:
        if character == "[":
            depth += 1
        elif character == "]" and depth > 0:
            depth -= 1
        if character == "," and depth == 0:
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(character)
    parts.append("".join(buffer))
    return parts


def _structural_reason(node_id: str) -> str | None:
    """Reject traversal, globs, injection characters, and cross-lane paths."""

    if node_id == "":
        return "MISSING_TEST_ID"
    if "*" in node_id or "?" in node_id:
        return "GLOB_FORBIDDEN"
    if (
        ".." in node_id.split("/")
        or node_id.startswith("/")
        or re.match(r"^[A-Za-z]:", node_id) is not None
    ):
        return "PATH_TRAVERSAL"
    if "\\" in node_id:
        return "INVALID_TEST_ID"
    if "::" not in node_id:
        return "MISSING_TEST_ID"
    path_text, selector = node_id.split("::", 1)
    if "[" in path_text or "]" in path_text:
        return "GLOB_FORBIDDEN"
    if not path_text.startswith("tests/"):
        return "CROSS_LANE"
    parts = path_text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "PATH_TRAVERSAL"
    if any(PATH_PART_PATTERN.fullmatch(part) is None for part in parts[1:-1]):
        return "INVALID_TEST_ID"
    if FILE_NAME_PATTERN.fullmatch(parts[-1]) is None:
        return "CROSS_LANE"
    return _selector_reason(selector)


def _selector_reason(selector: str) -> str | None:
    pieces = selector.split("::")
    if len(pieces) > 2 or any(piece == "" for piece in pieces):
        return "INVALID_TEST_ID"
    for index, piece in enumerate(pieces):
        parameter = ""
        name = piece
        if "[" in piece or "]" in piece:
            if index != len(pieces) - 1 or piece.count("[") != 1 or not piece.endswith("]"):
                return "INVALID_TEST_ID"
            name, _, parameter = piece.partition("[")
            parameter = parameter[:-1]
            if PARAM_PATTERN.fullmatch(parameter) is None:
                return "INVALID_TEST_ID"
        if SELECTOR_PART_PATTERN.fullmatch(name) is None:
            return "INVALID_TEST_ID"
        expects_test = index == len(pieces) - 1
        if expects_test and not name.startswith("test_"):
            return "UNKNOWN_SELECTOR"
        if not expects_test and not name.startswith("Test"):
            return "UNKNOWN_SELECTOR"
    return None


def _locate_node(
    repo_root: Path,
    node_id: str,
) -> tuple[str, Path, ast.Module, ast.AST] | str:
    """Resolve a contained file and the exact class or function node."""

    path_text, selector = node_id.split("::", 1)
    tests_root = (repo_root / "tests").resolve()
    candidate = (repo_root / Path(*path_text.split("/"))).resolve()
    if not candidate.is_relative_to(tests_root):
        return "PATH_TRAVERSAL"
    if not candidate.is_file():
        return "MISSING_TEST_ID"
    try:
        tree = ast.parse(candidate.read_text(encoding="utf-8"), filename=str(candidate))
    except (OSError, SyntaxError, UnicodeError):
        return "INVALID_TEST_ID"
    pieces = selector.split("::")
    names = [piece.split("[", 1)[0] for piece in pieces]
    if len(names) == 1:
        found = _module_function(tree, names[0])
    else:
        found = _class_method(tree, names[0], names[1])
    if found is None:
        return "UNKNOWN_SELECTOR"
    return node_id, candidate, tree, found


def _module_function(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _class_method(tree: ast.Module, class_name: str, method_name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == method_name
                ):
                    return child
    return None


def _selection_is_destructive(
    repo_root: Path,
    file_path: Path,
    tree: ast.Module,
    node: ast.AST,
) -> bool:
    """Return whether the selected node or a parent conftest applies the marker."""

    if _node_is_destructive(node):
        return True
    if _pytestmark_is_blocking(tree):
        return True
    parents = _class_parents(tree, node)
    if any(_node_is_destructive(parent) for parent in parents):
        return True
    tests_root = (repo_root / "tests").resolve()
    directory = file_path.parent.resolve()
    while directory.is_relative_to(tests_root):
        conftest = directory / "conftest.py"
        if conftest.is_file():
            try:
                conftest_tree = ast.parse(
                    conftest.read_text(encoding="utf-8"), filename=str(conftest)
                )
            except (OSError, SyntaxError, UnicodeError):
                return True
            if _pytestmark_is_blocking(conftest_tree):
                return True
        if directory == tests_root:
            break
        directory = directory.parent
    return False


def _class_parents(tree: ast.Module, node: ast.AST) -> list[ast.ClassDef]:
    parents: list[ast.ClassDef] = []
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef) and any(child is node for child in parent.body):
            parents.append(parent)
    return parents


def _node_is_destructive(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    return any(_decorator_is_destructive(decorator) for decorator in decorators)


def _pytestmark_is_blocking(tree: ast.Module) -> bool:
    for statement in tree.body:
        target_names = _assigned_names(statement)
        if "pytestmark" not in target_names:
            continue
        value = getattr(statement, "value", None)
        if value is not None and _mark_expression_blocks(value):
            return True
    return False


def _assigned_names(statement: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(statement, ast.Assign):
        for target in statement.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        names.add(statement.target.id)
    return names


def _decorator_is_destructive(expression: ast.AST) -> bool:
    """Recognize only an explicit destructive_local decorator."""

    target = expression.func if isinstance(expression, ast.Call) else expression
    if isinstance(target, ast.Attribute) and target.attr == "destructive_local":
        return _chain_has_mark(target.value)
    return False


def _mark_expression_blocks(expression: ast.AST) -> bool:
    """Block an explicit destructive mark and any pytestmark value that is not literal."""

    if isinstance(expression, ast.Call):
        return _mark_expression_blocks(expression.func)
    if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        return any(_mark_expression_blocks(element) for element in expression.elts)
    if isinstance(expression, ast.Attribute):
        if expression.attr == "destructive_local":
            return _chain_has_mark(expression.value)
        return False
    return True


def _chain_has_mark(expression: ast.AST) -> bool:
    if isinstance(expression, ast.Attribute):
        return expression.attr == "mark" or _chain_has_mark(expression.value)
    if isinstance(expression, ast.Name):
        return expression.id in {"pytest", "mark"}
    if isinstance(expression, ast.Call):
        return _chain_has_mark(expression.func)
    return False


def _destructive_detail(repo_root: Path, node_id: str) -> str:
    """Return chat-only detail when destructive_local blocks manual execution."""

    planner = repo_root / PLANNER_RELATIVE_PATH
    planner_hint = PLANNER_RELATIVE_PATH.as_posix()
    if planner.is_file():
        return (
            f"node={node_id}; destructive_local requires scoped planner "
            f"{planner_hint}; use /opsx-test"
        )
    return (
        f"node={node_id}; destructive_local blocked; planner missing at "
        f"{planner_hint}; use /opsx-test"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, evaluate one manual run, and print RUN_RESULT."""

    parser = argparse.ArgumentParser(description="Manual explicit /opsx-run-tests helper")
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--arguments", required=True)
    namespace = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    result = evaluate_manual_run(namespace.repo_root, namespace.arguments)
    sys.stdout.write(format_run_result(result) + "\n")
    if result.status == "PASS":
        return 0
    if result.status == "FAIL":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
