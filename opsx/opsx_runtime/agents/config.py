"""Политика read-scope для adapter hops (единый restrict_allow для всех агентов).

``GLOBAL_READ_SCOPE`` задаёт allow/deny; ``AGENT_READ_OVERRIDES`` и slice work
paths только расширяют restrict. Адаптер получает ``ReadScope`` без доменной логики.

Запуск продукта: webhook API — ``uvicorn stroy.api:get_app`` (Docker/k8s);
боты и фон — ``poetry run stroy-worker --role …`` (``stroy.cli``).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")

_GLOBAL_DENY: tuple[str, ...] = (
    "**/*.log",
    "**/*.live.log",
    "**/*.live.ndjson",
    "openspec/changes/**/verify/**",
    "openspec/changes/**/scenario-coverage/*.receipt.json",
    "openspec/changes/**/scenario-coverage/test_trace_matrix.json",
    "openspec/changes/**/scenario-coverage/row_walk_journal.json",
    "openspec/changes/**/scenario-row-streams/**",
    "opsx/logs/**",
    "opsx/receipts/**",
    "opsx/runs/**",
    ".opsx/runs/**",
    "opsx/runs/dialogs/**",
    "**/checkpoints.sqlite",
    "**/checkpoints.sqlite-wal",
    "**/checkpoints.sqlite-shm",
    ".git/**",
    ".env",
    ".env.*",
    "**/.env",
    ".venv/**",
    "node_modules/**",
    "**/__pycache__/**",
)

# Единый allow-list для всех SDK-агентов (grep/glob/read/shell/edit).
_GLOBAL_RESTRICT_ALLOW: tuple[str, ...] = (
    "src/**",
    "app/**",
    "tests/**",
    "openspec/changes/**",
    "openspec/decisions/**",
    "openspec/specs/**",
    "migrations/**",
    "alembic.ini",
    "pyproject.toml",
    "poetry.lock",
)

# Для shell: фатально только явный доступ к артефактам (не allow-miss, не «вне репо»).
_SHELL_FATAL_DENY: tuple[str, ...] = (
    "**/*.live.log",
    "**/*.live.ndjson",
    "**/checkpoints.sqlite",
    "**/checkpoints.sqlite-wal",
    "**/checkpoints.sqlite-shm",
    "opsx/runs/dialogs/**",
    "opsx/logs/**",
    "opsx/receipts/**",
    "opsx/runs/**",
    ".opsx/runs/**",
)

READ_CLASS_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "grep",
        "glob",
        "ls",
        "list_dir",
        "listdir",
        "search",
    }
)

WRITE_CLASS_TOOLS: frozenset[str] = frozenset(
    {
        "edit",
        "write",
        "delete",
        "apply_patch",
        "strreplace",
    }
)


@dataclass(frozen=True, slots=True)
class ReadScope:
    """Разрешённые и запрещённые пути (repo-relative globs)."""

    allow: tuple[str, ...]
    deny: tuple[str, ...]
    restrict_allow: tuple[str, ...] | None = None
    restrict_write_allow: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AgentReadOverride:
    """Сужение поверх глобального потолка для одного агента."""

    allow: tuple[str, ...] | None = None
    extra_deny: tuple[str, ...] = ()
    extra_allow: tuple[str, ...] = ()


GLOBAL_READ_SCOPE = ReadScope(
    allow=(),
    deny=_GLOBAL_DENY,
    restrict_allow=_GLOBAL_RESTRICT_ALLOW,
)

AGENT_READ_OVERRIDES: dict[str, AgentReadOverride] = {}


def read_scope_enforcement_mode() -> str:
    """``enforce`` | ``warn`` | ``off`` — из ``OPSX_READ_SCOPE_MODE``."""
    raw = os.environ.get("OPSX_READ_SCOPE_MODE", "enforce").strip().lower()
    if raw in {"enforce", "warn", "off"}:
        return raw
    return "enforce"


class ReadScopeViolation(RuntimeError):
    """Tool-call затронул путь вне effective read scope."""

    def __init__(
        self,
        *,
        tool: str,
        path: str,
        rule: str = "",
        command: str = "",
        pattern_hint: str = "",
        agent_id: str = "",
        run_id: str = "",
    ) -> None:
        self.tool = tool
        self.path = path
        self.rule = rule
        self.command = command
        self.agent_id = agent_id
        self.run_id = run_id
        hint = pattern_hint or rule
        super().__init__(
            f"READ_SCOPE_VIOLATION tool={tool} path={path!r}"
            + (f" rule={hint!r}" if hint else "")
        )


@dataclass(slots=True)
class ReadScopeCheckResult:
    """Результат проверки tool-call."""

    violations: list[ReadScopeViolation] = field(default_factory=list)
    notes: list[dict[str, str]] = field(default_factory=list)


def _normalize_repo_rel(path: str) -> str:
    token = str(path or "").replace("\\", "/").strip()
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if len(token) >= 2 and token[1] == ":":
        parts = token.split("/")
        if len(parts) > 1:
            token = "/".join(parts[1:])
    return token


def _suffix_after_repo_marker(path_posix: str, repo_name: str) -> str | None:
    """Fallback для absolute без диска: ``Users/.../repo_name/openspec/...``."""
    lowered = path_posix.lower()
    marker = f"/{repo_name.lower()}/"
    idx = lowered.find(marker)
    if idx >= 0:
        return path_posix[idx + len(marker) :]
    prefix = f"{repo_name.lower()}/"
    if lowered.startswith(prefix):
        return path_posix[len(prefix) :]
    return None


def to_repo_relative(path: str, repo_root: Path) -> str:
    """Привести tool path к repo-relative POSIX для сопоставления с allow/deny."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    repo = repo_root.resolve()
    repo_name = repo.name
    token = raw.replace("\\", "/")

    if not token.startswith("/") and not _WIN_ABS.match(raw):
        head = token.split("/", 1)[0]
        if ":" not in head:
            marker_hit = _suffix_after_repo_marker(token, repo_name)
            if marker_hit is not None:
                return _normalize_repo_rel(marker_hit)
            return _normalize_repo_rel(raw)

    repo_s = str(repo).replace("\\", "/")
    try:
        candidate = Path(raw)
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        res_s = str(resolved).replace("\\", "/")
        if os.name == "nt":
            repo_cmp = repo_s.lower()
            res_cmp = res_s.lower()
            if res_cmp == repo_cmp:
                return ""
            if res_cmp.startswith(repo_cmp + "/"):
                return res_s[len(repo_s) + 1 :]
        else:
            try:
                return resolved.relative_to(repo).as_posix()
            except ValueError:
                pass
        if res_s.startswith(repo_s + "/") or res_s == repo_s:
            return res_s[len(repo_s) + 1 :]
    except (OSError, ValueError, TypeError):
        pass

    marker_hit = _suffix_after_repo_marker(token, repo_name)
    if marker_hit is not None:
        return _normalize_repo_rel(marker_hit)

    return _normalize_repo_rel(raw)


def path_under_repo_root(path: str, repo_root: Path | None) -> bool:
    """Путь внутри ``repo_root`` (абсолютные вне репо — False)."""
    if repo_root is None:
        return True
    raw = str(path or "").strip()
    if not raw:
        return True
    norm = raw.replace("\\", "/")
    if not (norm.startswith("/") or _WIN_ABS.match(raw)):
        return True
    if norm.startswith(("/tmp/", "/var/", "/etc/", "/usr/", "/dev/")):
        return False
    repo = repo_root.resolve()
    try:
        candidate = Path(raw)
        resolved = candidate.resolve()
        resolved.relative_to(repo)
        return True
    except (OSError, ValueError):
        pass
    repo_s = str(repo).replace("\\", "/").lower()
    if norm.lower() == repo_s or norm.lower().startswith(repo_s + "/"):
        return True
    rel = to_repo_relative(raw, repo_root)
    if not rel or ".." in rel.split("/"):
        return False
    try:
        (resource_path(repo, rel)).resolve().relative_to(repo)
        return True
    except (OSError, ValueError):
        return False


def _effective_repo_rel(path: str, repo_root: Path | None) -> str:
    if repo_root is not None:
        return to_repo_relative(path, repo_root)
    return _normalize_repo_rel(path)


def _glob_pattern_matches(rel: str, pat: str) -> bool:
    """Glob по сегментам пути, включая ``**`` между каталогами."""
    pat = pat.replace("\\", "/").strip()
    if pat in {"*", "**"}:
        return True
    regex_parts: list[str] = []
    for segment in pat.split("/"):
        if segment == "**":
            regex_parts.append("(?:.*)")
        else:
            seg_re = re.escape(segment).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
            seg_re = seg_re.replace(r"\?", "[^/]")
            regex_parts.append(seg_re)
    regex = "^" + "/".join(regex_parts) + "$"
    return re.match(regex, rel, re.IGNORECASE) is not None


def path_matches_pattern(rel_path: str, pattern: str) -> bool:
    """Сопоставить repo-relative путь с glob (``**`` в префиксе/суффиксе)."""
    rel = _normalize_repo_rel(rel_path)
    if not rel:
        return pattern in {"*", "**"} or pattern.endswith("/**")
    pat = pattern.replace("\\", "/").strip()
    if pat in {"*", "**"}:
        return True
    if "**" in pat or "*" in pat or "?" in pat:
        if _glob_pattern_matches(rel, pat):
            return True
    if pat.endswith("/**"):
        root = pat[:-3].rstrip("/")
        if not root:
            return True
        return rel == root or rel.startswith(f"{root}/")
    if pat.startswith("**/"):
        tail = pat[3:]
        if tail.startswith("*.") and "/" not in tail:
            return rel.endswith(tail[1:])
        if "**" not in tail:
            return fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(
                rel.split("/")[-1],
                tail,
            )
    return fnmatch.fnmatchcase(rel, pat)


def _read_scope_candidates(rel_path: str, repo_root: Path | None) -> list[str]:
    """Варианты repo-relative пути для allow/deny (shell часто пишет ``/changes/...``)."""
    rel = _effective_repo_rel(rel_path, repo_root)
    if not rel:
        return [""]
    norm = _normalize_repo_rel(rel).lstrip("/")
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in (rel, norm):
        token = _normalize_repo_rel(raw)
        if token not in seen:
            seen.add(token)
            candidates.append(token)
    if norm.startswith("changes/"):
        aliased = f"openspec/{norm}"
        if aliased not in seen:
            seen.add(aliased)
            candidates.append(aliased)
    return candidates or [norm]


def _path_denied_by_patterns(rel: str, patterns: Sequence[str]) -> str | None:
    if not rel:
        return None
    for pattern in patterns:
        if path_matches_pattern(rel, pattern):
            return pattern
    return None


def is_repo_root_tool_path(path: str, repo_root: Path | None) -> bool:
    """Запретить grep/glob/read по корню репо (ловит ``**/…`` по всему дереву)."""
    raw = str(path or "").strip()
    if not raw:
        return True
    if repo_root is None:
        return False
    rel = to_repo_relative(raw, repo_root)
    if not rel:
        return True
    repo_s = str(repo_root.resolve()).replace("\\", "/").rstrip("/")
    norm = raw.replace("\\", "/").rstrip("/")
    return norm.lower() == repo_s.lower()


def _path_allowed_for_single_rel(
    scope: ReadScope,
    rel: str,
    *,
    restrict_allow: tuple[str, ...] | None = None,
) -> bool:
    patterns = (
        restrict_allow
        if restrict_allow is not None
        else scope.restrict_allow
    )
    if not rel:
        return not bool(patterns)
    if _path_denied_by_patterns(rel, scope.deny):
        return False
    if patterns:
        return any(path_matches_pattern(rel, p) for p in patterns)
    if scope.allow:
        return any(path_matches_pattern(rel, p) for p in scope.allow)
    return False


def path_allowed_by_scope(
    scope: ReadScope,
    rel_path: str,
    *,
    repo_root: Path | None = None,
    restrict_allow: tuple[str, ...] | None = None,
) -> bool:
    """``deny`` побеждает; deny-first разрешает остальное внутри репо."""
    if repo_root is not None and not path_under_repo_root(rel_path, repo_root):
        return False
    for rel in _read_scope_candidates(rel_path, repo_root):
        if _path_allowed_for_single_rel(
            scope,
            rel,
            restrict_allow=restrict_allow,
        ):
            return True
    return False


def effective_restrict_allow_for_tool(
    scope: ReadScope,
    tool_name: str,
) -> tuple[str, ...] | None:
    """Read/shell — ``restrict_allow``; write/edit — ``restrict_write_allow`` если задан."""
    if _tool_class(tool_name) == "write" and scope.restrict_write_allow:
        return scope.restrict_write_allow
    return scope.restrict_allow


def resolve_read_scope(agent_key: str | None) -> ReadScope:
    """Собрать effective scope: профиль роли ∩ потолок ± per-agent override."""
    from agents.read_scope_profiles import restrict_allow_for_agent
    from agents.repair.product_surface import (
        is_repair_product_hop,
        repair_product_write_globs,
    )

    deny = GLOBAL_READ_SCOPE.deny
    profile = restrict_allow_for_agent(agent_key)
    restrict: tuple[str, ...] = profile or (GLOBAL_READ_SCOPE.restrict_allow or ())
    key = str(agent_key or "").strip()
    write_restrict: tuple[str, ...] | None = None
    from agents.process_repair.meta_surface import (
        PROCESS_SUPERVISOR_FIX_KEY,
        supervisor_read_globs,
        supervisor_self_deny_repo_globs,
        supervisor_write_globs,
    )

    if key == PROCESS_SUPERVISOR_FIX_KEY:
        restrict = supervisor_read_globs()
        write_restrict = supervisor_write_globs()
        deny = tuple(dict.fromkeys((*deny, *supervisor_self_deny_repo_globs())))
    elif is_repair_product_hop(key):
        write_restrict = repair_product_write_globs()
    override = AGENT_READ_OVERRIDES.get(key) if key else None
    if override is not None:
        deny = tuple(sorted(set(deny) | set(override.extra_deny)))
        if override.extra_allow:
            restrict = tuple(sorted(set(restrict) | set(override.extra_allow)))
        if override.allow:
            restrict = tuple(override.allow)
    if key != PROCESS_SUPERVISOR_FIX_KEY:
        from agents.read_scope_profiles import product_source_root_globs_from_env

        extra_product = product_source_root_globs_from_env()
        if extra_product:
            restrict = tuple(dict.fromkeys((*restrict, *extra_product)))
            if write_restrict is not None:
                write_restrict = tuple(dict.fromkeys((*write_restrict, *extra_product)))
    return ReadScope(
        allow=(),
        deny=deny,
        restrict_allow=restrict or None,
        restrict_write_allow=write_restrict,
    )


def resolve_effective_read_scope(
    agent_key: str | None,
    state: Mapping[str, Any] | None,
) -> ReadScope:
    """Scope для hop: базовая политика для всех агентов + slice work paths при наличии."""
    scope = resolve_read_scope(agent_key)
    if state is None:
        return scope
    from agents.implementation.hop_read_scope import (
        augment_scope_with_change_declared_paths,
        augment_scope_with_slice_work_paths,
    )

    scope = augment_scope_with_slice_work_paths(scope, state, agent_key=agent_key)
    return augment_scope_with_change_declared_paths(scope, state, agent_key=agent_key)


def _is_glob_or_search_pattern(token: str) -> bool:
    """Строка — glob/regex для tool, не каталог или файл для scope."""
    t = token.strip()
    if "**" in t or "?" in t:
        return True
    if t.startswith("*") or t.endswith("*"):
        return True
    return False


def _path_keys_from_mapping(args: Mapping[str, Any], *, tool_name: str) -> list[str]:
    """Только filesystem-аргументы; ``pattern`` / ``glob_pattern`` не пути."""
    name = tool_name.strip().lower()
    if name == "glob":
        keys = ("path", "target_directory", "targetDirectory")
    elif name == "grep":
        keys = ("path", "target_directory", "targetDirectory", "file", "filename")
    else:
        keys = (
            "path",
            "target_directory",
            "targetDirectory",
            "file",
            "filename",
        )
    out: list[str] = []
    for key in keys:
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            token = raw.strip()
            if _is_glob_or_search_pattern(token):
                continue
            out.append(token)
    return out


def _tokenize_shell_command(command: str) -> list[str]:
    """Целые токены команды (кавычки, без регэксп-фрагментов)."""
    cmd = str(command or "").strip()
    if not cmd:
        return []
    try:
        posix = cmd.replace("\\", "/")
        if os.name == "nt" and ";" in cmd and "&&" not in cmd:
            parts: list[str] = []
            for segment in cmd.split(";"):
                seg = segment.strip()
                if not seg:
                    continue
                try:
                    parts.extend(shlex.split(seg, posix=False))
                except ValueError:
                    parts.extend(seg.split())
            return parts
        return shlex.split(posix, posix=True)
    except ValueError:
        return cmd.split()


def _shell_token_may_be_path(token: str) -> bool:
    """Токен похож на путь к файлу (не флаг, не URL, не /dev/null)."""
    t = token.strip().strip("\"'")
    if not t or t.startswith("-"):
        return False
    # JS/Python literals в ``node -e`` (``paths=[...]``, ``const paths=``) — не пути.
    if t.lower().startswith("paths=") or t.lower().startswith("paths=["):
        return False
    if "://" in t:
        return False
    if t in {"/dev/null", "/dev/zero", "nul", "NUL"}:
        return False
    if t in {"/", "\\"}:
        return False
    if "/" in t or "\\" in t:
        return True
    if _WIN_ABS.match(t):
        return True
    # Bare ``RED.json`` / ``evil.json`` в ``node -e`` — строки в коде, не пути на диск.
    if "/" not in t and "\\" not in t and t.lower().endswith(
        (".json", ".md", ".yaml", ".yml")
    ):
        return False
    if t.endswith(
        (".py", ".mjs", ".json", ".md", ".yaml", ".yml", ".sql", ".toml")
    ):
        return True
    return False


_SHELL_VALUE_FLAGS = frozenset(
    {
        "-m",
        "--message",
        "-c",
        "-e",
        "/c",
        "/C",
    }
)


_SHELL_CD_OPS = frozenset(
    {"cd", "pushd", "chdir", "set-location", "push-location"}
)


def _shell_token_is_io_redirect(token: str) -> bool:
    """Токен перенаправления stdout/stderr — не аргумент ``pytest``."""
    bare = token.strip().strip("\"'")
    if bare in {"2>&1", ">&1", ">&2", "2>nul", "2>NUL", "1>nul", "1>NUL"}:
        return True
    if ">" in bare and not bare.startswith("-"):
        tail = bare.lstrip("0123456789")
        if tail.startswith(">>") or tail.startswith(">"):
            return True
    return False


def _pytest_invocation_has_explicit_test_files(pytest_args: Sequence[str]) -> bool:
    """``pytest`` с явными целями: ``.py``, ``::`` или каталог строго под ``tests/``.

    Запрещены ``.``, ``tests`` (включая ``tests/``) и прочие не-тестовые каталоги.
    """
    has_explicit = False
    for arg in pytest_args:
        bare = arg.strip().strip("\"'")
        if _shell_token_is_io_redirect(bare):
            continue
        if bare.startswith("-"):
            continue
        if bare in {".", ".."}:
            return False
        norm = bare.replace("\\", "/").rstrip("/")
        if norm == "tests":
            return False
        if norm.endswith(".py") or "::" in bare:
            has_explicit = True
            continue
        if norm.startswith("tests/"):
            has_explicit = True
            continue
        if "/" in norm or "\\" in bare:
            return False
        return False
    return has_explicit


def _shell_rest_has_explicit_pytest_invocation(tokens: Sequence[str]) -> bool:
    """После cwd-hint: ``poetry run pytest`` или ``pytest`` с явными целями."""
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low == "pytest":
            return _pytest_invocation_has_explicit_test_files(tokens[i + 1 :])
        if low != "poetry" or i + 2 >= len(tokens):
            continue
        if tokens[i + 1].lower() == "run" and tokens[i + 2].lower() == "pytest":
            return _pytest_invocation_has_explicit_test_files(tokens[i + 3 :])
    return False


def _shell_poetry_dash_c_explicit_pytest(
    command: str,
    repo_root: Path,
) -> bool:
    """``poetry -C/-c <repo_root> run pytest <explicit test paths>`` — cwd hint."""
    tokens = _tokenize_shell_command(command)
    for i, tok in enumerate(tokens):
        if tok.lower() != "poetry" or i + 2 >= len(tokens):
            continue
        flag = tokens[i + 1]
        if flag not in {"-C", "-c"}:
            continue
        target = tokens[i + 2].strip().strip("\"'")
        if not is_repo_root_tool_path(target, repo_root):
            continue
        rest = tokens[i + 3 :]
        if len(rest) < 2 or rest[0].lower() != "run" or rest[1].lower() != "pytest":
            return False
        return _pytest_invocation_has_explicit_test_files(rest[2:])
    return False


def _shell_poetry_directory_explicit_pytest(
    command: str,
    repo_root: Path,
) -> bool:
    """``poetry --directory <repo_root> run pytest <explicit test paths>`` — cwd hint."""
    tokens = _tokenize_shell_command(command)
    for i, tok in enumerate(tokens):
        if tok.lower() != "poetry" or i + 2 >= len(tokens):
            continue
        if tokens[i + 1].lower() != "--directory":
            continue
        target = tokens[i + 2].strip().strip("\"'")
        if not is_repo_root_tool_path(target, repo_root):
            continue
        rest = tokens[i + 3 :]
        if len(rest) < 2 or rest[0].lower() != "run" or rest[1].lower() != "pytest":
            return False
        return _pytest_invocation_has_explicit_test_files(rest[2:])
    return False


def _shell_path_is_cd_repo_root(
    command: str,
    path: str,
    repo_root: Path | None,
) -> bool:
    """Cwd-hint на repo root + явный ``pytest``/``poetry run pytest`` — не repo scan."""
    if repo_root is None or not is_repo_root_tool_path(path, repo_root):
        return False
    if _shell_poetry_dash_c_explicit_pytest(command, repo_root):
        return True
    if _shell_poetry_directory_explicit_pytest(command, repo_root):
        return True
    norm_path = str(path).replace("\\", "/").rstrip("/").lower()
    tokens = _tokenize_shell_command(command)
    for i, tok in enumerate(tokens):
        if tok.lower() not in _SHELL_CD_OPS or i + 1 >= len(tokens):
            continue
        target = tokens[i + 1].strip().strip("\"'")
        norm_target = target.replace("\\", "/").rstrip("/").lower()
        if norm_target != norm_path:
            continue
        return _shell_rest_has_explicit_pytest_invocation(tokens[i + 2 :])
    return False


def paths_from_shell_command(command: str) -> list[str]:
    """Пути из shell — только полные токены, без substring из python -c."""
    paths: list[str] = []
    tokens = _tokenize_shell_command(command)
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        bare = token.strip().strip("\"'")
        if bare in _SHELL_VALUE_FLAGS:
            skip_next = True
            continue
        if _shell_token_may_be_path(token):
            paths.append(bare)
    return paths


def _normalize_tool_args(args: Mapping[str, Any]) -> Mapping[str, Any]:
    """Слить вложенный ``args`` (MCP custom-user-tools) с верхним уровнем."""
    inner = args.get("args")
    if not isinstance(inner, Mapping):
        return dict(args)
    merged = dict(inner)
    for key, value in args.items():
        if key == "args" or key in merged:
            continue
        merged[key] = value
    return merged


def paths_from_tool_args(tool_name: str, args: Any) -> list[str]:
    """Извлечь repo-relative пути из args built-in tool."""
    name = str(tool_name or "").strip().lower()
    if not isinstance(args, Mapping):
        return []
    args = _normalize_tool_args(args)
    paths = _path_keys_from_mapping(args, tool_name=name)
    if name == "shell":
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, str) and cmd.strip():
            paths.extend(paths_from_shell_command(cmd))
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if raw not in seen:
            seen.add(raw)
            deduped.append(raw)
    return deduped


def paths_from_stream_record(record: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Вернуть ``(tool_name, paths)`` для NDJSON/live record tool-call."""
    kind = record.get("kind")
    if kind == "tool_call":
        name = str(record.get("name") or "")
        args = record.get("args") or {}
        return name, paths_from_tool_args(name, args)
    if kind == "interaction":
        update = str(record.get("update_type") or "")
        if update not in {
            "tool-call-started",
            "tool-call-completed",
            "partial-tool-call",
        }:
            return "", []
        tool_call = record.get("tool_call") or {}
        if isinstance(tool_call, Mapping):
            name = str(tool_call.get("name") or tool_call.get("toolName") or "")
            args = tool_call.get("args") or tool_call.get("arguments") or {}
            return name, paths_from_tool_args(name, args)
    return "", []


def _tool_class(tool_name: str) -> str:
    name = str(tool_name or "").strip().lower()
    if name in WRITE_CLASS_TOOLS:
        return "write"
    if name == "shell":
        return "shell"
    if name in READ_CLASS_TOOLS:
        return "read"
    return "other"


def _append_scope_violation(
    result: ReadScopeCheckResult,
    *,
    tool_name: str,
    path: str,
    rule: str,
    command: str = "",
) -> None:
    result.violations.append(
        ReadScopeViolation(
            tool=tool_name,
            path=path,
            rule=rule,
            command=command[:500],
        )
    )


def _check_one_tool_path(
    scope: ReadScope,
    *,
    tool_name: str,
    path: str,
    repo_root: Path | None,
    command: str,
    result: ReadScopeCheckResult,
    enforce_restrict: bool,
    restrict_allow: tuple[str, ...] | None = None,
) -> None:
    if _is_glob_or_search_pattern(path):
        return
    if is_repo_root_tool_path(path, repo_root):
        if tool_name == "shell" and _shell_path_is_cd_repo_root(
            command, path, repo_root
        ):
            return
        _append_scope_violation(
            result,
            tool_name=tool_name,
            path=path,
            rule="repo_root_scan",
            command=command,
        )
        return
    if repo_root is not None and not path_under_repo_root(path, repo_root):
        _append_scope_violation(
            result,
            tool_name=tool_name,
            path=path,
            rule="outside_repo",
            command=command,
        )
        return
    rel = _effective_repo_rel(path, repo_root)
    if not enforce_restrict:
        hit = _path_denied_by_patterns(rel, _SHELL_FATAL_DENY)
        if hit:
            _append_scope_violation(
                result,
                tool_name=tool_name,
                path=path,
                rule=f"shell_fatal:{hit}",
                command=command,
            )
        return
    if not path_allowed_by_scope(
        scope,
        path,
        repo_root=repo_root,
        restrict_allow=restrict_allow,
    ):
        deny_hit = _path_denied_by_patterns(rel, scope.deny)
        rule = f"deny:{deny_hit}" if deny_hit else "outside_restrict_allow"
        _append_scope_violation(
            result,
            tool_name=tool_name,
            path=path,
            rule=rule,
            command=command,
        )


def check_paths_in_scope(
    scope: ReadScope | None,
    *,
    tool_name: str,
    paths: Sequence[str],
    repo_root: Path | None = None,
    command: str = "",
) -> ReadScopeCheckResult:
    """Проверить пути; при ``restrict_allow`` — единые правила для read/shell/write."""
    result = ReadScopeCheckResult()
    if scope is None or read_scope_enforcement_mode() == "off":
        return result
    tclass = _tool_class(tool_name)
    if tclass == "other":
        return result
    effective_restrict = effective_restrict_allow_for_tool(scope, tool_name)
    enforce_restrict = bool(effective_restrict) and tclass in {
        "read",
        "shell",
        "write",
    }
    for path in paths:
        _check_one_tool_path(
            scope,
            tool_name=tool_name,
            path=path,
            repo_root=repo_root,
            command=command,
            result=result,
            enforce_restrict=enforce_restrict,
            restrict_allow=effective_restrict,
        )
    return result


def assert_paths_in_scope(
    scope: ReadScope | None,
    *,
    tool_name: str,
    paths: Sequence[str],
    repo_root: Path | None = None,
) -> ReadScopeCheckResult:
    """Поднять ``ReadScopeViolation`` в режиме enforce; иначе notes."""
    cmd = ""
    if tool_name == "shell" and len(paths) == 1:
        pass
    checked = check_paths_in_scope(
        scope,
        tool_name=tool_name,
        paths=paths,
        repo_root=repo_root,
        command=cmd,
    )
    mode = read_scope_enforcement_mode()
    if checked.violations and mode == "enforce":
        raise checked.violations[0]
    return checked


def _implicit_scan_roots(
    tool_name: str,
    args: Mapping[str, Any],
    paths: Sequence[str],
    *,
    repo_root: Path | None,
) -> list[str]:
    """Glob/grep/ls без bounded target → неявный обход REPO_ROOT (repo_root_scan).

    model adapter подставляет workspace root, когда ``target_directory`` / ``path``
    опущены; ловим это до выполнения tool, пока paths_from_tool_args пуст.
    """
    name = str(tool_name or "").strip().lower()
    if paths or repo_root is None:
        return list(paths)
    if name not in {"glob", "grep", "ls"}:
        return list(paths)
    root_s = str(repo_root.resolve())
    if name == "glob":
        pattern = args.get("glob_pattern") or args.get("globPattern") or ""
        if isinstance(pattern, str) and pattern.strip():
            return [root_s]
    if name == "grep":
        pattern = args.get("pattern") or args.get("query") or ""
        if isinstance(pattern, str) and pattern.strip():
            return [root_s]
    if name == "ls":
        return [root_s]
    return list(paths)


def assert_paths_in_scope_from_record(
    scope: ReadScope | None,
    *,
    tool_name: str,
    args: Mapping[str, Any],
    repo_root: Path | None = None,
) -> ReadScopeCheckResult:
    """Проверка с извлечением command для shell."""
    norm_args = _normalize_tool_args(args) if isinstance(args, Mapping) else args
    paths = _implicit_scan_roots(
        tool_name,
        norm_args,
        paths_from_tool_args(tool_name, args),
        repo_root=repo_root,
    )
    command = ""
    if tool_name == "shell":
        command = str(args.get("command") or args.get("cmd") or "")
    checked = check_paths_in_scope(
        scope,
        tool_name=tool_name,
        paths=paths,
        repo_root=repo_root,
        command=command,
    )
    if checked.violations and read_scope_enforcement_mode() == "enforce":
        raise checked.violations[0]
    return checked
