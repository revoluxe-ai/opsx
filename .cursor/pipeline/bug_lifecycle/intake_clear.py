"""Read-only intake normalization (STROY subset of /opsx-bug-intake-clear)."""

from __future__ import annotations

from typing import Any

PROCESS_PREFIXES = (
    ".cursor/",
    "opsx/",
    ".agents/",
    "tools/temporal/",
)


def _is_process(location: str) -> bool:
    text = location.replace("\\", "/").lstrip("`")
    return any(text.startswith(prefix) for prefix in PROCESS_PREFIXES)


def normalize_findings(rows: list[dict[str, str]]) -> dict[str, Any]:
    product: list[dict[str, str]] = []
    process: list[dict[str, str]] = []
    for row in rows:
        location = row.get("location", "")
        finding = row.get("finding", "") or row.get("id", "")
        item = {"location": location, "finding": finding}
        if _is_process(location):
            process.append(item)
        else:
            product.append(item)
    if not product and not process:
        return {
            "STATUS": "BLOCKED_NEEDS_INPUT",
            "CLEAR_COMPLETE": "NO",
            "CLEAN_FINDINGS": [],
            "PROCESS_FINDINGS": [],
        }
    return {
        "STATUS": "CLEAR_COMPLETE",
        "CLEAR_COMPLETE": "YES",
        "CLEAN_FINDINGS": product,
        "PROCESS_FINDINGS": process,
        "PROCESS_FINDINGS_ROUTED_COUNT": len(process),
        "PRODUCT_COUNT": len(product),
    }
