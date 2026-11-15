"""Public stream timing constants for adapter live-log (monkeypatch surface)."""

# Тишина без полезного прогресса hop (opsx.mdc §27.0.3): не ждать дальше при
# heartbeat/wire без sdk_message. Согласовано по величине с
# ``QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS`` (300), без циклического импорта.
CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS = 300.0

# Пауза без полезных событий observe (диагностика GetRun).
CURSOR_STREAM_IDLE_TIMEOUT_SECONDS = 600.0

CURSOR_STREAM_GETRUN_POLL_SECONDS = 90.0

CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS = 15.0

CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS = 90.0

CURSOR_HOP_HARD_DEADLINE_SECONDS = 2700.0

CURSOR_HOP_CANCEL_DRAIN_SECONDS = 30.0

CURSOR_STREAM_CONVERSATION_RECOVERY_TIMEOUT_SECONDS = 60.0
