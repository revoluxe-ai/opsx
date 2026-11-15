# Repair agents

| Слой | Где |
|------|-----|
| Workflow | ``workflows/repair/`` |
| Исполнители | ``agents/repair/<role>/`` (напр. ``green_verifier/``, ``fix_bug/``, ``invariant_capture/``, ``bounded_hunter/``) |
| **Общие подстановки** (protocol, paths, shared rules) | ``agents/common_prompt_data/repair/`` |
| Сборка prompt + SDK hop | ``prompt_wire.py``, ``execute_role.py`` |
| Worker path по роли | ``role_contracts.py`` |

Паттерн роли: ``ABCAgent`` (``preflight`` / ``on_success``) + ``prompt/get_prompt.py``. Graph/CLI импортирует ``build_request_payload`` / ``get_prompt`` и вызывает ``agent.run(state, prompt=..., payload=...)`` → ``execute_role.run_role``.

Модель для hop: ``resolve_model_for_role`` → ``workflows.repair.repair_adapter_port.default_hop_model_selection`` → ``RepairAdapter(timeout_seconds=...)``. Справочник slug: ``MODELS.md``; смена модели — ``default_model.DEFAULT_MODEL`` (+ ``MODELS.md``).

``common_prompt_data`` — **только строки**, одинаковые для 2+ агентов. Не шаблон prompt.
