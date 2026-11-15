"""Tool allowlists and name mapping for mutating repair adapter hops."""

from __future__ import annotations

from collections.abc import Sequence

READ_ONLY_TOOLS = frozenset({"read", "grep", "glob", "ls"})
MUTATION_TOOLS = frozenset({"edit"})
REPAIR_CUSTOM_TOOLS = frozenset({"run_pytest"})
ALLOWED_TOOLS = READ_ONLY_TOOLS | MUTATION_TOOLS | REPAIR_CUSTOM_TOOLS
_ADAPTER_TOOL_ALIASES: dict[str, str] = {"piWrite": "edit", "piEdit": "edit"}


def tools_for_adapter_create(requested: Sequence[str]) -> list[str]:
    """Normalize tool names for agent create (legacy ``pi*`` → ``edit``)."""
    out: list[str] = []
    seen: set[str] = set()
    for name in requested:
        mapped = _ADAPTER_TOOL_ALIASES.get(name, name)
        if mapped in seen:
            continue
        seen.add(mapped)
        out.append(mapped)
    return out
