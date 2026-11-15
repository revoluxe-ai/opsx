"""Unit tests: ``is_agent_json_object`` vs prose markers."""

from __future__ import annotations

from agents.utils.response import is_agent_json_object
from agents.verify.impl_verify.prompt.get_prompt import get_prompt
from agents.verify.impl_verify.schemas import VerifyImplAnswer
from agents.adapters.cursor_sdk.cursor_sdk_stream import _conversation_terminal_assistant_text


def test_is_agent_json_object_rejects_prose_impl_ok():
    assert not is_agent_json_object("✅ IMPL_OK")
    assert not is_agent_json_object("Verdict IMPL_OK after reading receipt")


def test_is_agent_json_object_accepts_minimal_json_object():
    assert is_agent_json_object('{"verdict":"IMPL_OK"}')


def test_impl_verify_prompt_does_not_embed_answer_model():
    text = get_prompt(
        repository_path="/tmp/repo",
        change_id="demo",
        artifact_bundle={"spec_paths": [], "artifacts": {}, "missing": []},
    )
    assert "VerifyImplAnswer" not in text
    assert "IMPL-VERIFY-RECEIPT.json" in text
    assert "`verdict`" in text


def test_conversation_recovery_requires_full_verify_impl_schema():
    """Checkpoint recovery не принимает только ``{"verdict":"IMPL_OK"}`` без полей схемы."""
    terminal = [
        {
            "type": "agentConversationTurn",
            "turn": {
                "steps": [
                    {
                        "type": "assistantMessage",
                        "message": {"text": '{"verdict":"IMPL_OK"}'},
                    },
                ]
            },
        }
    ]
    assert (
        _conversation_terminal_assistant_text(
            terminal,
            payload_model=VerifyImplAnswer,
        )
        is None
    )
