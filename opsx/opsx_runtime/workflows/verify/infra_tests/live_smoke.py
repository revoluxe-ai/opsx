"""Живой smoke infra-среды. Без отдельного GO Owner процесс не стартует.

Запуск: ``OPSX_INFRA_LIVE_SMOKE=1 poetry run python -m workflows.verify.infra_tests.live_smoke``.
Этот модуль сам smoke не вызывает.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Отказать, пока Owner не выставил OPSX_INFRA_LIVE_SMOKE=1."""
    if os.environ.get("OPSX_INFRA_LIVE_SMOKE") != "1":
        print(
            "OPSX_STAGE_STOP code=LIVE_SMOKE_NOT_AUTHORIZED stage=infra_tests "
            "owner=owner\n"
            "evidence=OPSX_INFRA_LIVE_SMOKE is not 1\n"
            "next_action=set OPSX_INFRA_LIVE_SMOKE=1 only after an explicit Owner GO",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print("live smoke authorized; engine entry is workflows.verify.infra_tests.pipeline")


if __name__ == "__main__":
    main()
