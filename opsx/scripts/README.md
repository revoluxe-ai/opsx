# `opsx/scripts/` — локальные OPSX-утилиты

| Файл | Назначение |
|------|------------|
| `zip_opsx_conversations.py` | Zip с conversation/live/run_dialogs артеfactами |
| `meta_commit_batch.sh` | Функции `commit_group`, preflight, запрет product/runs путей |
| `meta_commit_groups.sh` | **Редактируемый** список групп для пакетного meta-commit |
| `meta_commit_groups.example.sh` | Эталон 11 групп (2026-11-02); copy-paste в ACTIVE блок |

## Пакетный `/opsx-meta-commit ALL GO`

1. `git status` — инвентарь allowlisted process-путей (см. `opsx/sources.yaml`, без `opsx/runs/**`).
2. Разбить на смысловые группы в **`meta_commit_groups.sh`** (блок ACTIVE GROUPS).
3. Plan без записи:

   ```bash
   DRY_RUN=1 bash opsx/scripts/meta_commit_groups.sh
   ```

4. После Owner **`GO`** в чате с `/opsx-meta-commit ALL GO`:

   ```bash
   OPSX_META_COMMIT_GO=1 bash opsx/scripts/meta_commit_groups.sh
   ```

Скрипт **не заменяет** контракт команды (PCP, FAST_CHECK, protected branch) — только
механизирует повторяемые `git add` + `git commit` по точным путям.

Пример групп из успешного прогона 2026-11-02 — в комментариях внутри `meta_commit_groups.sh`
и в истории git (`f6334cc8` … `645e96db`).
