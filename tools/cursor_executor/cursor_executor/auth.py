"""In-memory auth resolution for Cursor SDK (no persistence)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from .schemas import AuthSource


@dataclass(frozen=True)
class AuthResolution:
    api_key: str | None
    source: AuthSource
    process_env_key_present: bool
    user_env_key_present: bool


def _read_windows_user_env(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            val, _ = winreg.QueryValueEx(key, name)
            if isinstance(val, str) and val.strip():
                return val
    except OSError:
        return None
    return None


def _cursor_agent_terminal() -> bool:
    """True when Cursor IDE injected agent-terminal env (``CURSOR_AGENT``)."""

    flag = os.environ.get("CURSOR_AGENT", "").strip().lower()
    return flag not in ("", "0", "false", "no", "off")


def resolve_auth() -> AuthResolution:
    """LOCAL Windows V0.1: process env → HKCU user env → missing.

    In Cursor agent terminals IDE injects ``CURSOR_API_KEY`` that is not valid
    for cloud Agent RPC; when ``CURSOR_AGENT`` is set and HKCU has a key,
    HKCU wins over process env.
    """
    process_val = os.environ.get("CURSOR_API_KEY")
    process_present = bool(process_val and str(process_val).strip())
    user_val = _read_windows_user_env("CURSOR_API_KEY")
    user_present = bool(user_val and str(user_val).strip())

    if _cursor_agent_terminal() and user_present:
        return AuthResolution(
            api_key=str(user_val).strip(),
            source=AuthSource.WINDOWS_USER_ENV,
            process_env_key_present=process_present,
            user_env_key_present=True,
        )
    if process_present:
        return AuthResolution(
            api_key=str(process_val).strip(),
            source=AuthSource.PROCESS_ENV,
            process_env_key_present=True,
            user_env_key_present=user_present,
        )
    if user_present:
        return AuthResolution(
            api_key=str(user_val).strip(),
            source=AuthSource.WINDOWS_USER_ENV,
            process_env_key_present=False,
            user_env_key_present=True,
        )
    return AuthResolution(
        api_key=None,
        source=AuthSource.NONE,
        process_env_key_present=False,
        user_env_key_present=False,
    )
