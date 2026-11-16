"""Resolve instance vs product settings from project.json."""
from __future__ import annotations

from typing import Any


def instance_layer(settings: dict[str, Any]) -> dict[str, Any]:
    if settings.get("schema_version", 1) >= 2 and isinstance(settings.get("instance"), dict):
        layer = dict(settings["instance"])
        if settings.get("product_profile"):
            layer["capabilities"] = settings["product_profile"]
        return layer
    return settings