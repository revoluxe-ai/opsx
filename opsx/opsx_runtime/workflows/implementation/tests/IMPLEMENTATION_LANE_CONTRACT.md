# Implementation lane — контракт стабилизации

Рабочий контракт для стенда и правок процесса. Единственный выбор стадии/слайса — `opsx-super-implement` plan. LangGraph исполняет план и направляет разрешённый repair. Агент не назначает следующую стадию.

Имя файла, SDK FINISHED, `tests_frozen=true` и ответ модели `APPLY_COMPLETE` доказательством не являются.

## Переходы

| Фактическое состояние | Разрешённое действие |
|---|---|
| Технический DESIGN некомплектен, контракт слайса не заморожен | DESIGN → REVIEW → повторная машинная проверка |
| Есть SCAFFOLD, принятого RED нет | RED пишет тесты и кандидат; runtime проверяет и принимает freeze |
| RED принят, IMPLEMENT нет | IMPLEMENT меняет разрешённый product-код, frozen tests не трогает |
| IMPLEMENT принят, GREEN нет | Историческая связь с RED, затем GREEN. Live RED-failure не требуется |
| GREEN актуален | Слайс пропускается |
| Чинится DESIGN будущего слайса | Принятые контракты и история других слайсов не меняются |
| Нужно менять уже frozen-контракт | Явный post-IMPLEMENT recovery с сохранением истории, не обычный повтор RED |
| Неустранимое противоречие утверждённых планов | STOP с источниками конфликта. Иных Owner-вопросов нет |

## Identity (норма)

Один и тот же набор frozen inputs может иметь legacy и canonical digest. Оба представления принимаются, если байты файлов совпадают с доказательством. Algorithm drift и совпадение одного hash сами по себе historical bound не делают.

Historical bound истинен только если одновременно:

- evidence RED полное и относится к этому slice;
- связь RED → IMPLEMENT цела;
- frozen tests/fixtures не мутированы относительно принятого снимка;
- digest совпал как legacy или canonical того же байтового набора.

`identityDriftOnly` / `canonical_algorithm_drift` не возвращают `ok: true`.
При провале linkage historical-путь не вызывает live pytest и не откатывается в operative `isDurableRedProven`.

Авторитетные `decision_digest`, exit codes проверки и transition decision считает runtime после проверки. Модель отдаёт результат работы и содержательный кандидат. Принятое историческое доказательство обычная стадия другого назначения не перезаписывает.

## DESIGN repair (норма)

До правки DESIGN фиксируется проверенное состояние каждого слайса.

- Принятый слайс: test paths, assertion semantics и frozen bytes не меняются.
- Будущий слайс: новые отдельные test paths и bindings. Повторное чтение чужого frozen-файла не разрешает дописывать в него тесты.
- R3/completeness не переписывают принятый слайс ради глобальной уникальности.
- `validateRedKnowledgeObligations` смотрит обязательства текущего слайса и явно назначенные межслайсовые, не flatMap всех слайсов.
- После содержательной правки DESIGN старый REVIEW недействителен: снова DESIGN review, затем машинная проверка.
- `TEST_CONTRACT_REBASELINE_REQUIRED` и owner packet не являются обращением к Owner. Если будущий слайс чинится отдельно, принятый контракт не мигрирует. Автомиграция только при доказанной совместимости требований, с сохранением исходных доказательств. Фиктивный Owner GO запрещён.
- `ALREADY_SATISFIED` не подменяет исторический `RED_PROVEN`.

## Repair, state, completion (норма)

- Operative RED FAIL → ошибки в state → тот же stage/slice → один корректирующий вызов → повторная машинная проверка.
- Второй FAIL → terminal `PAUSED` → END. Не GREEN, не begin-loop, не reset checkpoint.
- Неизвестный исход SDK → не запускать второго исполнителя.
- `apply_red_operative_validation` объявлен в GraphState, попадает в следующий prompt, чистится после PASS и не течёт в следующий слайс.
- Нарушение write boundary не принимается. Автооткат всего checkout запрещён.
- Plan subprocess: видимые begin/end/duration и timeout. Повторный plan без смены входов в том же переходе не выполняется.
- `RECOVERY_PAUSED`: нет dispatch и нет undefined invocation; сохранены code, recovery_contract, stage/slice, errors.
- В verify родитель входит только после глобального `APPLY_COMPLETE` (все нужные слайсы GREEN и task reconciliation). Exit 0 сам по себе успехом не является.

## Владение файлами

Один файл — один writer.

| Owner | Файлы |
|---|---|
| A | `opsx_tools/opsx-apply-pipeline-identity.mjs`, `opsx-apply-red-transition-gate.mjs`, `opsx-apply-red-bindings.mjs`, identity-проверки в `opsx-apply-implement.mjs`, вызовы identity в `opsx-super-implement.mjs` |
| B | `opsx-apply-design.mjs`, `opsx-apply-design-review.mjs`, `opsx-apply-red-oracle-authority.mjs`, `opsx-bug-learning-apply.mjs`, `opsx-post-implement-test-contract-recovery.mjs`, `apply_design/agent.py`, `design_machine_validation.py` |
| C | `workflows/implementation/graph.py`, `routing.py`, `stage_dispatch.py`, `hop_stagnation.py`, `red_operative_validation.py`, `routing_contract.py`, `agents/state.py`, `apply_red/agent.py`, `hop_lane_status.py` |
| D | только новые тесты и фикстуры стенда |

B и C не правят файлы A. Общий стык: A экспортирует предикат, B/C только вызывают.

## Открытые дефекты (по чтению, до патча)

1. `implementFrozenTestIdentityMatchesRedBytes` принимает canonical drift как bound.
2. Historical path может упасть в live `isDurableRedProven`.
3. `validateApplyImplementArtifact` сверяет freeze с полем receipt, не с байтами.
4. DESIGN R3/completeness глобальны и могут переписать принятый слайс.
5. `apply_red_operative_validation` не в GraphState и не в следующем prompt.
6. Rebaseline останавливается как Owner realign.
7. Нет стенда: реальные JS-предикаты + compiled subgraph + сменный адаптер + SqliteSaver.

## Следующий шаг

D строит стенд на два слайса. Тесты кодируют этот контракт и сначала должны падать. A/B/C стартуют после красного прогона стенда.
