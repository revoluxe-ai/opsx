"""META write/read surface для ProcessFixAgent (§27.0.4–27.0.5)."""

from __future__ import annotations

from typing import Final

PROCESS_SUPERVISOR_FIX_KEY: Final[str] = "PROCESS_SUPERVISOR_FIX"

META_READ_GLOBS: Final[tuple[str, ...]] = (
    "opsx/**",
    "opsx_cli/**",
    "opsx/opsx.mdc",
    # Только чтение: артефакты change нужны как улика, что именно агент не смог сдать.
    "openspec/changes/**",
)

META_WRITE_GLOBS: Final[tuple[str, ...]] = (
    "opsx/opsx_runtime/**",
    "opsx/opsx_tools/**",
    "opsx/contract_prompts/**",
    "opsx/configs/**",
    "opsx_cli/**",
    ".cursor/rules/**",
    ".cursor/skills/**",
)

# От корня проекта: инструмент read/edit режет по ним до записи на диск.
# ``opsx/core/**`` нужен целиком: grep/glob с path=opsx/core иначе
# outside_restrict_allow, хотя дети ``opsx/core/opsx/**`` уже были в списке.
# Продуктовые деревья сюда не добавлять.
SUPERVISOR_READ_GLOBS: Final[tuple[str, ...]] = (
    "app/**",
    "src/**",
    "tests/**",
    "openspec/**",
    "docs/**",
    "docks/**",
    "migrations/**",
    "alembic.ini",
    "pyproject.toml",
    "poetry.lock",
    "AGENTS.md",
    "README.md",
    "opsx/core/**",
)

SUPERVISOR_WRITE_GLOBS: Final[tuple[str, ...]] = (
    "opsx/core/opsx/opsx_runtime/**",
    "opsx/core/opsx/opsx_tools/**",
    "opsx/core/opsx/contract_prompts/**",
    "opsx/core/opsx/configs/**",
    "opsx/core/opsx_cli/**",
    "opsx/core/.cursor/rules/**",
    "opsx/core/.cursor/skills/**",
)

# Относительно корня ядра (commit-gate). Супервизор не чинит сам себя.
SUPERVISOR_SELF_DENY_CORE_GLOBS: Final[tuple[str, ...]] = (
    "opsx_cli/supervisor*.py",
    "opsx/opsx_runtime/agents/process_repair/**",
    "opsx/contract_prompts/process_supervisor/**",
    "tests/test_supervisor.py",
    "opsx/opsx_runtime/workflows/verify/cli_outcome.py",
)

# Те же пути от корня проекта: read/edit режет инструмент до диска.
SUPERVISOR_SELF_DENY_REPO_GLOBS: Final[tuple[str, ...]] = tuple(
    f"opsx/core/{pattern}" for pattern in SUPERVISOR_SELF_DENY_CORE_GLOBS
)

META_WRITE_DENY_GLOBS: Final[tuple[str, ...]] = SUPERVISOR_SELF_DENY_CORE_GLOBS


def meta_supervisor_read_globs() -> tuple[str, ...]:
    return META_READ_GLOBS


def meta_supervisor_write_globs() -> tuple[str, ...]:
    return META_WRITE_GLOBS


def supervisor_read_globs() -> tuple[str, ...]:
    """Чтение супервизора: ядро ``opsx/core/**`` и точечная улика продукта."""
    return SUPERVISOR_READ_GLOBS


def supervisor_write_globs() -> tuple[str, ...]:
    """Запись супервизора: только ядро OPSX, не продукт и не артефакты change."""
    return SUPERVISOR_WRITE_GLOBS


def supervisor_self_deny_repo_globs() -> tuple[str, ...]:
    """Пути самого супервизора: ни читать, ни править."""
    return SUPERVISOR_SELF_DENY_REPO_GLOBS


def format_meta_surface_prompt() -> str:
    """Преамбула fix-hop: системный ремонт OPSX и списки read/write/deny."""
    read_lines = "\n".join(f"- `{p}`" for p in SUPERVISOR_READ_GLOBS)
    write_lines = "\n".join(f"- `{p}`" for p in SUPERVISOR_WRITE_GLOBS)
    deny_lines = "\n".join(f"- `{p}`" for p in SUPERVISOR_SELF_DENY_REPO_GLOBS)
    return (
        "### PROCESS SUPERVISOR META SURFACE\n\n"
        "Role: repair the OPSX platform, not the current change. The change id, "
        "log tail, and failed artifact are evidence of a defect class. The edit "
        "must make that class succeed on later runs and on other changes. "
        "A patch keyed to this change_id, this run, or this one path is a failed "
        "fix even if it would turn this run green.\n\n"
        "Fix only OPSX/META defects. Product (`app/**`, `src/**`, `tests/**`) and "
        "change artifacts (`openspec/**`) are read-only evidence. The edit tool "
        "rejects any path outside the OPSX write list; a rejected edit aborts the hop.\n\n"
        "Grep and glob `path` must be `opsx/core` or a subdirectory of a READ ALLOW "
        "entry. Paths `opsx`, `opsx/contract_prompts`, `opsx/opsx_runtime`, and the "
        "workspace root are outside allow: prompts and runtime live under "
        "`opsx/core/opsx/`. Do not search `app/`, `src/`, or product `tests/` "
        "unless the log tail already names that exact file; read that file only.\n\n"
        "You cannot read or edit the supervisor itself. WRITE DENY paths below "
        "are closed to read, grep, glob, and edit. If the only cause is in those "
        "files, return CANNOT_FIX. Do not try the edit: the tool aborts the hop.\n\n"
        f"READ ALLOW:\n{read_lines}\n\n"
        f"WRITE ALLOW:\n{write_lines}\n\n"
        f"WRITE DENY:\n{deny_lines}\n"
    )