"""Bridge launch configuration for local SDK runs (Windows disposable workspaces).

``cursor-sdk-bridge`` ``--workspace`` must совпадать с папкой, открытой в Cursor.
При запуске OPSX из WSL Linux-путь ``/home/...`` ломает клики по файлам в Composer;
нужен ``\\\\wsl.localhost\\<distro>\\...`` (через ``wslpath -w`` или эвристику).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import logging
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

_LOG = logging.getLogger(__name__)

# Git-Bash / MSYS markers inherited from Owner shells break Cursor integrated
# Shell on some hosts (NtCreateDirectoryObject / 0xC0000022). cursor-sdk 1.0.31
# exposes no local.shell option; bridge subprocess env is the supported knob
# (_async_bridge.py: model shell tools inherit bridge env).
_WIN_BASH_ENV_KEYS = (
    "MSYSTEM",
    "MSYSTEM_PREFIX",
    "MSYSTEM_C",
    "MSYSTEM_CHROOT",
    "PLINK_PROTOCOL",
)


def executor_package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def game_repo_root() -> Path:
    return executor_package_root().parent.parent


def selected_sdk_store_backend() -> str:
    """Explicit opt-in only; never silently change the recovery backend."""
    backend = os.environ.get("OPSX_CURSOR_SDK_STORE", "jsonl").strip().lower()
    if backend not in {"jsonl", "sqlite"}:
        raise ValueError("CURSOR_SDK_STORE_BACKEND_INVALID")
    return backend


def _require_sqlite_scope(scope: str | None) -> str:
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 512:
        raise ValueError("CURSOR_SQLITE_STORE_SCOPE_REQUIRED")
    return scope


def sqlite_store_scope_digest(store_scope: str) -> str:
    """Стабильный каталог для ``store_scope`` (SHA-256, полный hex)."""
    return hashlib.sha256(store_scope.encode("utf-8")).hexdigest()


def cursor_sdk_sqlite_store_root(
    agent_cwd: str | os.PathLike[str], store_scope: str,
) -> Path:
    """``<repo>/opsx/runs/cursor-sdk/<scope_digest>/`` — фактический SQLite stateRoot."""
    scope = _require_sqlite_scope(store_scope)
    repo = _resolve_opsx_repo_root(Path(agent_cwd))
    root = resource_path(repo, "opsx", "runs", "cursor-sdk", sqlite_store_scope_digest(scope))
    root.mkdir(parents=True, exist_ok=True)
    return root


adapter_sqlite_store_root = cursor_sdk_sqlite_store_root


def bridge_state_root(
    *,
    store_backend: str = "jsonl",
    store_scope: str | None = None,
    agent_cwd: str | os.PathLike[str] | None = None,
) -> Path:
    """Durable root. SQLite roots are stable across restarts of one recovery scope."""
    if store_backend == "sqlite":
        if agent_cwd is None:
            raise ValueError("CURSOR_SQLITE_STORE_AGENT_CWD_REQUIRED")
        return cursor_sdk_sqlite_store_root(agent_cwd, _require_sqlite_scope(store_scope))
    root = executor_package_root() / ".bridge-state"
    if store_backend != "jsonl":
        raise ValueError("CURSOR_SDK_STORE_BACKEND_INVALID")
    root.mkdir(parents=True, exist_ok=True)
    return root


def jsonl_store_root() -> Path:
    root = bridge_state_root() / "jsonl-store"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jsonl_local_store_config() -> dict[str, str]:
    return {"type": "jsonl", "rootDir": os.fspath(jsonl_store_root())}


def local_agent_store_config(
    *,
    store_backend: str = "jsonl",
    store_scope: str | None = None,
    agent_cwd: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    if store_backend == "sqlite":
        if agent_cwd is None:
            raise ValueError("CURSOR_SQLITE_STORE_AGENT_CWD_REQUIRED")
        scope = _require_sqlite_scope(store_scope)
        root = cursor_sdk_sqlite_store_root(agent_cwd, scope)
        # rootDir передаётся в opsx-bridge и открывает SqliteLocalAgentStore.open({ stateRoot }).
        return {"type": "sqlite", "rootDir": os.fspath(root)}
    if store_backend == "jsonl":
        return jsonl_local_store_config()
    raise ValueError("CURSOR_SDK_STORE_BACKEND_INVALID")


def _running_in_wsl() -> bool:
    """True, если процесс внутри WSL (не Git Bash на Windows)."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _resolve_opsx_repo_root(agent_cwd: Path) -> Path:
    """Корень репозитория с ``opsx``; disposable hop внутри game repo → game root."""
    resolved = agent_cwd.resolve()
    if (resolved / "opsx").is_dir():
        return resolved
    for parent in resolved.parents:
        if (parent / "opsx").is_dir():
            return parent
    game = game_repo_root().resolve()
    if resolved == game or game in resolved.parents:
        return game
    return resolved


def linux_path_to_cursor_workspace(linux_path: str) -> str | None:
    """Преобразовать POSIX-путь WSL в Windows UNC для Cursor (``\\\\wsl.localhost\\...``).

    Args:
        linux_path: Абсолютный путь вида ``/home/...`` внутри WSL.

    Returns:
        UNC для ``--workspace`` или ``None``, если преобразование не применимо.
    """
    normalized = linux_path.replace("\\", "/")
    if not normalized.startswith("/"):
        return None
    wslpath = shutil.which("wslpath")
    if wslpath:
        proc = subprocess.run(
            [wslpath, "-w", normalized],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            win_path = proc.stdout.strip()
            if win_path:
                return win_path
    if not normalized.startswith("/home/"):
        return None
    distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu-24.04"
    tail = normalized.lstrip("/").replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}\\{tail}"


def cursor_bridge_workspace(agent_cwd: str | os.PathLike[str]) -> str:
    """Workspace для ``cursor-sdk-bridge`` — совпадает с открытой в IDE папкой Owner."""
    repo = _resolve_opsx_repo_root(Path(agent_cwd))
    repo_s = os.fspath(repo.resolve())
    if repo_s.startswith("/") and (_running_in_wsl() or os.name == "nt"):
        unc = linux_path_to_cursor_workspace(repo_s)
        if unc:
            return unc
    return repo_s


def bridge_workspace_for_agent_cwd(agent_cwd: str | os.PathLike[str]) -> str:
    """Bridge subprocess workspace: repo root (disposable hop → game root), WSL-safe UNC."""
    return cursor_bridge_workspace(agent_cwd)


def _default_windows_comspec() -> str | None:
    for candidate in (
        os.environ.get("ComSpec"),
        os.environ.get("COMSPEC"),
        r"C:\Windows\System32\cmd.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def windows_agent_subprocess_env(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build env for cursor-sdk bridge / executor worker on Windows.

    Prefer cmd.exe via COMSPEC and strip MSYSTEM/SHELL bash markers so SDK agent
    Shell can run git/pytest. No invented cursor-sdk local keys — env only.
    """
    env = dict(base if base is not None else os.environ)
    if os.name != "nt":
        return env
    comspec = _default_windows_comspec()
    if comspec:
        env["ComSpec"] = comspec
        env["COMSPEC"] = comspec
    for key in _WIN_BASH_ENV_KEYS:
        _ = env.pop(key, None)
    shell = env.get("SHELL", "")
    if shell:
        lowered = shell.lower()
        if "bash" in lowered or "msys" in lowered or "/git/" in lowered.replace("\\", "/"):
            _ = env.pop("SHELL", None)
    return env


def _cursor_sdk_vendor_bridge_root() -> Path:
    """Корень vendored ``cursor-sdk-bridge`` (node.exe + dist)."""
    import cursor_sdk._vendor as cursor_vendor

    return Path(cursor_vendor.__file__).resolve().parent / "bridge"


def opsx_bridge_launcher_path() -> Path:
    """Launcher, который связывает sqlite ``rootDir`` с ``SqliteLocalAgentStore``."""
    bridge_bin = executor_package_root() / "bridge_bin"
    if sys.platform == "win32":
        launcher = bridge_bin / "cursor-sdk-bridge-opsx.cmd"
    else:
        launcher = bridge_bin / "cursor-sdk-bridge-opsx.mjs"
    if not launcher.is_file():
        raise FileNotFoundError("CURSOR_SDK_BRIDGE_OPSX_LAUNCHER_MISSING")
    return launcher


def ensure_opsx_sqlite_bridge_env(
    *,
    store_backend: str,
    agent_cwd: str | os.PathLike[str] | None = None,
) -> None:
    """CURSOR_SDK_BRIDGE_BIN + vendor paths для opsx sqlite bridge (идемпотентно)."""
    if store_backend != "sqlite":
        return
    vendor_bridge = _cursor_sdk_vendor_bridge_root()
    if not vendor_bridge.is_dir():
        raise FileNotFoundError("CURSOR_SDK_VENDOR_BRIDGE_MISSING")
    os.environ["CURSOR_SDK_BRIDGE_BIN"] = os.fspath(opsx_bridge_launcher_path())
    os.environ["OPSX_CURSOR_SDK_BRIDGE_VENDOR_ROOT"] = os.fspath(vendor_bridge)
    os.environ["OPSX_CURSOR_SDK_BRIDGE_VENDOR_DIST"] = os.fspath(vendor_bridge / "dist")
    node_modules = vendor_bridge / "node_modules"
    if node_modules.is_dir():
        prev = os.environ.get("NODE_PATH", "")
        node_path = os.fspath(node_modules)
        if prev:
            if node_path not in prev.split(os.pathsep):
                os.environ["NODE_PATH"] = os.pathsep.join([node_path, prev])
        else:
            os.environ["NODE_PATH"] = node_path
    if agent_cwd is not None:
        repo = _resolve_opsx_repo_root(Path(agent_cwd))
        bridge_tmp = resource_path(repo, "opsx", "runs", "cursor-sdk-bridge-tmp")
        bridge_tmp.mkdir(parents=True, exist_ok=True)
        bridge_tmp_s = os.fspath(bridge_tmp)
        for key in ("TEMP", "TMP", "TMPDIR"):
            os.environ[key] = bridge_tmp_s


def log_cursor_sdk_store_layout(
    *,
    store_backend: str,
    store_scope: str | None,
    state_root: str | Path,
) -> None:
    """Без секретов: backend, scope, каталог SQLite."""
    _LOG.info(
        "cursor_sdk_store backend=%s scope=%s sqlite_root=%s",
        store_backend,
        store_scope or "",
        os.fspath(state_root),
    )


def apply_windows_agent_shell_env() -> None:
    """Apply Windows shell env to the current process (worker before SDK bridge)."""
    if os.name != "nt":
        return
    patched = windows_agent_subprocess_env(os.environ)
    comspec = patched.get("COMSPEC") or patched.get("ComSpec")
    if comspec:
        os.environ["ComSpec"] = comspec
        os.environ["COMSPEC"] = comspec
    for key in _WIN_BASH_ENV_KEYS:
        _ = os.environ.pop(key, None)
    shell = os.environ.get("SHELL", "")
    if shell:
        lowered = shell.lower()
        if "bash" in lowered or "msys" in lowered or "/git/" in lowered.replace("\\", "/"):
            _ = os.environ.pop("SHELL", None)


def bridge_launch_kwargs(
    *, agent_cwd: str, store_backend: str | None = None,
    store_scope: str | None = None,
) -> dict[str, Any]:
    """One explicit store identity for bridge and all SDK agents in its scope."""
    backend = store_backend if store_backend is not None else selected_sdk_store_backend()
    store = local_agent_store_config(
        store_backend=backend,
        store_scope=store_scope,
        agent_cwd=agent_cwd,
    )
    root = bridge_state_root(
        store_backend=backend,
        store_scope=store_scope,
        agent_cwd=agent_cwd,
    )
    apply_windows_agent_shell_env()
    ensure_opsx_sqlite_bridge_env(store_backend=backend, agent_cwd=agent_cwd)
    log_cursor_sdk_store_layout(
        store_backend=backend,
        store_scope=store_scope,
        state_root=root,
    )
    return {
        "workspace": bridge_workspace_for_agent_cwd(agent_cwd),
        "state_root": os.fspath(root),
        "local": {"store": store},
    }


def agent_local_options(
    *, agent_cwd: str, store_backend: str | None = None,
    store_scope: str | None = None,
) -> dict[str, Any]:
    repo = _resolve_opsx_repo_root(Path(agent_cwd))
    backend = store_backend if store_backend is not None else selected_sdk_store_backend()
    return {
        "cwd": os.fspath(repo.resolve()),
        "store": local_agent_store_config(
            store_backend=backend,
            store_scope=store_scope,
            agent_cwd=agent_cwd,
        ),
    }


def cursor_sdk_builtin_tools_only_extras() -> dict[str, Any]:
    """Отключить MCP-family (IDE MCP forward / CallMcpTool), оставить built-in tools.

    Cursor SDK: ``disallowed_tools: ["mcp"]`` убирает всю MCP-группу, включая
    customTools и серверы из ``mcp_servers``.
    """
    return {
        "disallowed_tools": ["mcp"],
        "mcp_servers": {},
    }
