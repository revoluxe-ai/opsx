"""Live catalog preflight: exact model id and parameter validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .schemas import ModelRoute


@dataclass(frozen=True)
class PreflightFailure:
    code: str
    message: str
    details: dict[str, Any]


@dataclass(frozen=True)
class PreflightSuccess:
    validated_route: ModelRoute
    catalog_model_id: str


def _parameter_definitions(model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    params = model.get("parameters") or []
    if isinstance(params, Sequence):
        return [p for p in params if isinstance(p, Mapping)]
    return []


def _allowed_values(param_def: Mapping[str, Any]) -> list[str]:
    values = param_def.get("values") or []
    out: list[str] = []
    if isinstance(values, Sequence):
        for item in values:
            if isinstance(item, Mapping):
                out.append(str(item.get("value", "")))
    return out


def _param_def_to_wire(param_def: Any) -> Mapping[str, Any]:
    if isinstance(param_def, Mapping):
        return param_def
    if hasattr(param_def, "id"):
        values = []
        for item in getattr(param_def, "values", []) or []:
            if isinstance(item, Mapping):
                values.append(item)
            elif hasattr(item, "value"):
                values.append({"value": item.value, "displayName": getattr(item, "display_name", "")})
        return {"id": param_def.id, "values": values}
    return {}


def _catalog_entry_to_wire(entry: Any) -> Mapping[str, Any]:
    if isinstance(entry, Mapping):
        return entry
    if hasattr(entry, "to_json"):
        return entry.to_json()
    parameters = [
        _param_def_to_wire(p) for p in getattr(entry, "parameters", []) or []
    ]
    return {"id": getattr(entry, "id", ""), "parameters": parameters}


def validate_model_route(
    requested: ModelRoute,
    catalog: Sequence[Mapping[str, Any] | Any],
) -> PreflightSuccess | PreflightFailure:
    """Exact id match and parameter id/value validation against catalog."""
    catalog_by_id: dict[str, Mapping[str, Any]] = {}
    for entry in catalog:
        wire = _catalog_entry_to_wire(entry)
        model_id = str(wire.get("id", ""))
        catalog_by_id[model_id] = wire

    if requested.model_id not in catalog_by_id:
        return PreflightFailure(
            code="MODEL_ROUTE_UNAVAILABLE",
            message=f"model_id not in live catalog: {requested.model_id}",
            details={"model_id": requested.model_id},
        )

    model_wire = catalog_by_id[requested.model_id]
    defs_by_id = {str(p.get("id", "")): p for p in _parameter_definitions(model_wire)}
    catalog_param_ids = set(defs_by_id.keys())

    for param_id in requested.params:
        if param_id not in catalog_param_ids:
            return PreflightFailure(
                code="MODEL_ROUTE_UNAVAILABLE",
                message=f"unknown parameter id: {param_id}",
                details={"parameter_id": param_id, "model_id": requested.model_id},
            )

    for param_id, param_value in requested.params.items():
        allowed = _allowed_values(defs_by_id[param_id])
        if param_value not in allowed:
            return PreflightFailure(
                code="MODEL_ROUTE_UNAVAILABLE",
                message=f"unavailable parameter value for {param_id}",
                details={
                    "parameter_id": param_id,
                    "requested_value": param_value,
                    "allowed_values": allowed,
                },
            )

    for extra_id in catalog_param_ids:
        if extra_id and extra_id not in requested.params:
            # Catalog may expose optional params; caller must supply explicit set only.
            # Reject if caller sent unknown ids already handled; do not auto-fill missing.
            pass

    validated = ModelRoute(
        model_id=requested.model_id,
        params=requested.normalized_params(),
    )
    return PreflightSuccess(validated_route=validated, catalog_model_id=requested.model_id)
