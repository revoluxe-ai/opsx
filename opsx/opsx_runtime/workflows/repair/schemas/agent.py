"""Модели hop receipt, выбор модели и usage для repair workflow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONFIGS_ROOT = Path(__file__).resolve().parents[4] / "configs"
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))

from model_config.agent_roles import REPAIR_RECEIPT_ROLES, AgentRole  # noqa: E402

DEFAULT_MODEL_ID = "claude-haiku-5-5"
DEFAULT_CONTEXT = "300k"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_AGENT_INPUT_LIMIT = 3_000_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelParam(StrictModel):
    id: str
    value: str | int | float | bool


class ModelSelection(StrictModel):
    id: str = DEFAULT_MODEL_ID
    params: list[ModelParam] = Field(
        default_factory=lambda: [
            ModelParam(id="context", value=DEFAULT_CONTEXT),
            ModelParam(id="reasoning_effort", value=DEFAULT_REASONING_EFFORT),
        ]
    )


class TokenUsage(StrictModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    def observed_input_tokens(self) -> int | None:
        parts = (
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )
        if any(value is None for value in parts):
            return None
        return sum(int(value) for value in parts if value is not None)


class WorkerReceipt(StrictModel):
    """Persisted outcome of one AI or deterministic hop."""

    hop: str
    role: AgentRole
    bug_id: str | None = None
    execution_id: str | None = None
    request_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    state: Literal["finished", "FAILED", "BLOCKED"]

    @field_validator("role", mode="before")
    @classmethod
    def migrate_legacy_role(cls, value: Any) -> Any:
        legacy = {
            "classify": AgentRole.CLASSIFIER,
            "red": AgentRole.RED_TEST_WORKER,
            "fix": AgentRole.IMPLEMENTATION_EXECUTOR,
            "green": AgentRole.GREEN_VERIFIER,
            "invariant": AgentRole.INVARIANT_CAPTURE_WORKER,
            "hunt": AgentRole.BOUNDED_HUNTER,
            "relation": AgentRole.RELATION_CLASSIFIER,
            "fanin": AgentRole.FAN_IN_ASSEMBLER,
        }
        if isinstance(value, AgentRole):
            return value
        mapped = legacy.get(str(value), value)
        if isinstance(mapped, AgentRole):
            return mapped
        return AgentRole(str(mapped))

    @field_validator("role")
    @classmethod
    def receipt_role_must_be_repair_hop(cls, value: AgentRole) -> AgentRole:
        if value not in REPAIR_RECEIPT_ROLES:
            raise ValueError(f"WorkerReceipt.role must be a repair hop role, got {value!r}")
        return value

    input_token_limit: int = DEFAULT_AGENT_INPUT_LIMIT
    usage: TokenUsage | None = None
    tokens_reported: int | None = None
    input_tokens_reported: int | None = None
    input_limit_exceeded: bool | None = None
    cost: dict[str, float] | None = None
    cost_status: str = "not_reported"
    agent_id: str | None = None
    run_id: str | None = None
    usage_source: str = "unavailable"
    billing_error: str | None = None
    budget_stop_reason: str | None = None
    error: str | None = None
    tool_calls_observed: int | None = None
    validated: bool = False
    payload: dict[str, Any] | None = None
    prompt_artifact: str | None = None
    response_raw_artifact: str | None = None
    prompt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    response_raw_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    model_id: str | None = None
