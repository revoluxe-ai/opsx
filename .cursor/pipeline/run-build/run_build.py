"""Manual explicit Poetry package build for target-native /opsx-run-build.

The helper accepts only build_recipe=poetry_package with an explicit owned
output_dir under `.cursor/pipeline/run-build/artifacts/`. It does not run
frozen S6 assignments, write verification receipts, or default-implicit builds.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


CONTRACT_ID = "OPSX-WB-RUN-BUILD-01"
ARTIFACT_PREFIX = Path(".cursor/pipeline/run-build/artifacts")
POETRY_BUILD_BACKEND = "poetry.core.masonry.api"
ALLOWED_RECIPE = "poetry_package"
RUNNER = Callable[[Sequence[str], Path], int]
OUTPUT_PART_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class BuildResult:
    """Chat-only build report. No field is a durable verification receipt path."""

    mode: str
    status: str
    reason: str
    build_recipe: str
    output_dir: str
    resolved_command: tuple[str, ...]
    exit_code: int | None
    detail: str = ""

    @property
    def runner_invoked(self) -> bool:
        """Return whether a build subprocess was started."""

        return self.exit_code is not None


def build_poetry_package_argv(output_dir: Path) -> tuple[str, ...]:
    """Build shell-free argv for `poetry build` into a non-default output directory."""

    return (
        "poetry",
        "build",
        "--output",
        output_dir.as_posix(),
        "--no-interaction",
    )


def poetry_build_runner(argv: Sequence[str], cwd: Path) -> int:
    """Run one validated argv with shell disabled."""

    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        shell=False,
        check=False,
    )
    return int(completed.returncode)


def evaluate_manual_build(
    repo_root: Path,
    arguments: str,
    runner: RUNNER | None = None,
) -> BuildResult:
    """Validate recipe/output_dir and optionally run a proven Poetry package build."""

    root = repo_root.resolve()
    parsed = _parse_arguments(arguments)
    if isinstance(parsed, BuildResult):
        return parsed
    mode, recipe, output_dir = parsed
    if mode == "bounded":
        return _blocked(
            "bounded",
            "BINDING_GAP",
            recipe,
            output_dir,
            "S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED",
        )
    if recipe == "npm_run_build":
        return _blocked(
            "manual",
            "EXECUTOR_ADAPTER_GAP",
            recipe,
            output_dir,
            "vue-npm frontend_build UNAVAILABLE on poetry-python target",
        )
    if recipe != ALLOWED_RECIPE:
        if recipe == "":
            return _blocked("rejected", "BUILD_RECIPE_REQUIRED", "", "")
        return _blocked("manual", "UNKNOWN_BUILD_RECIPE", recipe, output_dir)
    if not output_dir:
        return _blocked(
            "manual",
            "OUTPUT_DIR_REQUIRED",
            recipe,
            "",
            "explicit output_dir under .cursor/pipeline/run-build/artifacts/",
        )

    resolved_output = _resolve_owned_output_dir(root, output_dir)
    if isinstance(resolved_output, BuildResult):
        return resolved_output

    backend_reason = _poetry_backend_reason(root)
    if backend_reason is not None:
        return _blocked("manual", backend_reason, recipe, output_dir)

    argv = build_poetry_package_argv(resolved_output)
    invoke = runner if runner is not None else poetry_build_runner
    resolved_output.mkdir(parents=True, exist_ok=True)
    exit_code = invoke(argv, root)
    if exit_code == 0:
        return BuildResult(
            mode="manual",
            status="PASS",
            reason="",
            build_recipe=recipe,
            output_dir=output_dir,
            resolved_command=argv,
            exit_code=exit_code,
        )
    return BuildResult(
        mode="manual",
        status="FAIL",
        reason="BUILD_FAILED",
        build_recipe=recipe,
        output_dir=output_dir,
        resolved_command=argv,
        exit_code=exit_code,
    )


def format_build_result(result: BuildResult) -> str:
    """Render chat-only RUN_RESULT. This text is not verification evidence."""

    exit_display = "" if result.exit_code is None else str(result.exit_code)
    lines = [
        f"CONTRACT_ID: {CONTRACT_ID}",
        f"MODE: {result.mode}",
        f"STATUS: {result.status}",
        f"reason: {result.reason}",
        f"detail: {result.detail}",
        f"build_recipe: {result.build_recipe}",
        f"output_dir: {result.output_dir}",
        "resolved_command: "
        + json.dumps(list(result.resolved_command), ensure_ascii=True),
        f"exit_code: {exit_display}",
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
    build_recipe: str,
    output_dir: str,
    detail: str = "",
) -> BuildResult:
    return BuildResult(
        mode=mode,
        status="BLOCKED",
        reason=reason,
        build_recipe=build_recipe,
        output_dir=output_dir,
        resolved_command=(),
        exit_code=None,
        detail=detail,
    )


def _parse_arguments(
    arguments: str,
) -> tuple[str, str, str] | BuildResult:
    """Parse key=value tokens. Bounded mode wins over manual recipe."""

    tokens = arguments.split()
    if not tokens:
        return _blocked("rejected", "BUILD_RECIPE_REQUIRED", "", "")
    mode = ""
    recipe = ""
    output_dir = ""
    for token in tokens:
        if "=" not in token:
            return _blocked("rejected", "INVALID_ARGUMENT", "", "")
        key, value = token.split("=", 1)
        if key == "mode" and value == "bounded":
            return ("bounded", "", "")
        if key == "mode":
            mode = value
        elif key == "build_recipe":
            recipe = value
        elif key == "output_dir":
            output_dir = value
        else:
            return _blocked("rejected", "UNKNOWN_TOKEN", recipe, output_dir, key)
    if mode and mode != "manual":
        return _blocked("rejected", "UNKNOWN_MODE", recipe, output_dir)
    return ("", recipe, output_dir)


def _resolve_owned_output_dir(repo_root: Path, output_dir: str) -> Path | BuildResult:
    """Resolve output_dir to a path under the owned artifacts prefix."""

    if output_dir.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", output_dir):
        return _blocked("manual", "OUTPUT_DIR_ABSOLUTE_FORBIDDEN", ALLOWED_RECIPE, output_dir)
    if ".." in Path(output_dir).parts:
        return _blocked("manual", "PATH_TRAVERSAL", ALLOWED_RECIPE, output_dir)
    for part in Path(output_dir).parts:
        if part in {"", "."}:
            continue
        if not OUTPUT_PART_PATTERN.fullmatch(part):
            return _blocked("manual", "INVALID_OUTPUT_DIR", ALLOWED_RECIPE, output_dir)

    candidate = (repo_root / output_dir).resolve()
    prefix = (repo_root / ARTIFACT_PREFIX).resolve()
    try:
        candidate.relative_to(prefix)
    except ValueError:
        return _blocked(
            "manual",
            "OUTPUT_DIR_NOT_OWNED",
            ALLOWED_RECIPE,
            output_dir,
            f"must be under {ARTIFACT_PREFIX.as_posix()}/",
        )
    if candidate == prefix:
        return _blocked(
            "manual",
            "OUTPUT_DIR_NOT_OWNED",
            ALLOWED_RECIPE,
            output_dir,
            "must name a subdirectory under artifacts/",
        )
    return candidate


def _poetry_backend_reason(repo_root: Path) -> str | None:
    """Return a BLOCKED reason when Poetry package build backend is not provable."""

    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return "POETRY_PROJECT_MISSING"
    text = pyproject.read_text(encoding="utf-8")
    if "[tool.poetry]" not in text:
        return "POETRY_PROJECT_MISSING"
    if POETRY_BUILD_BACKEND not in text:
        return "POETRY_BUILD_BACKEND_MISSING"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, evaluate one manual build, and print RUN_RESULT."""

    parser = argparse.ArgumentParser(
        description="Manual explicit /opsx-run-build Poetry package helper"
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--arguments", required=True)
    namespace = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    result = evaluate_manual_build(namespace.repo_root, namespace.arguments)
    sys.stdout.write(format_build_result(result) + "\n")
    if result.status == "PASS":
        return 0
    if result.status == "FAIL":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
