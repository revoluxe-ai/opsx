#!/usr/bin/env python3
"""Portable OpenSpec/Cursor pre-write guard (size and context budgets).

Проверяет детерминированные лимиты размеров артефактов и контекстные бюджеты
по сценариям ``opsx_*`` (platform / change contour / total — раздельно).
Размеры файлов в байтах **не** эквивалентны токенам модели; при появлении
фактической token-телеметрии калибровка порогов выполняется отдельно от guard.
Не является полным семантическим валидатором OpenSpec: контракт
``openspec/config.yaml`` остаётся у ``openspec/tools/check_config_contract.py``.

CLI exit semantics:

- Без ``--strict``: ненулевой код только при findings уровня ``FAIL``.
- С ``--strict``: ненулевой код при любом ``FAIL`` или ``WARN`` (fail-closed).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml

GUARD_IMPLEMENTATION_ID = "stroy-openspec-guard-v0.2.0"


class Level(str, Enum):
    """Finding severity."""

    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Finding:
    """Single guard finding."""

    level: Level
    code: str
    message: str

    def format_line(self) -> str:
        return f"{self.level.value} {self.code}: {self.message}"


class GuardPolicyError(Exception):
    """Invalid guard policy or missing required inputs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class GuardRunTelemetry:
    """Evidence that scenario checks performed real I/O / parsing (anti-stub)."""

    scenario_id: str = ""
    config_bytes_read: int = 0
    command_surface_bytes: int = 0
    context_file_count: int = 0
    context_aggregate_bytes: int = 0
    platform_context_bytes: int = 0
    change_context_bytes: int = 0
    artifact_stat_count: int = 0
    baselines_loaded: bool = False


def _repo_root(explicit: Path | None) -> Path:
    return (explicit or Path.cwd()).resolve()


def _pipeline_repo_root() -> Path:
    """Корень репозитория с `.cursor/pipeline` (не зависит от ``--root`` тестов)."""

    return Path(__file__).resolve().parents[2]


def _policy_path(root: Path) -> Path:
    return resource_path(root, "tools", "openspec", "openspec_guard.yaml")


def _load_policy(root: Path) -> dict[str, Any]:
    path = _policy_path(root)
    if not path.is_file():
        raise GuardPolicyError("MISSING_POLICY", f"guard policy not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise GuardPolicyError("MALFORMED_POLICY", f"invalid YAML in {path}: {error}") from error
    if not isinstance(document, dict):
        raise GuardPolicyError("MALFORMED_POLICY", f"policy root must be a mapping: {path}")
    impl = document.get("implementation_id")
    if impl != GUARD_IMPLEMENTATION_ID:
        raise GuardPolicyError(
            "POLICY_IMPLEMENTATION_MISMATCH",
            f"expected implementation_id {GUARD_IMPLEMENTATION_ID!r}, got {impl!r}",
        )
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise GuardPolicyError("MALFORMED_POLICY", "scenarios mapping is required")
    return document


def _expand_globs(root: Path, patterns: Iterable[str]) -> list[Path]:
    """Expand glob patterns relative to ``root`` (deterministic sorted order)."""

    files: set[Path] = set()
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if "**" in pattern:
            anchor = pattern.split("**", 1)[0].rstrip("/")
            base = resource_path(root, anchor) if anchor else root
            if not base.exists():
                continue
            suffix = pattern.split("**", 1)[1].lstrip("/")
            glob_suffix = suffix or "*"
            for candidate in base.rglob(glob_suffix):
                if candidate.is_file():
                    files.add(candidate.resolve())
            continue
        matched = list(root.glob(pattern))
        if matched:
            for candidate in matched:
                if candidate.is_file():
                    files.add(candidate.resolve())
                elif candidate.is_dir():
                    for nested in candidate.rglob("*"):
                        if nested.is_file():
                            files.add(nested.resolve())
        else:
            direct = resource_path(root, pattern)
            if direct.is_file():
                files.add(direct.resolve())
    return sorted(files)


def _byte_size(path: Path) -> int:
    return path.stat().st_size


def _check_config_sections(
    root: Path,
    limits: dict[str, Any],
) -> tuple[list[Finding], int]:
    """Return findings and bytes read from ``openspec/config.yaml`` (0 if unread)."""

    findings: list[Finding] = []
    config_path = resource_path(root, "openspec", "config.yaml")
    if not config_path.is_file():
        findings.append(
            Finding(Level.FAIL, "MISSING_CONFIG", f"openspec/config.yaml not found under {root}")
        )
        return findings, 0
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        config_bytes_read = len(raw_text.encode("utf-8"))
        document = yaml.safe_load(raw_text)
    except OSError as error:
        findings.append(
            Finding(Level.FAIL, "MALFORMED_CONFIG", f"openspec/config.yaml read error: {error}")
        )
        return findings, 0
    except yaml.YAMLError as error:
        findings.append(
            Finding(Level.FAIL, "MALFORMED_CONFIG", f"openspec/config.yaml parse error: {error}")
        )
        return findings, config_bytes_read
    if not isinstance(document, dict):
        findings.append(
            Finding(Level.FAIL, "MALFORMED_CONFIG", "openspec/config.yaml root must be a mapping")
        )
        return findings, config_bytes_read

    section_limits = limits if limits else {}
    for section_name in ("context", "rules"):
        section_cfg = section_limits.get(section_name) or {}
        warn_limit = int(section_cfg.get("warn_bytes", 0))
        fail_limit = int(section_cfg.get("fail_bytes", 0))
        raw = document.get(section_name)
        if raw is None:
            findings.append(
                Finding(
                    Level.FAIL,
                    "MISSING_CONFIG_SECTION",
                    f"openspec/config.yaml missing required section: {section_name}",
                )
            )
            continue
        if section_name == "context":
            if not isinstance(raw, str):
                findings.append(
                    Finding(
                        Level.FAIL,
                        "MALFORMED_CONFIG",
                        "openspec/config.yaml context must be a string",
                    )
                )
                continue
            payload = raw.encode("utf-8")
        else:
            try:
                payload = yaml.safe_dump(raw, allow_unicode=True).encode("utf-8")
            except yaml.YAMLError as error:
                findings.append(
                    Finding(
                        Level.FAIL,
                        "MALFORMED_CONFIG",
                        f"openspec/config.yaml rules not serializable: {error}",
                    )
                )
                continue
        size = len(payload)
        if fail_limit and size > fail_limit:
            findings.append(
                Finding(
                    Level.FAIL,
                    "CONFIG_SECTION_TOO_LARGE",
                    f"config.{section_name} size {size} exceeds fail_bytes {fail_limit}",
                )
            )
        elif warn_limit and size > warn_limit:
            findings.append(
                Finding(
                    Level.WARN,
                    "CONFIG_SECTION_LARGE",
                    f"config.{section_name} size {size} exceeds warn_bytes {warn_limit}",
                )
            )
    return findings, config_bytes_read


def _artifact_limits_for_path(path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    """Выбрать tier лимитов по имени файла (planning / tasks / default)."""

    name = path.name
    tasks_cfg = policy.get("tasks_artifact_limits") or {}
    task_names = tasks_cfg.get("path_names") or ["tasks.md"]
    if name in task_names:
        return tasks_cfg
    planning_cfg = policy.get("planning_artifact_limits") or {}
    planning_names = planning_cfg.get("path_names") or ["design.md", "proposal.md", "plan.md"]
    if name in planning_names:
        return planning_cfg
    return policy.get("artifact_limits") or {}


def _check_artifact_limits(
    paths: list[Path],
    policy: dict[str, Any],
) -> tuple[list[Finding], int]:
    """Return findings and count of on-disk byte measurements performed."""

    findings: list[Finding] = []
    stat_count = 0
    for path in paths:
        limits = _artifact_limits_for_path(path, policy)
        warn_limit = int(limits.get("warn_bytes", 0))
        fail_limit = int(limits.get("fail_bytes", 0))
        size = _byte_size(path)
        stat_count += 1
        rel = path.as_posix()
        if fail_limit and size > fail_limit:
            findings.append(
                Finding(
                    Level.FAIL,
                    "ARTIFACT_TOO_LARGE",
                    f"{rel} size {size} exceeds fail_bytes {fail_limit}",
                )
            )
        elif warn_limit and size > warn_limit:
            findings.append(
                Finding(
                    Level.WARN,
                    "ARTIFACT_LARGE",
                    f"{rel} size {size} exceeds warn_bytes {warn_limit}",
                )
            )
    return findings, stat_count


def _merge_budget_layer(
    defaults: dict[str, Any],
    scenario_layer: dict[str, Any],
    legacy_flat: dict[str, Any],
) -> dict[str, Any]:
    """Слить platform/change/total слой: defaults → scenario override → legacy flat."""

    merged = dict(defaults)
    merged.update(scenario_layer)
    if legacy_flat.get("warn_bytes") and not scenario_layer:
        merged.setdefault("warn_bytes", int(legacy_flat["warn_bytes"]))
    if legacy_flat.get("fail_bytes") and not scenario_layer:
        merged.setdefault("fail_bytes", int(legacy_flat["fail_bytes"]))
    return merged


def _resolve_context_budget_layers(
    policy: dict[str, Any],
    scenario_cfg: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Вернуть (platform, change_contour, total) budget dicts для сценария."""

    defaults = policy.get("context_budget_defaults") or {}
    scenario_budget = scenario_cfg.get("context_budget") or {}
    legacy_flat = scenario_budget if "platform" not in scenario_budget else {}
    platform = _merge_budget_layer(
        defaults.get("platform") or {},
        scenario_budget.get("platform") or {},
        legacy_flat,
    )
    change = _merge_budget_layer(
        defaults.get("change_contour") or {},
        scenario_budget.get("change_contour") or {},
        {},
    )
    total = _merge_budget_layer(
        defaults.get("total") or {},
        scenario_budget.get("total") or {},
        {},
    )
    return platform, change, total


def _check_absolute_context_layer(
    measured_bytes: int,
    budget: dict[str, Any],
    *,
    scenario_id: str,
    code_prefix: str,
) -> list[Finding]:
    findings: list[Finding] = []
    warn_limit = int(budget.get("warn_bytes", 0))
    fail_limit = int(budget.get("fail_bytes", 0))
    if fail_limit and measured_bytes > fail_limit:
        findings.append(
            Finding(
                Level.FAIL,
                f"{code_prefix}_BUDGET_EXCEEDED",
                (
                    f"{scenario_id} {code_prefix.lower()} context {measured_bytes} "
                    f"exceeds fail_bytes {fail_limit}"
                ),
            )
        )
    elif warn_limit and measured_bytes > warn_limit:
        findings.append(
            Finding(
                Level.WARN,
                f"{code_prefix}_BUDGET_LARGE",
                (
                    f"{scenario_id} {code_prefix.lower()} context {measured_bytes} "
                    f"exceeds warn_bytes {warn_limit}"
                ),
            )
        )
    return findings


def _check_baseline_growth_platform(
    platform_bytes: int,
    *,
    scenario_id: str,
    baselines: dict[str, Any] | None,
    growth_policy: dict[str, Any] | None,
    platform_budget: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    if not growth_policy or growth_policy.get("enabled") is not True:
        return findings
    applies = growth_policy.get("applies_to", "platform_context_only")
    if applies not in ("platform_context_only", "platform"):
        return findings
    if baselines is None:
        findings.append(
            Finding(
                Level.FAIL,
                "MISSING_BASELINES",
                "baseline_growth enabled but baselines document was not loaded",
            )
        )
        return findings
    scenario_baselines = baselines.get("scenarios")
    if not isinstance(scenario_baselines, dict) or not scenario_baselines:
        findings.append(
            Finding(
                Level.FAIL,
                "MALFORMED_BASELINES",
                "guard_baselines.json scenarios must be a non-empty mapping",
            )
        )
        return findings
    baseline = scenario_baselines.get(scenario_id)
    if not isinstance(baseline, int) or baseline <= 0:
        findings.append(
            Finding(
                Level.FAIL,
                "MISSING_SCENARIO_BASELINE",
                (
                    f"{scenario_id}: baseline_growth enabled but no positive "
                    f"baseline entry in guard_baselines.json"
                ),
            )
        )
        return findings
    warn_ratio = float(growth_policy.get("warn_ratio", 1.25))
    fail_ratio = float(growth_policy.get("fail_ratio", 1.75))
    platform_fail = int(platform_budget.get("fail_bytes", 0))
    warn_growth = int(baseline * warn_ratio)
    fail_growth = int(baseline * fail_ratio)
    if platform_fail:
        fail_growth = min(fail_growth, platform_fail)
    if platform_bytes > fail_growth:
        findings.append(
            Finding(
                Level.FAIL,
                "BASELINE_GROWTH_EXCEEDED",
                (
                    f"{scenario_id} platform context {platform_bytes} exceeds baseline growth "
                    f"fail ({baseline} * {fail_ratio}, capped at platform fail = {fail_growth})"
                ),
            )
        )
    elif platform_bytes > warn_growth:
        findings.append(
            Finding(
                Level.WARN,
                "BASELINE_GROWTH_LARGE",
                (
                    f"{scenario_id} platform context {platform_bytes} exceeds baseline growth "
                    f"warn ({baseline} * {warn_ratio} = {warn_growth})"
                ),
            )
        )
    return findings


def _check_split_context_budget(
    *,
    platform_bytes: int,
    change_bytes: int,
    scenario_id: str,
    platform_budget: dict[str, Any],
    change_budget: dict[str, Any],
    total_budget: dict[str, Any],
    baselines: dict[str, Any] | None,
    growth_policy: dict[str, Any] | None,
    change_id: str | None,
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(
        _check_absolute_context_layer(
            platform_bytes,
            platform_budget,
            scenario_id=scenario_id,
            code_prefix="PLATFORM_CONTEXT",
        )
    )
    findings.extend(
        _check_baseline_growth_platform(
            platform_bytes,
            scenario_id=scenario_id,
            baselines=baselines,
            growth_policy=growth_policy,
            platform_budget=platform_budget,
        )
    )
    if change_id:
        findings.extend(
            _check_absolute_context_layer(
                change_bytes,
                change_budget,
                scenario_id=scenario_id,
                code_prefix="CHANGE_CONTOUR",
            )
        )
        total_bytes = platform_bytes + change_bytes
        findings.extend(
            _check_absolute_context_layer(
                total_bytes,
                total_budget,
                scenario_id=scenario_id,
                code_prefix="TOTAL_CONTEXT",
            )
        )
    return findings


def _load_baselines(root: Path, policy: dict[str, Any]) -> dict[str, Any] | None:
    growth = policy.get("baseline_growth") or {}
    if growth.get("enabled") is not True:
        return None
    rel = str(growth.get("baselines_path", "guard_baselines.json"))
    path = resource_path(root, "tools", "openspec", rel)
    if not path.is_file():
        raise GuardPolicyError("MISSING_BASELINES", f"baseline file not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GuardPolicyError("MALFORMED_BASELINES", f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise GuardPolicyError("MALFORMED_BASELINES", f"baselines root must be object: {path}")
    return document


def _resolve_required_command_surface(
    root: Path,
    scenario_cfg: dict[str, Any],
) -> Path:
    rel = scenario_cfg.get("command_surface")
    if not isinstance(rel, str) or not rel:
        raise GuardPolicyError("MALFORMED_POLICY", "scenario.command_surface is required")
    path = (resource_path(root, rel)).resolve()
    if not path.is_file():
        raise GuardPolicyError(
            "MISSING_COMMAND_SURFACE",
            f"command surface not found: {rel}",
        )
    return path


def _insufficient_telemetry_findings(
    telemetry: GuardRunTelemetry,
    policy: dict[str, Any],
) -> list[Finding]:
    """Fail closed when scenario execution did not record real measurements."""

    gaps: list[str] = []
    if not telemetry.scenario_id:
        gaps.append("scenario_id")
    if telemetry.config_bytes_read <= 0:
        gaps.append("config_bytes_read")
    if telemetry.command_surface_bytes <= 0:
        gaps.append("command_surface_bytes")
    if telemetry.context_file_count <= 0:
        gaps.append("context_file_count")
    if telemetry.context_aggregate_bytes <= 0:
        gaps.append("context_aggregate_bytes")
    if telemetry.platform_context_bytes <= 0:
        gaps.append("platform_context_bytes")
    if telemetry.artifact_stat_count <= 0:
        gaps.append("artifact_stat_count")

    growth = policy.get("baseline_growth") or {}
    if growth.get("enabled") is True and not telemetry.baselines_loaded:
        gaps.append("baselines_loaded")

    if not gaps:
        return []
    return [
        Finding(
            Level.FAIL,
            "GUARD_REGRESSION_WEAKENED",
            "insufficient run telemetry (stub or no-op checks): " + ", ".join(gaps),
        )
    ]


def run_scenario(
    root: Path,
    scenario_id: str,
    policy: dict[str, Any],
    *,
    change: str | None = None,
) -> tuple[list[Finding], GuardRunTelemetry]:
    """Execute configured checks for ``scenario_id``; return findings and run telemetry."""

    scenarios = policy.get("scenarios")
    if not isinstance(scenarios, dict):
        raise GuardPolicyError("MALFORMED_POLICY", "scenarios must be a mapping")
    scenario_cfg = scenarios.get(scenario_id)
    if not isinstance(scenario_cfg, dict):
        raise GuardPolicyError("UNKNOWN_SCENARIO", f"unknown scenario: {scenario_id}")

    findings: list[Finding] = []
    telemetry = GuardRunTelemetry(scenario_id=scenario_id)

    config_findings, config_bytes = _check_config_sections(
        root,
        policy.get("config_sections") or {},
    )
    findings.extend(config_findings)
    telemetry.config_bytes_read = config_bytes

    command_surface = _resolve_required_command_surface(root, scenario_cfg)
    telemetry.command_surface_bytes = _byte_size(command_surface)
    surface_findings, surface_stats = _check_artifact_limits([command_surface], policy)
    findings.extend(surface_findings)
    telemetry.artifact_stat_count += surface_stats

    platform_includes = list(scenario_cfg.get("context_includes") or [])
    platform_paths = _expand_globs(root, platform_includes)
    change_paths: list[Path] = []
    if change:
        change_paths = _expand_globs(root, [f"openspec/changes/{change}/**/*"])
    context_paths = sorted(set(platform_paths) | set(change_paths))
    if not platform_paths:
        findings.append(
            Finding(
                Level.FAIL,
                "EMPTY_CONTEXT",
                f"{scenario_id}: no files matched platform context_includes",
            )
        )
    platform_bytes = sum(_byte_size(path) for path in platform_paths)
    change_bytes = sum(_byte_size(path) for path in change_paths)
    aggregate = platform_bytes + change_bytes
    baselines = _load_baselines(root, policy)
    telemetry.baselines_loaded = baselines is not None
    platform_budget, change_budget, total_budget = _resolve_context_budget_layers(
        policy,
        scenario_cfg,
    )
    findings.extend(
        _check_split_context_budget(
            platform_bytes=platform_bytes,
            change_bytes=change_bytes,
            scenario_id=scenario_id,
            platform_budget=platform_budget,
            change_budget=change_budget,
            total_budget=total_budget,
            baselines=baselines,
            growth_policy=policy.get("baseline_growth"),
            change_id=change,
        )
    )
    telemetry.context_file_count = len(context_paths)
    telemetry.context_aggregate_bytes = aggregate
    telemetry.platform_context_bytes = platform_bytes
    telemetry.change_context_bytes = change_bytes

    artifact_targets = list(context_paths)
    if command_surface not in artifact_targets:
        artifact_targets.append(command_surface)
    artifact_findings, artifact_stats = _check_artifact_limits(artifact_targets, policy)
    findings.extend(artifact_findings)
    telemetry.artifact_stat_count += artifact_stats

    if scenario_id in ("opsx_continue", "opsx_ff", "opsx_update", "opsx_sync", "opsx_apply"):
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from tools.openspec.opsx_mutation_authority import enforce_guard_scenario

        auth = enforce_guard_scenario(root, scenario_id)
        if auth is not None and not auth.ok:
            findings.append(
                Finding(
                    Level.FAIL,
                    "MUTATION_AUTHORITY",
                    f"{auth.reason}: {auth.detail} (next: {auth.next_action})",
                )
            )

    if scenario_id == "opsx_continue":
        contract_script = Path(__file__).resolve().parent / "opsx_continue_skill_contract.py"
        env = os.environ.copy()
        module_repo = contract_script.resolve().parents[2]
        prefixes = [str(root), str(module_repo)]
        existing = env.get("PYTHONPATH", "")
        if existing:
            prefixes.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(prefixes)
        proc = subprocess.run(
            [
                sys.executable,
                str(contract_script),
                "--guard-skill-contract",
                "--root",
                str(root),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "skill contract check failed").strip()
            for line in detail.splitlines():
                message = line.strip()
                if message:
                    findings.append(
                        Finding(
                            Level.FAIL,
                            "SKILL_CONTRACT",
                            message,
                        )
                    )
            if not detail:
                findings.append(
                    Finding(
                        Level.FAIL,
                        "SKILL_CONTRACT",
                        "opsx_continue skill contract subprocess failed",
                    )
                )

    return findings, telemetry


def _exit_code(findings: list[Finding], *, strict: bool) -> int:
    if any(item.level == Level.FAIL for item in findings):
        return 1
    if strict and any(item.level == Level.WARN for item in findings):
        return 1
    return 0


def _print_findings(findings: list[Finding]) -> None:
    for item in findings:
        print(item.format_line(), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenSpec pre-write guard (portable size/context budgets).",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Configured scenario id (opsx_continue, opsx_ff, opsx_sync, opsx_update).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on FAIL (and WARN when strict).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: current working directory).",
    )
    parser.add_argument(
        "--change",
        default=None,
        help="Optional OpenSpec change id (extends context includes).",
    )
    parser.add_argument(
        "--guard-stage",
        choices=("early", "planning_readiness", "none"),
        default=None,
        help=(
            "Channel-impact enforcement stage: early= budget only; "
            "planning_readiness= run planning-gate (fail-closed); "
            "none= skip channel-impact subprocess."
        ),
    )
    return parser


def _resolve_guard_stage(explicit: str | None, change: str | None) -> str:
    if explicit:
        return explicit
    if change:
        return "early"
    return "none"


def main(argv: list[str] | None = None) -> int:
    """CLI entry: load policy, run scenario checks, print findings to stderr."""

    args = build_parser().parse_args(argv)
    root = _repo_root(args.root)
    guard_stage = _resolve_guard_stage(args.guard_stage, args.change)
    try:
        policy = _load_policy(root)
        findings, telemetry = run_scenario(root, args.scenario, policy, change=args.change)
        if args.strict:
            findings.extend(_insufficient_telemetry_findings(telemetry, policy))
    except GuardPolicyError as error:
        print(f"FAIL {error.code}: {error}", file=sys.stderr)
        return 1

    # The mandatory Architecture stage is separate from dependency/library evidence.
    # Planning commands can create upstream artifacts; once design/tasks exist,
    # their architecture review must be current. Apply always requires it.
    if args.change and args.scenario in (
        "opsx_continue", "opsx_ff", "opsx_update", "opsx_apply", "opsx_sync", "opsx_verify_archive"
    ):
        stage_script = Path(__file__).with_name("architecture_stage_gate.py")
        mode = "check" if args.scenario in ("opsx_apply", "opsx_sync", "opsx_verify_archive") else "check-if-design"
        proc = subprocess.run(
            [sys.executable, str(stage_script), mode,
             "--root", str(root), "--change", args.change],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            reason = (proc.stdout or proc.stderr or "mandatory architecture gate failed").strip()
            findings.append(Finding(Level.FAIL, "ARCHITECTURE_STAGE_BLOCKED",
                                    reason[:1200]))

    if (
        guard_stage == "planning_readiness"
        and args.change
        and args.scenario in ("opsx_ff", "opsx_update", "opsx_continue")
    ):
        guard_script = (
            _pipeline_repo_root()
            / ".cursor"
            / "pipeline"
            / "channel-impact"
            / "channel_impact_guard.py"
        )
        if guard_script.is_file():
            proc = subprocess.run(
                [
                    sys.executable,
                    str(guard_script),
                    "planning-gate",
                    "--change",
                    args.change,
                    "--root",
                    str(root),
                ],
                cwd=_pipeline_repo_root(),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "channel impact planning gate failed").strip()
                findings.append(
                    Finding(
                        Level.FAIL,
                        "CHANNEL_IMPACT_PLANNING",
                        detail.splitlines()[0] if detail else "planning gate failed",
                    )
                )

    if args.change and args.scenario == "opsx_verify_archive":
        verify_script = _pipeline_repo_root() / "tools" / "opsx" / "verify_channel_impact_gate.py"
        if verify_script.is_file():
            proc = subprocess.run(
                [
                    sys.executable,
                    str(verify_script),
                    "--change",
                    args.change,
                    "--base",
                    "origin/stage",
                    "--head",
                    "HEAD",
                ],
                cwd=_pipeline_repo_root(),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "channel impact verify gate failed").strip()
                findings.append(
                    Finding(
                        Level.FAIL,
                        "CHANNEL_IMPACT_VERIFY",
                        detail.splitlines()[0] if detail else "verify gate failed",
                    )
                )

    if findings:
        _print_findings(findings)
    else:
        print(f"PASS scenario={args.scenario} implementation={GUARD_IMPLEMENTATION_ID}")
    return _exit_code(findings, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
