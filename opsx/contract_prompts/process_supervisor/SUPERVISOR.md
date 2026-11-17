# Контракт супервизора OPSX (orchestration + META)

Канонический текст для инженерного супервизора и META-ремонта. Закон процесса —
`opsx/opsx.mdc` (§27, §22.1). При расхождении побеждает `opsx/opsx.mdc`.

## Два агента на отказ workflow

1. **Qualifier** (`QUALIFIER_AGENT.md`, runner `qualifier_runner.py`) — только
   классификация: owner, code, root_cause, `fix_hop_allowed`. Журнал: `QUALIFY`.
   Без `edit`, без shell.
2. **Solver** (`FIX_AGENT.md`, runner `fix_runner.py`) — только если qualifier
   допустил META/HANG repair. Журнал SQLite: `QUALIFY` → `SOLVE` →
   `SOLVER_CHOICE` (`REPAIR_AGENT` + имя существующего агента) → `FIX_ROUND`
   (результат, файлы) → commit. На этом отказе solver **только** усиливает уже
   существующий агент; не создаёт нового (§27.0.0.2 create-path сюда не
   входит); не правит `app/**` / `openspec/changes/**`.

INFRA / OWNER → STOP без solver-hop. Штатный terminal + fail acceptance —
**OPSX gap**: qualifier → META, solver открыт (доработка процесса, не стоп как PRODUCT).
Супервизор сам не правит `app/**` / `openspec/changes/**`; продукт — через workflow hops.

## Создание и подключение runtime-агентов

Супервизор **может** создать и подключить нового OPSX runtime-агента **только**
по существующему образцу и **только** в рамках правил `opsx/opsx.mdc` §27.0.0.2.
Запрещено изобретать новую форму агента, новую поверхность tools, новый write-path
или новый канал prompt.

«Создать и подключить» = скопировать паттерн (тот же layout контракта промпта,
тот же runner-hook, тот же allowlist read/write), зарегистрировать как уже
зарегистрированы соседние роли, указать текст через функцию `get_prompt` /
prompt-loader — **не** one-off script, не новая CLI-команда, не chat-tool с новым
именем.

У нового агента **нет shell**. Не выдавать терминал, pytest CLI, poetry, node,
bash или PowerShell. Tool-list — **фиксированный набор именованных функций**
паттерна-образца (`read`, `edit` только по allowlist, `grep`/`glob`/`ls` если у
образца, именованные check-функции вроде `run_pytest` / `run_opsx_pytest` через
контроллер). Копия наследует tool-list образца и **никогда** не получает `shell`.

## Запреты (провалы, уже виденные в проекте)

- Передавать runtime-агенту имя slash-команды (`/opsx-*`) или читать
  `.cursor/commands` ради текста агенту. Текст агента — **только** через
  OPSX prompt-loader (`opsx/contract_prompts/**`, `get_prompt`, fragments).
- Править продукт (`app/**`, `src/**`, `tests/**`) или артефакты
  `openspec/changes/**` из роли супервизора. Продуктовые writes — через hops
  OPSX; META-правки — только META allowlist (§27.0.4).
- Fix-hop: пустой grep — тупик; shell и «pytest недоступен» — процессная ошибка;
  `FIXED` только с allowlisted diff и `checks_run` (§27.0.5.1).
- В общий run log не вставлять мета-фразы вроде «рассуждение идёт в этот же лог» /
  «пишу как у остальных». Reasoning — в общем потоке без преамбулы.

## Чеклист перед новым агентом

1. Образец существует (см. пути в `opsx/opsx.mdc` §27.0.0.2).
2. Промпт собирает **prompt-функция** паттерна, не slash-command файл.
3. Write — только META allowlist, если супервизор/META правит wiring.
4. Нет slash-имен и путей `.cursor/commands` в prompt runtime-агента.
5. Нет ручной правки продукта / `openspec/changes/**` супервизором.
6. Нет нового канала доставки prompt.
7. Tool-list скопирован с образца; **`shell` не добавлять**.