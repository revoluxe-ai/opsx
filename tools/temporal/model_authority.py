"""Граница запуска модели для Temporal SDK и CLI.

Доказывает Composer 2.5 Standard только явным маршрутом
``composer-2.5`` + ``fast=false``, сверенным с зафиксированным каталогом
SDK от 2026-09-17 (параметр ``fast``: false|true). Пустой params, inherit
и опущенный selector этим доказательством не являются.

Grok 4.7 Medium принимается, если каталог содержит id ``grok-4.7-medium``
без параметров кроме ``fast``, либо id ``grok-4.7`` и перечисляет
``reasoning_effort=medium`` / ``effort=medium``. Нет каталога или значения —
``MODEL_AUTHORITY_BLOCKED``, без подмены на другую модель.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

_EXECUTOR_ROOT = Path(__file__).resolve().parents[1] / "cursor_executor"
if str(_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR_ROOT))

from cursor_executor.preflight import validate_model_route  # noqa: E402
from cursor_executor.schemas import ModelRoute  # noqa: E402

COMPOSER_STANDARD_ID = "composer-2.5"
GROK_47_MEDIUM_ID = "grok-4.7-medium"
GROK_47_HIGH_SELECTOR = "grok-4.7-high"
GROK_47_SDK_ID = "grok-4.7"
COMPOSER_STANDARD_PARAMS = {"fast": "false"}
GROK_47_HIGH_PARAMS = {"reasoning_effort": "high", "fast": "false"}

# Зафиксированный фрагмент live-каталога SDK (2026-09-17): у composer-2.5
# единственный параметр fast со значениями false|true. Пустой params не
# выбирает Standard.
COMPOSER_STANDARD_CATALOG_EVIDENCE: dict[str, Any] = {
    "id": COMPOSER_STANDARD_ID,
    "displayName": "Composer 2.5",
    "parameters": [
        {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
    ],
    "evidence": "tools/temporal sdk live-catalog note 2026-09-17",
}

_FORBIDDEN_MARKERS = (
    "gpt",
    "claude",
    "sonnet",
    "opus",
    "gemini",
    "luna",
    "sol",
    "grok-4.6",
    "inherit",
    "auto",
)


class ModelAuthorityBlocked(ValueError):
    """Запуск модели запрещён: нет доказанного tier или маршрут вне allowlist."""

    def __init__(self, message: str) -> None:
        self.code = "MODEL_AUTHORITY_BLOCKED"
        super().__init__(f"{self.code}: {message}")


def composer_standard_route() -> ModelRoute:
    """Явный маршрут Composer Standard: ``fast=false``, не пустые params."""

    return ModelRoute(COMPOSER_STANDARD_ID, dict(COMPOSER_STANDARD_PARAMS))


def _reject_ambiguous_selector(selector: str, *, speed: str | None) -> str:
    name = (selector or "").strip()
    if not name or name.lower() in {"inherit", "auto", "default", "standard"}:
        raise ModelAuthorityBlocked(
            "omitted, inherited, or ambiguous tier is not proof of Composer Standard"
        )
    lowered = name.lower()
    if lowered.endswith("-fast") or lowered.endswith("_fast"):
        raise ModelAuthorityBlocked(f"fast tier is forbidden: {name}")
    if speed and str(speed).strip().lower() == "fast":
        raise ModelAuthorityBlocked("speed=fast is forbidden")
    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            raise ModelAuthorityBlocked(f"forbidden model route: {name}")
    return name


def _require_validated(route: ModelRoute, catalog: Sequence[Mapping[str, Any] | Any]) -> ModelRoute:
    validation = validate_model_route(route, catalog)
    if not hasattr(validation, "validated_route"):
        raise ModelAuthorityBlocked(
            f"catalog rejected {route.model_id}: {getattr(validation, 'message', validation)}"
        )
    validated = validation.validated_route
    if route.model_id == COMPOSER_STANDARD_ID and validated.params.get("fast") != "false":
        raise ModelAuthorityBlocked("Composer route is not explicit Standard (fast=false)")
    return validated


def prove_composer_standard(catalog: Sequence[Mapping[str, Any] | Any] | None = None) -> ModelRoute:
    """Доказать Composer Standard по зафиксированному каталогу и, если передан, живому.

    Args:
        catalog: необязательный каталог SDK. Если передан и не содержит
            ``fast=false`` для composer-2.5, запуск блокируется (нет подмены).

    Returns:
        Валидированный ModelRoute.

    Raises:
        ModelAuthorityBlocked: каталог противоречит Standard или не принимает маршрут.
    """

    route = composer_standard_route()
    proven = _require_validated(route, [COMPOSER_STANDARD_CATALOG_EVIDENCE])
    if catalog is not None:
        proven = _require_validated(route, catalog)
    if proven.params != COMPOSER_STANDARD_PARAMS:
        raise ModelAuthorityBlocked(
            "Composer Standard requires exact params fast=false; omitted params are not proof"
        )
    return proven


def _grok_47_catalog_entry(
    catalog: Sequence[Mapping[str, Any] | Any],
) -> Mapping[str, Any]:
    for item in catalog:
        if isinstance(item, Mapping) and str(item.get("id", "")) == GROK_47_SDK_ID:
            return item
    raise ModelAuthorityBlocked(
        f"{GROK_47_SDK_ID} is not in the supplied SDK catalog; no substitution"
    )


def _grok_47_high_params_from_catalog(entry: Mapping[str, Any]) -> dict[str, str]:
    raw_params = entry.get("parameters") or []
    param_ids: list[str] = []
    if isinstance(raw_params, Sequence) and not isinstance(raw_params, (str, bytes)):
        for param in raw_params:
            if isinstance(param, Mapping) and param.get("id"):
                param_ids.append(str(param["id"]))
    if "reasoning_effort" in param_ids:
        return {"reasoning_effort": "high", "fast": "false"}
    if "effort" in param_ids:
        return {"effort": "high", "fast": "false"}
    raise ModelAuthorityBlocked(
        "grok-4.7 catalog has no reasoning_effort or effort parameter; refusing to guess"
    )


def prove_grok_47_high(catalog: Sequence[Mapping[str, Any] | Any] | None) -> ModelRoute:
    """Принять selector ``grok-4.7-high`` только при каталоге SDK ``grok-4.7``.

    Маршрут: ``grok-4.7`` с ``reasoning_effort=high`` (или ``effort=high``, если
    в каталоге нет ``reasoning_effort``) и ``fast=false``.
    """

    if not catalog:
        raise ModelAuthorityBlocked(
            "grok-4.7-high has no SDK catalog evidence; refusing to guess parameters "
            "or substitute another model"
        )
    entry = _grok_47_catalog_entry(catalog)
    params = _grok_47_high_params_from_catalog(entry)
    route = ModelRoute(GROK_47_SDK_ID, params)
    return _require_validated(route, catalog)


def _param_values(param: Mapping[str, Any]) -> set[str]:
    values = param.get("values") or []
    found: set[str] = set()
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for item in values:
            if isinstance(item, Mapping) and item.get("value") is not None:
                found.add(str(item["value"]))
    return found


def _explicit_grok_effort(entry: Mapping[str, Any], level: str) -> dict[str, str]:
    """Маршрут ``grok-4.7`` с effort, только если каталог перечисляет это значение."""

    raw_params = entry.get("parameters") or []
    if not isinstance(raw_params, Sequence) or isinstance(raw_params, (str, bytes)):
        raise ModelAuthorityBlocked(
            "grok-4.7 catalog has no reasoning_effort or effort parameter; refusing to guess"
        )
    for param in raw_params:
        if not isinstance(param, Mapping):
            continue
        param_id = str(param.get("id") or "")
        if param_id not in ("reasoning_effort", "effort"):
            continue
        if level not in _param_values(param):
            raise ModelAuthorityBlocked(
                f"grok-4.7 catalog does not list {param_id}={level}; refusing to guess"
            )
        return {param_id: level, "fast": "false"}
    raise ModelAuthorityBlocked(
        "grok-4.7 catalog has no reasoning_effort or effort parameter; refusing to guess"
    )


def prove_grok_47_medium(catalog: Sequence[Mapping[str, Any] | Any] | None) -> ModelRoute:
    """Принять Grok 4.7 Medium только по записям переданного каталога.

    Отдельный id ``grok-4.7-medium`` допустим, если у него нет параметров
    кроме ``fast``. Иначе живой каталог ``grok-4.7`` с перечисленным
    ``reasoning_effort=medium`` (или ``effort=medium``) даёт тот же явный
    маршрут, что high для ``high``. Другую модель функция не подставляет.

    Raises:
        ModelAuthorityBlocked: каталога нет, значение medium не перечислено,
            или параметры отдельного id требуют угадывания.
    """

    if not catalog:
        raise ModelAuthorityBlocked(
            "grok-4.7-medium has no SDK catalog evidence; refusing to guess parameters "
            "or substitute another model"
        )
    entry: Mapping[str, Any] | None = None
    for item in catalog:
        if isinstance(item, Mapping) and str(item.get("id", "")) == GROK_47_MEDIUM_ID:
            entry = item
            break
    if entry is not None:
        raw_params = entry.get("parameters") or []
        param_ids = []
        if isinstance(raw_params, Sequence) and not isinstance(raw_params, (str, bytes)):
            for param in raw_params:
                if isinstance(param, Mapping) and param.get("id"):
                    param_ids.append(str(param["id"]))
        if any(param_id != "fast" for param_id in param_ids):
            raise ModelAuthorityBlocked(
                "grok-4.7-medium catalog parameters are not an approved explicit tier map; "
                "refusing to guess SDK params"
            )
        params = {"fast": "false"} if "fast" in param_ids else {}
        route = ModelRoute(GROK_47_MEDIUM_ID, params)
        return _require_validated(route, catalog)
    try:
        sdk_entry = _grok_47_catalog_entry(catalog)
    except ModelAuthorityBlocked:
        raise ModelAuthorityBlocked(
            "grok-4.7-medium is not in the supplied SDK catalog; no substitution"
        ) from None
    params = _explicit_grok_effort(sdk_entry, "medium")
    route = ModelRoute(GROK_47_SDK_ID, params)
    return _require_validated(route, catalog)


def authorize_model_selector(
    selector: str,
    *,
    catalog: Sequence[Mapping[str, Any] | Any] | None = None,
    speed: str | None = None,
) -> ModelRoute:
    """Разрешить selector на границе SDK-запуска либо отклонить его.

    Raises:
        ModelAuthorityBlocked: маршрут не доказан.
    """

    name = _reject_ambiguous_selector(selector, speed=speed)
    if name == COMPOSER_STANDARD_ID:
        return prove_composer_standard(catalog)
    if name == GROK_47_MEDIUM_ID:
        return prove_grok_47_medium(catalog)
    if name == GROK_47_HIGH_SELECTOR:
        return prove_grok_47_high(catalog)
    raise ModelAuthorityBlocked(f"unknown model route {name}: no fallback")


def authorize_cli_model(selector: str, *, speed: str | None = None) -> str:
    """Разрешить точный CLI slug. SDK-параметры здесь не изобретаются.

    Composer Standard для CLI — ровно ``composer-2.5`` (не ``*-fast``).
    ``grok-4.7-medium`` без каталога SDK не запускается.

    Returns:
        Тот же slug, если он доказан.

    Raises:
        ModelAuthorityBlocked: slug неоднозначен или не доказан.
    """

    name = _reject_ambiguous_selector(selector, speed=speed)
    if name == COMPOSER_STANDARD_ID:
        _ = prove_composer_standard(None)
        return name
    if name == GROK_47_MEDIUM_ID:
        raise ModelAuthorityBlocked(
            "CLI grok-4.7-medium is not launchable without SDK catalog evidence"
        )
    if name == GROK_47_HIGH_SELECTOR:
        raise ModelAuthorityBlocked(
            "CLI grok-4.7-high is not launchable without SDK catalog evidence"
        )
    raise ModelAuthorityBlocked(f"CLI model {name} is not an authorized slug")
