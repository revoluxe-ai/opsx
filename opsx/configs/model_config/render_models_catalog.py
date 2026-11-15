"""Генерация MODELS.md из materialized Cursor API registry.

Источник: ``.cursor/pipeline/shared/MODEL_REGISTRY.json`` (sync:
``.cursor/tools/sync-cursor-models.py`` / ``/opsx-meta-sync-models``).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = project_root()
REGISTRY_PATH = resource_path(REPO_ROOT, ".cursor", "pipeline", "shared", "MODEL_REGISTRY.json")
OUTPUT_PATH = Path(__file__).resolve().parent / "MODELS.md"


def _param_values(param: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in param.get("values") or []:
        if not isinstance(item, dict):
            continue
        val = item.get("value")
        if val is None:
            continue
        label = item.get("displayName")
        if label and str(label) != str(val):
            out.append(f"{val} ({label})")
        else:
            out.append(str(val))
    return out


def _format_parameters(parameters: list[Any]) -> list[str]:
    lines: list[str] = []
    if not parameters:
        lines.append("- _(нет объявленных parameters в API)_")
        return lines
    for param in parameters:
        if not isinstance(param, dict):
            continue
        pid = param.get("id") or "?"
        display = param.get("displayName") or pid
        values = _param_values(param)
        if values:
            lines.append(f"- **`{pid}`** ({display}): {', '.join(f'`{v}`' for v in values)}")
        else:
            lines.append(f"- **`{pid}`** ({display})")
    return lines


def _format_variants(variants: list[Any]) -> list[str]:
    if not variants:
        return ["- —"]
    lines: list[str] = []
    for idx, variant in enumerate(variants, start=1):
        if not isinstance(variant, dict):
            continue
        label = variant.get("displayName") or variant.get("description") or f"variant {idx}"
        default = " **(default)**" if variant.get("isDefault") else ""
        params = variant.get("params") or []
        bits = [
            f"`{p.get('id')}={p.get('value')}`"
            for p in params
            if isinstance(p, dict) and p.get("id") is not None
        ]
        param_text = ", ".join(bits) if bits else "no params"
        lines.append(f"- {label}{default}: {param_text}")
    return lines


def _opsx_header() -> str:
    return """# Справочник моделей

## OPSX repair (исполняемый выбор)

Профили: ``models/composer.py`` (repair), ``models/claude_haiku.py`` (Bugbot).
Маршрутизация — ``resolve_model.py``; маппинг в Cursor SDK —
``workflows/repair/cursor_adapter._cursor_sdk_params``.

| Роль | Объект | name |
|------|--------|------|
| repair hops | ``composer_super_low`` | `composer-2.5` |
| ``AgentRole.BUG_BOT`` | ``claude_haiku_bug_bot`` | `claude-haiku-5-5` |

Обновление каталога API ниже: ``python opsx/configs/model_config/render_models_catalog.py``
(после ``sync-cursor-models.py update --write`` или ``/opsx-meta-sync-models``).

---

"""


def render_catalog(registry: dict[str, Any]) -> str:
    synced = registry.get("synced_at", "unknown")
    source = registry.get("source", "unknown")
    models = registry.get("models") or []
    pseudo = registry.get("pseudo_models") or []

    parts = [
        _opsx_header().rstrip(),
        "## Cursor API catalog",
        "",
        f"- **synced_at:** `{synced}`",
        f"- **source:** `{source}`",
        f"- **model_count:** {len(models)} (+ {len(pseudo)} pseudo)",
        f"- **machine-readable:** `.cursor/pipeline/shared/MODEL_REGISTRY.json`",
        "",
        "### Индекс (все slug)",
        "",
    ]

    slugs = [p["slug"] for p in pseudo if p.get("slug")]
    slugs.extend(m["slug"] for m in models if m.get("slug"))
    parts.append(", ".join(f"`{s}`" for s in slugs))
    parts.extend(["", "### Сводная таблица", ""])
    parts.extend(_summary_table(registry))
    parts.extend(["", "### Полная выгрузка по моделям", ""])

    for pseudo_item in pseudo:
        slug = pseudo_item.get("slug", "?")
        parts.extend(
            [
                f"#### `{slug}` (pseudo)",
                "",
                pseudo_item.get("description") or "",
                "",
            ]
        )

    for model in models:
        slug = model.get("slug", "?")
        display = model.get("display_name") or slug
        fast = "yes" if model.get("is_fast_variant") else "no"
        aliases = model.get("aliases") or []
        desc = model.get("description")

        parts.append(f"#### `{slug}`")
        parts.append("")
        parts.append(f"- **display_name:** {display}")
        parts.append(f"- **fast_variant:** {fast}")
        if desc:
            parts.append(f"- **description:** {desc}")
        if aliases:
            parts.append(f"- **aliases:** {', '.join(f'`{a}`' for a in aliases)}")
        parts.append("")
        parts.append("**parameters (API):**")
        parts.extend(_format_parameters(model.get("parameters") or []))
        parts.append("")
        parts.append("**variants (preset param combinations):**")
        parts.extend(_format_variants(model.get("variants") or []))
        parts.append("")

    parts.append("")
    return "\n".join(parts)


def _summary_table(registry: dict[str, Any]) -> list[str]:
    lines = [
        "| slug | display | fast? | param ids | variant count | aliases |",
        "|------|---------|-------|-----------|---------------|---------|",
    ]
    for pseudo in registry.get("pseudo_models") or []:
        lines.append(
            f"| `{pseudo.get('slug', '?')}` | pseudo | — | — | — | — |"
        )
    for model in registry.get("models") or []:
        slug = model.get("slug", "?")
        display = (model.get("display_name") or slug).replace("|", "\\|")
        fast = "yes" if model.get("is_fast_variant") else "no"
        param_ids = ", ".join(
            str(p.get("id"))
            for p in (model.get("parameters") or [])
            if isinstance(p, dict) and p.get("id")
        ) or "—"
        vcount = len(model.get("variants") or [])
        aliases = ", ".join(model.get("aliases") or []) or "—"
        lines.append(
            f"| `{slug}` | {display} | {fast} | {param_ids} | {vcount} | {aliases} |"
        )
    return lines


def main() -> int:
    if not REGISTRY_PATH.is_file():
        print(f"MISSING_REGISTRY: {REGISTRY_PATH}", file=sys.stderr)
        return 1
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    OUTPUT_PATH.write_text(render_catalog(registry), encoding="utf-8")
    print(f"WROTE: {OUTPUT_PATH}")
    print(f"MODEL_COUNT: {len(registry.get('models', []))}")
    print(f"SYNCED_AT: {registry.get('synced_at')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
