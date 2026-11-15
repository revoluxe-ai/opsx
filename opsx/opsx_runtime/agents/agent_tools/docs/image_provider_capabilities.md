# Image provider capabilities (Gate 0)

## ImageModelInputPort

- **Канал:** custom MCP tool `read_image_page` с paged base64 в `content` (см. `ImageModelInputPort`).
- **Используют:** child hop `image_asset`, visual review (тот же port).
- **Offline unit:** paging/compose — `test_image_provider_capability_spike.py`.
- **Live Gate 0 (обязателен для META GO):** `pytest -m gate0_live agents/agent_tools/tests/test_gate0_live_provider.py` при `CURSOR_API_KEY`. Контрольная картинка `gate0/control_reference.png` содержит маркер **только в PNG** (`gate0_fixture.GATE0_VISUAL_SECRET` не попадает в copy_text/rubric). Executor и reviewer должны вернуть `SEEN` и marker в `Gate0SightAnswer`.

## Repair budget

- Состояние: `apply-image/<task_id>/REPAIR-STATE.json` (attempts + deadline).
- Defaults: `repair_max_attempts=6`, `repair_deadline_seconds=3600` (override в `image_tasks`).
- **Прогресс:** visual `PASS` или снятые finding codes; смена `output_revision` alone — не прогресс.
- Исчерпание: `status=EXHAUSTED`, код `IMAGE_REPAIR_BUDGET_EXHAUSTED` / `IMAGE_TASK_DEADLINE`, `exhaust_reasons[]`.

## CursorProviderImageGenerationPort

- **Статус:** в репозитории нет прямого вызова Cursor Generate Image API.
- **Production:** `ComposeImageGenerationPort` + `cursor_compose_fallback` для `generative_required` до подключения bridge API.
- **BLOCKED:** `generative_required` при `allow_compose_fallback=False` → `GENERATION_UNAVAILABLE`.

## Owner next steps

1. Подтвердить multimodal канал Cursor SDK в spike-тесте (optional live).
2. При появлении API — реализовать запись в `cursor_generation.py` без обхода `ImageAssetAgent` hop.
