"""ChatGPT-only Codex provider configuration contract tests."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from opsx_cli.codex_provider import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_REASONING_EFFORT,
    CodexProviderError,
    chatgpt_only_environment,
)


class CodexProviderTests(unittest.TestCase):
    def test_model_and_reasoning_defaults_are_explicit(self):
        self.assertEqual(DEFAULT_CODEX_MODEL, "gpt-6-luna")
        self.assertEqual(DEFAULT_REASONING_EFFORT, "low")

    def test_paid_api_environment_not_propagated(self):
        env = chatgpt_only_environment({
            "OPENAI_API_KEY": "secret",
            "CODEX_API_KEY": "secret",
            "AZURE_OPENAI_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://paid.invalid",
            "AZURE_OPENAI_ENDPOINT": "https://paid.invalid",
            "SAFE": "yes",
        })
        self.assertEqual(env, {"SAFE": "yes"})

    def test_explicit_empty_environment_does_not_reinstate_host_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}):
            self.assertEqual(chatgpt_only_environment({}), {})

    def test_provider_failure_has_stable_error_code(self):
        failure = CodexProviderError("CODEX_TOOL_AUTHORITY_UNPROVEN")
        self.assertEqual(failure.code, "CODEX_TOOL_AUTHORITY_UNPROVEN")
        self.assertIn(failure.code, str(failure))


if __name__ == "__main__":
    unittest.main()
