"""Общая секция промпта: схема ответа Pydantic для любого агента."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

SCHEMA_SECTION_MARKER = "## Формат ответа (строго)"
FINAL_JSON_OUTPUT_MARKER = "## Финальный ответ — строго JSON"

# Shared output rules: identical for every schema-bound agent, regardless of role.
# The concise tail is appended AFTER all agent context by ABCAdapter so the
# model does not finish by copying planning artifacts or reporting prose.
_JSON_OUTPUT_RULES = (
    "Отправь ровно один синтаксически корректный JSON-объект с полями и типами из схемы выше.",
    "Первый символ финального ответа — {, последний — }. Никаких ```json / ```, Markdown, заголовков, цитат, списков, YAML или Python dict.",
    "Не добавляй вводных фраз, комментариев, отдельного verdict, пояснений или текста после объекта.",
    "Строго соблюдай названия и регистр ключей и enum, required-поля, типы, null и дополнительные ограничения схемы.",
    "Строки экранируй по JSON; не оставляй незакрытых кавычек или скобок, trailing commas, NaN/Infinity или непарного текста.",
    "Не выдумывай данные ради валидного JSON; неопределённость отражай только разрешёнными схемой значениями, не заявляй PASS без evidence.",
    "Прежде чем завершить ответ, молча проверь: это один полный JSON-объект, без текста вне него, и он соответствует JSON Schema.",
)


def final_json_output_contract() -> str:
    """Short role-agnostic final instruction for any Pydantic-bound model run."""
    return "\n".join((FINAL_JSON_OUTPUT_MARKER, *_JSON_OUTPUT_RULES))


def schema_for_prompt(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema ответа для промпта: поля и типы, без имён Python-классов.

    Pydantic кладёт имя класса в ``title`` и в ключи ``$defs``. В промпт это
    не попадает: агент видит только контракт JSON и не ищет модели в репозитории.
    """
    cleaned = _without_model_identity(model.model_json_schema())
    if not isinstance(cleaned, dict):
        raise TypeError("prompt JSON schema must be an object")
    return cleaned


def _without_model_identity(node: Any, *, rename: dict[str, str] | None = None) -> Any:
    """Убрать ``title`` и заменить имена классов в ``$defs`` / ``$ref``."""
    if rename is None:
        mapping: dict[str, str] = {}
        if isinstance(node, dict):
            defs = node.get("$defs")
            if isinstance(defs, dict):
                for index, name in enumerate(defs):
                    mapping[str(name)] = f"part_{index}"
        return _without_model_identity(node, rename=mapping)
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key == "title":
                continue
            if key == "$defs" and isinstance(value, dict):
                cleaned["$defs"] = {
                    rename.get(str(name), str(name)): _without_model_identity(
                        body, rename=rename
                    )
                    for name, body in value.items()
                }
                continue
            if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                def_name = value.removeprefix("#/$defs/")
                cleaned[key] = f"#/$defs/{rename.get(def_name, def_name)}"
                continue
            cleaned[key] = _without_model_identity(value, rename=rename)
        return cleaned
    if isinstance(node, list):
        return [_without_model_identity(item, rename=rename) for item in node]
    return node


def drop_embedded_response_schema(prompt: str) -> str:
    """Вырезать секцию схемы, если роль вставила её в текст hop.

    Единственная копия для SDK-промпта — та, что дописывает ``ABCAgent``
    из ``payload_model``.
    """
    text = prompt
    while SCHEMA_SECTION_MARKER in text:
        start = text.find(SCHEMA_SECTION_MARKER)
        json_header = text.find("```json", start)
        if json_header < 0:
            break
        close = text.find("```", json_header + len("```json"))
        if close < 0:
            break
        end = close + len("```")
        while end < len(text) and text[end] in "\r\n":
            end += 1
        head = text[:start].rstrip()
        tail = text[end:].lstrip("\r\n")
        if head and tail:
            text = f"{head}\n\n{tail}"
        else:
            text = head or tail
    return text


def response_schema_section(model: type[BaseModel]) -> str:
    """Секция промпта: правила + разрешённые ключи + JSON Schema без имени класса.

    Одну и ту же строку ставит ``ABCAgent`` на первый hop и на validation retry.
    Лишние ключи запрещены: модели проекта собраны с ``extra=forbid``.
    """
    schema = schema_for_prompt(model)
    defs = schema.get("$defs")
    defs_map = defs if isinstance(defs, dict) else {}
    lines = [
        SCHEMA_SECTION_MARKER,
        "",
        "Ответ — один JSON-объект без Markdown-обёртки и текста вокруг.",
        "Никогда не выводи финальный verdict отдельно от JSON или в формате Python dict.",
        "Первый символ ответа — {, последний — }; никакого ```json.",

        "Используй только ключи из списка ниже. Любой другой ключ — ошибка валидации.",
        "Поля enum копируй дословно (регистр и подчёркивания).",
        "Не переноси имена полей из инструкций роли, если их нет в списке.",
        "Обязательные поля должны присутствовать, даже если значение null там, где схема это разрешает.",
        "Не ищи схему ответа в файлах репозитория: контракт — только JSON Schema ниже.",
        "",
        "### Разрешённые ключи",
        "",
    ]
    lines.extend(_describe_node(schema, defs_map, indent=0))
    lines.extend(
        [
            "",
            "### JSON Schema",
            "",
            "```json",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "```",
        ],
    )
    return "\n".join(lines)


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.removeprefix("#/$defs/")
        target = defs.get(name)
        if isinstance(target, dict):
            return target
    return node


def _type_label(node: dict[str, Any]) -> str:
    if "enum" in node and isinstance(node["enum"], list):
        values = " | ".join(json.dumps(item, ensure_ascii=False) for item in node["enum"])
        return f"enum {values}"
    if "anyOf" in node and isinstance(node["anyOf"], list):
        parts = [
            _type_label(item) if isinstance(item, dict) else "any"
            for item in node["anyOf"]
        ]
        return " | ".join(parts)
    kind = node.get("type")
    if kind == "array":
        items = node.get("items")
        if isinstance(items, dict):
            return f"array<{_type_label(items)}>"
        return "array"
    if isinstance(kind, str):
        return kind
    if "$ref" in node:
        return "object"
    return "any"


def _describe_node(
    node: dict[str, Any],
    defs: dict[str, Any],
    *,
    indent: int,
) -> list[str]:
    resolved = _resolve(node, defs)
    properties = resolved.get("properties")
    if not isinstance(properties, dict):
        return []
    required = set(resolved.get("required") or [])
    pad = "  " * indent
    lines: list[str] = []
    for name, raw in properties.items():
        if not isinstance(raw, dict):
            continue
        field = _resolve(raw, defs)
        flag = "required" if name in required else "optional"
        lines.append(f"{pad}- `{name}` ({flag}, {_type_label(raw)})")
        nested_props = field.get("properties")
        if isinstance(nested_props, dict):
            lines.extend(_describe_node(field, defs, indent=indent + 1))
        items = field.get("items")
        if isinstance(items, dict):
            item = _resolve(items, defs)
            if isinstance(item.get("properties"), dict):
                lines.append(f"{pad}  элемент массива:")
                lines.extend(_describe_node(item, defs, indent=indent + 2))
    return lines
