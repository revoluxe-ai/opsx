"""Disposable Git checkout pinned to an explicit full commit SHA.

This is a test workspace helper, NOT OS isolation for arbitrary SDK writes.
Only a TemporaryDirectory owned by this helper is ever removed.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from workflows.implementation.node_subprocess import run_subprocess_utf8, subprocess_utf8_env


_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


class CheckoutError(RuntimeError):
    """Checkout cannot be proven pinned or safely temporary."""


def _git(*args: str, timeout: int = 120) -> str:
    # Do not let parent-shell GIT_DIR/GIT_WORK_TREE redirect git to another repo.
    raw_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env = subprocess_utf8_env(base=raw_env)
    try:
        result = run_subprocess_utf8(["git", *args], timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckoutError(f"Git operation failed: {args[0]}") from exc
    if result.returncode != 0:
        raise CheckoutError(f"Git operation failed: {args[0]}")
    return result.stdout.strip()


class PinnedCheckout:
    """Owns only a generated TemporaryDirectory; repeated close is safe."""

    def __init__(self, holder: tempfile.TemporaryDirectory[str], sha: str):
        self._holder: tempfile.TemporaryDirectory[str] | None = holder
        self.path = Path(holder.name) / "repo"
        self.sha = sha

    def close(self) -> None:
        holder = self._holder
        if holder is not None:
            holder.cleanup()  # propagate cleanup errors (no silent success)
            self._holder = None

    def __enter__(self) -> PinnedCheckout:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def create_pinned_checkout(
    source_repo: str | Path, commit_sha: str, *, temp_parent: str | Path | None = None,
) -> PinnedCheckout:
    """Clone local Git data, detach at SHA, verify HEAD; never edit source.

    The only directory eligible for deletion is the one created here with
    tempfile.TemporaryDirectory. Caller paths are NEVER cleanup targets.
    """
    if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
        raise CheckoutError("PINNED_COMMIT_REQUIRED: full 40-character commit SHA")
    source = Path(source_repo).resolve(strict=True)
    if not source.is_dir() or _git("-C", str(source), "rev-parse", "--show-toplevel") != str(source):
        raise CheckoutError("SOURCE_NOT_GIT_ROOT")
    if _git("-C", str(source), "cat-file", "-t", commit_sha) != "commit":
        raise CheckoutError("PINNED_OBJECT_NOT_COMMIT")
    if temp_parent is not None:
        parent = Path(temp_parent).resolve(strict=True)
        if not parent.is_dir() or parent == source or parent.is_relative_to(source):
            raise CheckoutError("UNSAFE_TEMP_PARENT: must be outside source repository")
    else:
        parent = None
    holder = tempfile.TemporaryDirectory(prefix="opsx-pinned-live-", dir=parent)
    checkout = PinnedCheckout(holder, commit_sha.lower())
    try:
        _git("clone", "--quiet", "--no-hardlinks", "--no-checkout", "--", str(source), str(checkout.path))
        _git("-C", str(checkout.path), "checkout", "--quiet", "--detach", "--force", commit_sha)
        actual = _git("-C", str(checkout.path), "rev-parse", "HEAD")
        if actual.lower() != commit_sha.lower():
            raise CheckoutError("CHECKOUT_SHA_MISMATCH")
        if checkout.path.samefile(source):
            raise CheckoutError("CHECKOUT_NOT_INDEPENDENT")
    except BaseException:
        checkout.close()
        raise
    return checkout
