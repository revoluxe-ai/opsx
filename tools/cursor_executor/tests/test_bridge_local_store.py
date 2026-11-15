"""Regression tests for jsonl bridge store on disposable agent cwd."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cursor_executor.bridge_local import (
    agent_local_options,
    bridge_launch_kwargs,
    bridge_workspace_for_agent_cwd,
    jsonl_local_store_config,
    linux_path_to_cursor_workspace,
    bridge_state_root,
    cursor_sdk_sqlite_store_root,
    selected_sdk_store_backend,
    sqlite_store_scope_digest,
)


class BridgeLocalStoreTests(unittest.TestCase):
    def test_jsonl_store_config(self) -> None:
        cfg = jsonl_local_store_config()
        self.assertEqual(cfg["type"], "jsonl")
        self.assertTrue(Path(cfg["rootDir"]).is_dir())

    def test_disposable_fixture_uses_repo_bridge_workspace(self) -> None:
        game = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(
            dir=game / "tools" / "temporal" / "fixtures" / "sdk_mutating_runs"
        ) as tmp:
            fixture = Path(tmp)
            bridge_ws = bridge_workspace_for_agent_cwd(str(fixture))
            self.assertEqual(Path(bridge_ws).resolve(), game.resolve())

    def test_agent_local_options_include_jsonl_store(self) -> None:
        game = Path(__file__).resolve().parents[3]
        opts = agent_local_options(agent_cwd=str(game))
        self.assertEqual(opts["store"]["type"], "jsonl")
        self.assertEqual(Path(opts["cwd"]).resolve(), game.resolve())

    def test_bridge_launch_kwargs_include_jsonl_local(self) -> None:
        game = Path(__file__).resolve().parents[3]
        kwargs = bridge_launch_kwargs(agent_cwd=str(game))
        self.assertIn("state_root", kwargs)
        self.assertEqual(kwargs["local"]["store"]["type"], "jsonl")

    def test_sqlite_scopes_are_stable_and_disjoint(self) -> None:
        game = Path(__file__).resolve().parents[3]
        digest_one = sqlite_store_scope_digest("verify_impl:one")
        expected_root = game / "opsx" / "runs" / "cursor-sdk" / digest_one
        first = bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:one",
        )
        again = bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:one",
        )
        second = bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:two",
        )
        opts = agent_local_options(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:one",
        )
        self.assertEqual(first["state_root"], again["state_root"])
        self.assertNotEqual(first["state_root"], second["state_root"])
        store = {"type": "sqlite", "rootDir": str(expected_root)}
        self.assertEqual(first["local"]["store"], store)
        self.assertEqual(opts["store"], first["local"]["store"])
        self.assertEqual(Path(first["state_root"]).resolve(), expected_root.resolve())
        self.assertTrue(expected_root.is_dir())
        legacy_jsonl = bridge_state_root()
        marker = legacy_jsonl / "legacy-marker"
        marker.write_text("keep", encoding="utf-8")
        bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:one",
        )
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_sqlite_requires_explicit_recovery_scope(self) -> None:
        game = Path(__file__).resolve().parents[3]
        for scope in (None, "", "   ", "x" * 513):
            with self.assertRaisesRegex(ValueError, "CURSOR_SQLITE_STORE_SCOPE_REQUIRED"):
                bridge_launch_kwargs(
                    agent_cwd=str(game), store_backend="sqlite", store_scope=scope,
                )
            with self.assertRaisesRegex(ValueError, "CURSOR_SQLITE_STORE_SCOPE_REQUIRED"):
                agent_local_options(
                    agent_cwd=str(game), store_backend="sqlite", store_scope=scope,
                )

    def test_backend_selection_is_explicit_and_strict(self) -> None:
        with patch.dict("os.environ", {"OPSX_CURSOR_SDK_STORE": "sqlite"}):
            self.assertEqual(selected_sdk_store_backend(), "sqlite")
        with patch.dict("os.environ", {"OPSX_CURSOR_SDK_STORE": "invalid"}):
            with self.assertRaisesRegex(ValueError, "CURSOR_SDK_STORE_BACKEND_INVALID"):
                selected_sdk_store_backend()
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(selected_sdk_store_backend(), "jsonl")

    def test_new_sqlite_scope_coexists_with_legacy_jsonl_state(self) -> None:
        game = Path(__file__).resolve().parents[3]
        legacy = Path(__file__).resolve().parents[1] / ".bridge-state" / "jsonl-store"
        legacy.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy / "checkpoint.ndjson"
        legacy_file.write_text("keep", encoding="utf-8")
        first = bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:pending",
        )
        again = bridge_launch_kwargs(
            agent_cwd=str(game), store_backend="sqlite",
            store_scope="verify_impl:pending",
        )
        self.assertEqual(first["state_root"], again["state_root"])
        self.assertEqual(first["local"]["store"]["type"], "sqlite")
        self.assertEqual(first["local"]["store"]["rootDir"], first["state_root"])
        self.assertTrue(Path(first["state_root"]).is_dir())
        self.assertEqual(legacy_file.read_text(encoding="utf-8"), "keep")

    def test_sqlite_root_under_opsx_runs_cursor_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "opsx").mkdir()
            root = cursor_sdk_sqlite_store_root(repo, "verify_impl:alpha")
            self.assertEqual(
                root,
                repo / "opsx" / "runs" / "cursor-sdk" / sqlite_store_scope_digest(
                    "verify_impl:alpha",
                ),
            )

    def test_unscoped_legacy_adapter_must_not_ignore_sqlite_opt_in(self) -> None:
        game = Path(__file__).resolve().parents[3]
        with patch.dict("os.environ", {"OPSX_CURSOR_SDK_STORE": "sqlite"}):
            with self.assertRaisesRegex(ValueError, "CURSOR_SQLITE_STORE_SCOPE_REQUIRED"):
                agent_local_options(agent_cwd=str(game))
            with self.assertRaisesRegex(ValueError, "CURSOR_SQLITE_STORE_SCOPE_REQUIRED"):
                bridge_launch_kwargs(agent_cwd=str(game))

    def test_linux_home_path_fallback_unc(self) -> None:
        unc = linux_path_to_cursor_workspace("/home/vkita/projects/stroy-system")
        self.assertIsNotNone(unc)
        assert unc is not None
        self.assertIn("wsl.localhost", unc.replace("/", "\\"))
        self.assertTrue(unc.replace("/", "\\").endswith("home\\vkita\\projects\\stroy-system"))


if __name__ == "__main__":
    unittest.main()
