#!/usr/bin/env python3
"""Live catalog audit for model routes reachable by canonical Temporal lanes."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
GAME_ROOT = HERE.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(CURSOR_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(CURSOR_EXECUTOR_ROOT))

from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.preflight import validate_model_route  # noqa: E402
from cursor_executor.sdk_client import AsyncCursorSdkClient  # noqa: E402
from model_config import ALLOWED_TIERS, AgentModelConfig
from sdk_model_route import resolve_sdk_model_route


STEPS = ("review", "repair", "next_check", "verification_executor")


def _config_for_tiers(review: str, repair: str) -> AgentModelConfig:
    return AgentModelConfig.load().with_steps(review=review, repair=repair)


def _reachable_combinations() -> list[tuple[str, str, str]]:
    """(label, step, tier_name) for every canonical tier pair × step."""
    out: list[tuple[str, str, str]] = []
    for review in ALLOWED_TIERS:
        for repair in ALLOWED_TIERS:
            cfg = _config_for_tiers(review, repair)
            label = f"review={review},repair={repair}"
            for step in STEPS:
                tier = cfg.steps.get(step, cfg.default_tier)
                out.append((label, step, tier))
    return out


async def _audit(*, workspace: str) -> dict:
    auth = resolve_auth()
    if not auth.api_key:
        return {"status": "BLOCKED", "reason": "CURSOR_API_KEY missing"}

    sdk = AsyncCursorSdkClient()
    try:
        catalog = await sdk.list_models(api_key=auth.api_key, workspace=workspace)
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": f"catalog fetch failed: {exc}"}

    entries: list[dict] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

    for review in ALLOWED_TIERS:
        for repair in ALLOWED_TIERS:
            cfg = _config_for_tiers(review, repair)
            label = f"review={review},repair={repair}"
            is_default_overlay = review == "low" and repair == "low"
            for step in STEPS:
                route = resolve_sdk_model_route(cfg, step=step)
                key = (route.model_id, tuple(sorted(route.normalized_params().items())))
                if key in seen:
                    continue
                seen.add(key)
                validation = validate_model_route(route, catalog)
                if hasattr(validation, "validated_route"):
                    classification = "EXACT_SDK_ROUTE_PROVEN"
                    detail = validation.validated_route.to_json()
                else:
                    classification = "BLOCKED"
                    detail = {"code": validation.code, "message": validation.message}
                entries.append(
                    {
                        "model_id": route.model_id,
                        "params": route.normalized_params(),
                        "example_step": step,
                        "example_tier_overlay": label,
                        "reachable_via_canonical_tier_overlay": True,
                        "default_production_low_low": is_default_overlay,
                        "classification": classification,
                        "validation": detail,
                    }
                )

    return {
        "status": "OK",
        "workspace": workspace,
        "routes": entries,
        "summary": {
            "EXACT_SDK_ROUTE_PROVEN": sum(
                1 for e in entries if e["classification"] == "EXACT_SDK_ROUTE_PROVEN"
            ),
            "BLOCKED": sum(1 for e in entries if e["classification"] == "BLOCKED"),
        },
    }


def main() -> int:
    workspace = str(GAME_ROOT)
    report = asyncio.run(_audit(workspace=workspace))
    out = HERE / ".dogfood-evidence" / "ACTIVE_MODEL_ROUTE_COVERAGE.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report.get("status") != "OK":
        return 2
    blocked_active = [
        e
        for e in report.get("routes", [])
        if e["classification"] == "BLOCKED"
        and e.get("default_production_low_low")
    ]
    return 1 if blocked_active else 0


if __name__ == "__main__":
    sys.exit(main())
