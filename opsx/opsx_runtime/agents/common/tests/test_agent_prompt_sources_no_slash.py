"""Agent prompt sources не содержат slash-команд Owner."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_AGENTS_ROOT = Path(__file__).resolve().parents[2]
_SLASH_OPSX = re.compile(r"/opsx-")
_COMMANDS_PATH = re.compile(r"\.cursor/commands", re.IGNORECASE)

# Исключения: тесты, санitizer regex, assert'ы «не должно быть».
_EXCLUDE_PARTS = (
    "/tests/",
    "\\tests\\",
    "test_agent_visible_text.py",
    "test_agent_prompt_sources_no_slash.py",
    "agent_visible_text.py",
)

_GLOB_PATTERNS = (
    "**/prompt/get_prompt.py",
    "**/prompt/*.md",
    "**/prompt/build_*.py",
    "**/prompt/stage_authority.py",
    "**/prompt/*_fragments.py",
    "**/prompt/*_template*.md",
)


def _iter_prompt_source_files() -> list[Path]:
    files: list[Path] = []
    for pattern in _GLOB_PATTERNS:
        files.extend(_AGENTS_ROOT.glob(pattern))
    out: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(files):
        resolved = path.resolve()
        if resolved in seen:
            continue
        text = str(resolved).replace("\\", "/")
        if any(part.replace("\\", "/") in text for part in _EXCLUDE_PARTS):
            continue
        if not path.is_file():
            continue
        seen.add(resolved)
        out.append(path)
    return out


class AgentPromptSourcesNoSlashTests(unittest.TestCase):
    def test_no_slash_opsx_in_prompt_sources(self) -> None:
        violations: list[str] = []
        for path in _iter_prompt_source_files():
            content = path.read_text(encoding="utf-8", errors="replace")
            if _SLASH_OPSX.search(content) or _COMMANDS_PATH.search(content):
                rel = path.relative_to(_AGENTS_ROOT)
                violations.append(str(rel))
        self.assertEqual(
            violations,
            [],
            "prompt sources must not reference /opsx-* or .cursor/commands: "
            + ", ".join(violations),
        )

    def test_verify_fragment_loaders_clean(self) -> None:
        from agents.verify.prompt.verify_prompt_fragments import (
            verify_archive_hop_authority,
            verify_impl_hop_authority,
            verify_scenario_coverage_audit_authority,
        )

        for text in (
            verify_impl_hop_authority(),
            verify_archive_hop_authority(),
            verify_scenario_coverage_audit_authority(),
        ):
            self.assertNotIn("/opsx-", text)
            self.assertNotIn(".cursor/commands", text.lower())


if __name__ == "__main__":
    unittest.main()