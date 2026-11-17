# Preserved Owner decision from stage (2026-11-16)

### Переносимый OPSX — целевая архитектура (Owner Decision, 2026-11-16)

**Цель:** OPSX развивается как переносимый, IDE-независимый control plane для AI-assisted software work. Исполнение, состояние, проверка, recovery и authority принадлежат OPSX Core; Cursor, Codex, VS Code и другие IDE предоставляют только интерфейсы доступа. Это целевое направление, а не заявление о готовности всех IDE-адаптеров или миграции существующих проектов.

**Канонический источник:** полные инструкции OPSX-команд хранятся в ядре OPSX, а не в копиях каждой IDE. Установщик использует явный перечень команд и целевых IDE, устанавливает только нужные тонкие обёртки, защищает чужие файлы от перезаписи и допускает добавление другой IDE без удаления уже установленных. Предусмотренные форматы: Cursor — `.cursor/commands/*.md`, Codex — `.agents/skills/<command>/SKILL.md`, VS Code — `.github/prompts/<command>.prompt.md`. Ручной Cursor-контекст `.cursor/rules/opsx.mdc` сохраняет `alwaysApply: false`. MCP может быть дополнительным транспортом, но не обязательным слоем минимальной версии.

**Загрузка:** тонкая обёртка ссылается на каноническую команду в закреплённом Core и вызывает read-only CLI `opsx command show <name> --project . --expected-sha256 <digest>`. Несоответствие SHA-256, отсутствие команды или недоступность ядра означают STOP, а не реконструкцию инструкций из памяти модели. Обёртка, skill, prompt и Cursor Rule сами по себе не разрешают write-операции.

**Authority:** проект-потребитель владеет своим `opsx/project/capabilities.yaml`; отсутствие доказанной capability — fail closed. Выбор IDE не меняет write boundaries, executor/model/tool tier, разрешённый semantic scope и состояние execution. Предписанный tier нельзя заменять без разрешённой policy либо Owner authorization. Для Gate необходимы machine-checkable evidence с привязкой к execution/revision и проверкой freshness; retry требует convergence predicate и stop condition.

**Безопасность миграции:** переход с полных legacy-команд на обёртки требует per-command semantic reconciliation, проверки конфликтов, сохранения пользовательских изменений и recovery evidence. Не считать сходство текста, прежнюю установку или waiver доказательством эквивалентности. Unresolved drift остаётся BLOCKED, пока не устранён доказательствами или отдельным Owner-решением. Не запускать migration apply, destructive retirement, merge или deployment из факта обновления этого контекста.

**Готовность:** различать проверенную локальную функциональность, опубликованную версию OPSX и доказанный consumer cutover. Ни локальные тесты установщика, ни этот документ сами по себе не делают существующий продукт готовым к миграции.
