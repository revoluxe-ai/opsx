"""Load and resolve Cursor Agent model tiers for the Temporal POC."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL_CONFIG_PATH = Path(__file__).resolve().parent / "agent-models.json"
ALLOWED_TIERS = ("low", "medium", "high")
DEFAULT_OVERLAY_REL = (
    Path("opsx") / "runs" / "temporal" / "config" / "agent-models.overlay.json"
)


def parse_tier(name: str) -> str:
    """Normalize a model tier token (low|medium|high)."""
    tier = (name or "").strip().lower()
    if tier not in ALLOWED_TIERS:
        allowed = ", ".join(ALLOWED_TIERS)
        raise ValueError(f"Unknown model tier {name!r}; expected one of: {allowed}")
    return tier


def parse_tier_pair(review: str, repair: str) -> tuple[str, str]:
    """Parse the `/tempo-*` argument pair: review-tier repair-tier."""
    return parse_tier(review), parse_tier(repair)


@dataclass(frozen=True)
class TierSpec:
    name: str
    label: str
    model: str
    reasoning: str
    speed: str
    cli_model: str

    def to_dict(self) -> dict[str, str]:
        return {
            "tier": self.name,
            "label": self.label,
            "model": self.model,
            "reasoning": self.reasoning,
            "speed": self.speed,
            "cli_model": self.cli_model,
        }


class AgentModelConfig:
    def __init__(self, data: dict[str, Any], *, source_path: Path | None = None) -> None:
        self.source_path = source_path
        self.version = data.get("version", 1)
        self.default_tier = data.get("default_tier", "medium")
        self.steps: dict[str, str] = dict(data.get("steps") or {})
        self.speed_variants: dict[str, str] = dict(data.get("speed_variants") or {})
        self.tiers: dict[str, TierSpec] = {}
        for tier_name, tier_data in (data.get("tiers") or {}).items():
            self.tiers[tier_name] = self._build_tier(tier_name, tier_data)

    @staticmethod
    def load(path: str | Path | None = None) -> AgentModelConfig:
        config_path = Path(path) if path else DEFAULT_MODEL_CONFIG_PATH
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return AgentModelConfig(data, source_path=config_path)

    def _build_tier(self, tier_name: str, tier_data: dict[str, Any]) -> TierSpec:
        model = str(tier_data["model"])
        speed = str(tier_data.get("speed", "normal"))
        cli_model = tier_data.get("cli_model")
        if not cli_model:
            cli_model = self._resolve_speed_variant(model, speed)
        return TierSpec(
            name=tier_name,
            label=str(tier_data.get("label", tier_name)),
            model=model,
            reasoning=str(tier_data.get("reasoning", "auto")),
            speed=speed,
            cli_model=str(cli_model),
        )

    def _resolve_speed_variant(self, model: str, speed: str) -> str:
        if speed == "fast":
            return self.speed_variants.get(model, f"{model}-fast")
        return model

    def get_tier(self, tier_name: str) -> TierSpec:
        if tier_name not in self.tiers:
            raise KeyError(f"Unknown model tier: {tier_name}")
        return self.tiers[tier_name]

    def resolve_step(self, step: str) -> TierSpec:
        tier_name = self.steps.get(step, self.default_tier)
        return self.get_tier(tier_name)

    def resolve_cli_model(self, *, step: str | None = None, tier: str | None = None) -> str | None:
        if tier is not None:
            return self.get_tier(tier).cli_model
        if step is not None:
            return self.resolve_step(step).cli_model
        return self.get_tier(self.default_tier).cli_model

    def summary(self) -> dict[str, Any]:
        return {
            "source": str(self.source_path) if self.source_path else None,
            "default_tier": self.default_tier,
            "steps": {
                step: self.resolve_step(step).to_dict() for step in sorted(self.steps)
            },
            "tiers": {name: spec.to_dict() for name, spec in sorted(self.tiers.items())},
        }

    def with_steps(self, *, review: str, repair: str) -> "AgentModelConfig":
        """Return a copy with review/repair tiers overlaid (does not write disk)."""
        review_tier, repair_tier = parse_tier_pair(review, repair)
        self.get_tier(review_tier)
        self.get_tier(repair_tier)
        if self.source_path and self.source_path.is_file():
            data = json.loads(self.source_path.read_text(encoding="utf-8"))
        else:
            data = {
                "version": self.version,
                "default_tier": self.default_tier,
                "steps": dict(self.steps),
                "tiers": {name: spec.to_dict() for name, spec in self.tiers.items()},
                "speed_variants": dict(self.speed_variants),
            }
        data["steps"] = {
            "review": review_tier,
            "repair": repair_tier,
            "next_check": review_tier,
            "verification_executor": repair_tier,
        }
        return AgentModelConfig(data, source_path=self.source_path)


def write_step_overlay(
    *,
    review: str,
    repair: str,
    dest: Path,
    source: Path | None = None,
) -> Path:
    """Write a per-run agent-models overlay so worker+start share the same tiers."""
    overlay = AgentModelConfig.load(source).with_steps(review=review, repair=repair)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(
        (overlay.source_path or DEFAULT_MODEL_CONFIG_PATH).read_text(encoding="utf-8")
    )
    payload["steps"] = {
        "review": overlay.steps["review"],
        "repair": overlay.steps["repair"],
        "next_check": overlay.steps.get("next_check", overlay.steps["review"]),
        "verification_executor": overlay.steps.get(
            "verification_executor", overlay.steps["repair"]
        ),
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest
