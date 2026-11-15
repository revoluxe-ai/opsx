# Среда нового проекта

Этот файл — основные инструкции, каким должен быть проект для OPSX. Порядок онбординга и слои тестов — соседние файлы этого каталога (`README.md`, `testing-policy.md`). История и норма контроля времени pytest — `@opsx` §1.1–§1.2 и §27.0.2–§27.0.3.

Проект обязан иметь библиотеку `pytest-timeout` (`>=2.3,<3`) в группах Poetry `dev` и `test`: 15 с на тест, метод `thread`, персональный маркер `@pytest.mark.timeout(N)` не выше 300 с. Свой таймер вместо библиотеки не писать. Тишина без новой строки хода — ошибка: subprocess убивается, finding идёт в триаж, весь verify из-за таймаута не обрывается.

Один источник профиля среды: `opsx/project/infra/infra-profile.yaml`.

В профиле задать сервисы, образы, порты, comment identity disposable-БД, healthcheck, тюнинг PostgreSQL для одноразового инстанса, имена env-переменных (не значения), tier-политику и триггеры полного infra-регресса.

Verify-стадия `infra_tests` сама определяет профиль ОС (Windows Docker Desktop, WSL, Linux, Cloud Agent) и выбирает ADOPT, START, START_ALT_PORT или UNAVAILABLE. Чужой процесс на порту не убивается. Усыновлённая БД на teardown не останавливается.

Runbook (`known-issues.yaml`) пополняется записями `proposed`. Статус `verified` ставится только после того, как remedy реально помог следующему probe или run. `runbook.md` рендерится из профиля и журнала, руками агентом не редактируется.

Первый живой smoke и снятие `baselines.json` — отдельное поручение Owner. Без него workflow остаётся на serial-прогоне и не включает `parallel_qualified`.
