"""Постоянная запись SDK-диалогов OPSX в gitignored inbox.

Inbox: ``opsx/tests/inbox/<agent>/<execution_id>/``. Promotion в Git —
отдельная reviewed-операция через ``opsx/tests/recording_contract.py``.
"""

from opsx_recording.inbox_capture import (
    CaptureCollisionError,
    OpsxCaptureSession,
    begin_opsx_sdk_capture,
    complete_opsx_sdk_capture,
    inbox_root,
)

__all__ = [
    "CaptureCollisionError",
    "OpsxCaptureSession",
    "begin_opsx_sdk_capture",
    "complete_opsx_sdk_capture",
    "inbox_root",
]
