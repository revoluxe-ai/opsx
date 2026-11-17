"""Durable provider choice: API, storage, and execution snapshot acceptance."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from opsx_cli.context import OpsxError, Project
from opsx_cli.provider_settings import (
    PROVIDER_MODULES, load_provider, save_provider, provider_from_settings,
)


class ProviderPersistenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.project = Project.at(folder.name)
        self.project.settings.parent.mkdir(parents=True, exist_ok=True)
        self.initial = {
            "schema_version": 2,
            "project_id": "test-project",
            "name": "fixture",
            "product_profile": {"source_roots": ["app"]},
            "instance": {
                "provider": "cursor",
                "adapter": {"module": PROVIDER_MODULES["cursor"], "extras": ["cursor"]},
                "workflow": {"timeout_seconds": 1234},
                "environment": {"OTHER": "unchanged"},
            },
        }
        self.project.settings.write_text(
            json.dumps(self.initial, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_saved_provider_survives_new_reader(self):
        original = load_provider(self.project)
        result = save_provider(
            self.project, provider="codex",
            expected_revision=original["revision"],
        )
        self.assertEqual(result["provider"], "codex")
        saved = json.loads(self.project.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["instance"]["adapter"]["module"], PROVIDER_MODULES["codex"])
        self.assertEqual(saved["instance"]["provider"], "codex")
        self.assertEqual(saved["instance"]["adapter"]["extras"], ["cursor"])
        self.assertEqual(saved["product_profile"], self.initial["product_profile"])
        self.assertEqual(saved["instance"]["workflow"], self.initial["instance"]["workflow"])
        self.assertEqual(saved["instance"]["environment"], self.initial["instance"]["environment"])
        self.assertEqual(load_provider(self.project)["provider"], "codex")

    def test_stale_revision_fails_without_modifying_current_file(self):
        original = load_provider(self.project)
        save_provider(self.project, provider="codex", expected_revision=original["revision"])
        before = self.project.settings.read_bytes()
        with self.assertRaises(OpsxError) as caught:
            save_provider(self.project, provider="cursor", expected_revision=original["revision"])
        self.assertEqual(caught.exception.code, "EXECUTION_PROVIDER_REVISION_STALE")
        self.assertEqual(self.project.settings.read_bytes(), before)

    def test_invalid_provider_fails_without_write(self):
        original = load_provider(self.project)
        before = self.project.settings.read_bytes()
        with self.assertRaises(OpsxError):
            save_provider(self.project, provider="generic_shell", expected_revision=original["revision"])
        self.assertEqual(self.project.settings.read_bytes(), before)

    def test_conflicting_provider_module_fails_closed(self):
        doc = self.initial
        doc["instance"]["provider"] = "codex"
        with self.assertRaises(OpsxError) as caught:
            provider_from_settings(doc)
        self.assertEqual(caught.exception.code, "EXECUTION_PROVIDER_CONFIG_CONFLICT")

    def test_legacy_schema_v1_preserves_unrelated_keys(self):
        doc = {
            "schema_version": 1, "name": "legacy",
            "provider": "cursor",
            "adapter": {"module": PROVIDER_MODULES["cursor"]},
            "extra_data": ["KEEP"],
        }
        self.project.settings.write_text(json.dumps(doc), encoding="utf-8")
        rev = load_provider(self.project)["revision"]
        save_provider(self.project, provider="codex", expected_revision=rev)
        final = json.loads(self.project.settings.read_text(encoding="utf-8"))
        self.assertEqual(final["schema_version"], 1)
        self.assertEqual(final["extra_data"], ["KEEP"])
        self.assertEqual(final["adapter"]["module"], PROVIDER_MODULES["codex"])

    def test_missing_project_config_fails_closed(self):
        self.project.settings.unlink()
        with self.assertRaises(OpsxError) as caught:
            load_provider(self.project)
        self.assertEqual(caught.exception.code, "EXECUTION_PROVIDER_CONFIG_MISSING")

    def test_config_change_is_not_environment_change(self):
        original = load_provider(self.project)
        env = dict(os.environ)
        save_provider(self.project, provider="codex", expected_revision=original["revision"])
        self.assertEqual(dict(os.environ), env)


class PanelProviderTests(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:
            self.skipTest("FastAPI test client not installed")
        self.TestClient = TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Project.at(tmp.name)
        self.project.settings.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 2, "instance": {
            "provider": "cursor",
            "adapter": {"module": PROVIDER_MODULES["cursor"], "extras": ["cursor"]},
        }}
        self.project.settings.write_text(json.dumps(data), encoding="utf-8")
        from opsx_cli.panel import create_app
        self.state = {
            "instance_id": "test-instance", "url": "http://testserver",
            "token": "test-token", "active_run_id": None,
        }
        self.app = create_app(self.project, self.state)
        self.client = TestClient(self.app)
        self.client.__enter__()  # keep the ASGI loop alive across HTTP calls
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.get("/")  # establish CSRF-protected session

    def test_api_persists_across_new_client(self):
        res = self.client.get("/api/provider")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["provider"], "cursor")
        updated = self.client.post("/api/provider", json={
            "provider": "codex", "expected_revision": res.json()["revision"],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["provider"], "codex")
        self.assertEqual(load_provider(self.project)["provider"], "codex")
        self.assertEqual(self.client.get("/api/provider").json()["provider"], "codex")

    def test_invalid_provider_cannot_be_saved(self):
        response = self.client.post("/api/provider", json={
            "provider": "shell", "expected_revision": load_provider(self.project)["revision"],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(load_provider(self.project)["provider"], "cursor")

    def test_csrf_is_required_for_provider_change(self):
        res = self.client.post(
            "/api/provider",
            headers={"Origin": "https://malicious.example"},
            json={"provider": "codex", "expected_revision": load_provider(self.project)["revision"]},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(load_provider(self.project)["provider"], "cursor")

    def test_active_run_rejects_provider_switch(self):
        # API gate blocks any new selection while the background workflow is active.
        async def blocked(*_args, **_kwargs):
            await asyncio.Event().wait()
        from opsx_cli.service import RunService
        with patch.object(RunService, "execute", blocked):
            response = self.client.post("/api/workflows", json={"change_id": "fixture"})
            self.assertEqual(response.status_code, 200, response.text)
            config = load_provider(self.project)
            refused = self.client.post("/api/provider", json={
                "provider": "codex", "expected_revision": config["revision"],
            })
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(load_provider(self.project)["provider"], "cursor")

    def test_workflow_captures_selected_provider(self):
        from opsx_cli.service import RunService
        called = []
        async def fake_execute(_service, **kwargs):
            called.append(kwargs)
            return {"status": "FAILED"}
        with patch.object(RunService, "execute", fake_execute):
            current = load_provider(self.project)
            saved = self.client.post("/api/provider", json={
                "provider": "codex", "expected_revision": current["revision"],
            })
            self.assertEqual(saved.status_code, 200)
            launched = self.client.post("/api/workflows", json={"change_id": "fixture"})
            self.assertEqual(launched.status_code, 200)
            run_id = launched.json()["run_id"]
            status = json.loads((self.project.runs / "controller" / run_id / "status.json").read_text())
            self.assertEqual(status["provider"], "codex")
            # Await background task to finish without invoking a provider.
            for _ in range(30):
                if called:
                    break
                import time
                time.sleep(.02)
            self.assertEqual(called[0]["provider"], "codex")


if __name__ == "__main__":
    unittest.main()
