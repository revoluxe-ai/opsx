# Cursor SDK package consolidation

## Layout

Все SDK-артефакты — в `agents/adapters/cursor_sdk/`. Старые flat-модули и `workflows/repair/cursor_adapter.py` — shim re-export.

DI source of truth: `agents/adapters/cursor_sdk/containers/`.  
`opsx/di/containers/{bridge,adapters}_container.py` — thin shim (+ `VerifyImplAdapters` mapping).

## Bridge lifecycle

**Colocated container:**

```python
from pathlib import Path
from dependency_injector import containers
from agents.adapters.cursor_sdk.containers import BridgeContainer

root = Path(...)
container = BridgeContainer()
container.repo_root.override(root)
container.change_id.override("my-change")
await container.init_resources()
bridge = await container.bridge_runtime()
try:
    ...
finally:
    await container.shutdown_resources()
```

**Verify-impl фасад (без смены `run_verify_impl` пока):**

```python
from opsx.di.main_container import MainContainer

mc = MainContainer("my-change", repo_root=root)
handle = await mc.open_bridge()
try:
    ctx = await mc.build_execute_context(handle.bridge)
finally:
    await mc.close_bridge(handle)
```

**Текущий CLI (`run_verify_impl.execute`):** `AsyncExitStack` + `async with CursorBridgeRuntime(...)` — эквивалент Resource.

## Follow-up

1. Перевести `run_verify_impl.execute` на `MainContainer` / colocated `BridgeContainer` вместо ручного `AsyncExitStack`.
2. Опционально: `opsx/di/main_container.py` импортировать `BridgeContainer` напрямую из `cursor_sdk.containers` (shim можно оставить).
3. `bridge_types` в репозитории нет — типы bridge в `cursor_bridge_runtime.py` и `cursor_sdk/types.py`.
