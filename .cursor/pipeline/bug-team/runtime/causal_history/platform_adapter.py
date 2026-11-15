# -*- coding: utf-8 -*-
"""Адаптер атомарной публикации без замены (production causal history)."""

from __future__ import annotations

import errno
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from .errors import HistoryErrorCode, HistoryPause

_RENAME_NOREPLACE = 1
_AT_FDCWD = -100


class PlatformClass(str, Enum):
    POSIX_A_LINUX = "POSIX_A_LINUX"
    POSIX_B_MACOS = "POSIX_B_MACOS"
    WINDOWS_NTFS = "WINDOWS_NTFS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PlatformProbe:
    platform_class: PlatformClass
    system: str
    release: str
    machine: str
    rename_noreplace_available: bool
    dir_fsync_available: bool
    notes: str


def probe_platform() -> PlatformProbe:
    system = platform.system()
    rename_ok = False
    dir_fsync_ok = False
    notes = ""
    pclass = PlatformClass.UNSUPPORTED
    if system == "Linux":
        pclass = PlatformClass.POSIX_A_LINUX
        rename_ok = _linux_rename_noreplace_available()
        dir_fsync_ok = hasattr(os, "O_DIRECTORY")
        notes = "Linux local FS"
    elif system == "Darwin":
        pclass = PlatformClass.POSIX_B_MACOS
        dir_fsync_ok = hasattr(os, "O_DIRECTORY")
        notes = "macOS adapter partial"
    elif system == "Windows":
        pclass = PlatformClass.WINDOWS_NTFS
        notes = "Windows adapter not implemented in v0 integration"
    if not rename_ok or not dir_fsync_ok:
        pclass = PlatformClass.UNSUPPORTED
    return PlatformProbe(
        platform_class=pclass,
        system=system,
        release=platform.release(),
        machine=platform.machine(),
        rename_noreplace_available=rename_ok,
        dir_fsync_available=dir_fsync_ok,
        notes=notes,
    )


def _linux_rename_noreplace_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        libc.renameat2.restype = ctypes.c_int
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a, b = root / "a", root / "b"
            a.write_text("1", encoding="utf-8")
            b.write_text("2", encoding="utf-8")
            ret = libc.renameat2(_AT_FDCWD, str(b).encode(), _AT_FDCWD, str(a).encode(), _RENAME_NOREPLACE)
            if ret == 0:
                return False
            err = ctypes.get_errno()
            return err in (errno.EEXIST, errno.ENOTSUP)
    except OSError:
        return False


def fsync_file(path: Path) -> None:
    with path.open("rb") as fh:
        os.fsync(fh.fileno())


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_no_replace(src: Path, dst: Path) -> Literal["published", "exists"]:
    probe = probe_platform()
    if probe.platform_class == PlatformClass.UNSUPPORTED:
        raise HistoryPause(
            HistoryErrorCode.UNSUPPORTED_PLATFORM,
            probe.notes,
            "Unsupported FS for causal append",
        )
    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        libc.renameat2.restype = ctypes.c_int
        ret = libc.renameat2(
            _AT_FDCWD,
            str(src).encode(),
            _AT_FDCWD,
            str(dst).encode(),
            _RENAME_NOREPLACE,
        )
        if ret == 0:
            return "published"
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            return "exists"
        raise OSError(err, os.strerror(err), str(dst))
    raise HistoryPause(
        HistoryErrorCode.UNSUPPORTED_PLATFORM,
        f"publish_no_replace not implemented for {probe.system}",
        "Extend platform adapter",
    )
