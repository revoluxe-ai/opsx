"""Portable command authority: no donor project may be default execution authority."""
from __future__ import annotations

import hashlib
import re

from .context import OpsxError

DONOR_OVERRIDE_HEADING = re.compile(
    r"^#\s*STROY-SYSTEM\s+TARGET\s+OVERRIDE[^\n]*\n",
    re.MULTILINE | re.IGNORECASE,
)
DONOR_PROJECTIZATION_HEADING = re.compile(
    r"^#\s*STROY-SYSTEM\s+PROJECTIZATION\s+GATE[^\n]*\n",
    re.MULTILINE | re.IGNORECASE,
)
FORBIDDEN_AUTHORITY_MARKERS = (
    re.compile(r"^#\s*STROY-SYSTEM\s+TARGET\s+OVERRIDE", re.MULTILINE | re.IGNORECASE),
    re.compile(r"\bFor\s+stroy-system\b", re.IGNORECASE),
    re.compile(r"\bTARGET\s+OVERRIDE\s*-\s*HIGHEST\s+PRIORITY\b", re.IGNORECASE),
)

PORTABLE_TARGET_PROFILE = """# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.

"""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


PORTABLE_PROJECTIZATION_GATE = """# PROJECT PROJECTIZATION GATE

Before any command-specific action, resolve adoption through:

  opsx/project/capabilities.yaml

If adoption is incomplete (`NEEDS_ADOPTION` or projectization incomplete), stop without
command-specific writes and emit OPSX_PROJECTIZATION_REQUIRED with NEXT_ACTION from the
project profile (`/opsx-adopt` or documented projectize flow).

"""


def _replace_donor_banner(text: str, heading: re.Pattern[str], *, replacement: str | None = None) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        if heading.match(lines[index]):
            index += 1
            while index < len(lines):
                if lines[index].startswith("# /") or lines[index].startswith("# "):
                    if lines[index].startswith("# /"):
                        break
                if "separate from product Temporal/runtime adoption" in lines[index]:
                    index += 1
                    break
                index += 1
            continue
        if replacement and heading is DONOR_PROJECTIZATION_HEADING and out and not out[-1].startswith("# PROJECT PROJECTIZATION"):
            out.append(replacement)
        out.append(lines[index])
        index += 1
    if replacement and heading is DONOR_PROJECTIZATION_HEADING:
        # banner was at EOF without command heading
        pass
    return "".join(out)


def neutralize_command_authority(text: str) -> str:
    """Remove donor override banners and insert portable profile before first command heading."""
    text = _replace_donor_banner(text, DONOR_OVERRIDE_HEADING)
    if DONOR_PROJECTIZATION_HEADING.search(text):
        text = re.sub(
            r"^#\s*STROY-SYSTEM\s+PROJECTIZATION\s+GATE.*?^# /",
            PORTABLE_PROJECTIZATION_GATE + "# /",
            text,
            count=1,
            flags=re.MULTILINE | re.DOTALL,
        )
    text = _replace_donor_banner(text, DONOR_PROJECTIZATION_HEADING)
    for pattern in FORBIDDEN_AUTHORITY_MARKERS:
        if pattern.search(text):
            text = pattern.sub("", text)
    if PORTABLE_TARGET_PROFILE.strip() not in text:
        match = re.search(r"^# /", text, re.MULTILINE)
        if match:
            text = text[: match.start()] + PORTABLE_TARGET_PROFILE + "\n" + text[match.start() :]
        elif text.strip():
            text = PORTABLE_TARGET_PROFILE + "\n" + text
    return text


def assert_portable_managed_content(relative: str, text: str) -> None:
    """Fail closed if managed integration still contains donor execution authority."""
    for pattern in FORBIDDEN_AUTHORITY_MARKERS:
        if pattern.search(text):
            raise OpsxError(
                "DONOR_AUTHORITY_FORBIDDEN",
                f"Managed file {relative} still contains donor execution authority.",
            )


def semantic_fingerprint(text: str) -> str:
    """Stable hash of command semantics after authority neutralization."""
    body = neutralize_command_authority(_strip_frontmatter(text))
    body = re.sub(r"\r\n", "\n", body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()