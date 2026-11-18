"""Codex uses the same three bounded apply-design capabilities as Cursor."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opsx/opsx_runtime"))
from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.adapters.codex_app_server.codex_adapter import CodexAppServerAdapter
from agents.capabilities.manifest import AgentCapabilityManifest, ReadDomain
from agents.config import ReadScope
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime
from opsx_cli.codex_tool_gateway import (
    CodexToolGateway, CodexToolDenied, dynamic_tool_specs,
)
from opsx_cli.codex_app_server import CodexServerHost, CodexAppServerOutcome
from opsx_cli.codex_provider import CodexProviderError
from opsx_cli.codex_turn_ledger import CodexTurnLedger


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, check=True,
    )


class CapabilityToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_capability_schema_and_host_call(self):
        calls=[]
        async def execute(args, _context):
            calls.append(dict(args))
            return {"ok": True, "result": "accepted"}
        spec=AgentCustomToolSpec(
            "Submit one bounded current-change artifact",
            {"type":"object","properties":{
                "content":{"type":"string"},
                "base_digest":{"type":"string"},
            },"required":["content","base_digest"],"additionalProperties":False},
            execute,
        )
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            gateway=CodexToolGateway(
                root, ("submit_apply_design",),
                guard=lambda *_args:None, read_scope=object(),
                capability_specs={"submit_apply_design":spec},
            )
            generated=dynamic_tool_specs(
                ["submit_apply_design"],capability_specs=gateway.capability_specs,
            )
            self.assertEqual(generated[0]["inputSchema"],spec.input_schema)
            host=CodexServerHost(gateway)
            host.thread_id="thread-a";host.turn_id="turn-a"
            reply=await host.response_for_async({
                "id":10,"method":"item/tool/call",
                "params":{"threadId":"thread-a","turnId":"turn-a",
                    "tool":"submit_apply_design","namespace":"opsx",
                    "arguments":{"content":"{}", "base_digest":"digest"}},
            })
            self.assertTrue(reply["result"]["success"])
            self.assertEqual(calls,[{"content":"{}","base_digest":"digest"}])
            self.assertEqual(gateway.calls[-1]["verdict"],"ALLOWED")
            bad=await host.response_for_async({
                "id":11,"method":"item/tool/call",
                "params":{"threadId":"thread-a","turnId":"different",
                    "tool":"submit_apply_design","namespace":"opsx",
                    "arguments":{"content":"{}", "base_digest":"digest"}},
            })
            self.assertFalse(bad["result"]["success"])
            self.assertEqual(len(calls),1)

    async def test_no_unregistered_or_malformed_capability(self):
        async def execute(_args,_ctx):
            raise AssertionError("unexpected capability dispatch")
        spec=AgentCustomToolSpec(
            "Search",{"type":"object","properties":{}},execute,
        )
        with tempfile.TemporaryDirectory() as td:
            gateway=CodexToolGateway(
                Path(td),("search_repository",),
                guard=lambda *_args:None,read_scope=object(),
                capability_specs={"search_repository":spec},
            )
            for arguments in (
                {}, {"query":"x","domains":[]},
                {"query":"x","domains":["product"],"shell":"bad"},
            ):
                with self.assertRaises(CodexToolDenied):
                    await gateway.dispatch_async("search_repository",arguments)
            with self.assertRaises(CodexToolDenied):
                await gateway.dispatch_async("shell",{"command":"echo unsafe"})


class ApplyDesignAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        base=self.root/"main";base.mkdir()
        git(base,"init","-q")
        git(base,"config","user.name","OPSX")
        git(base,"config","user.email","opsx@invalid.example")
        (base/"opsx").mkdir()
        (base/"opsx/.keep").write_text("")
        git(base,"add",".");git(base,"commit","-qm","fixture")
        self.worktree=self.root/"linked"
        git(base,"worktree","add","-b","codex-test",str(self.worktree))
        self.env=patch.dict(os.environ,{"LOCALAPPDATA":str(self.root/"local")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manifest=AgentCapabilityManifest(
            change_id="fixture",repo_root=self.worktree,
            read_domains={
                "product":ReadDomain(name="product",roots=("src",)),
            },readable_sources={},prior_designs={},
            design_submission=None,
        )
        self.runtime=ApplyDesignToolRuntime(self.manifest)
        self.state={
            "repository_path":str(self.worktree),
            "run_uuid":"execution-1","change_id":"fixture",
            "model":{"id":"gpt-6-luna"},
            "capability_manifest":self.manifest,
            "apply_design_tool_runtime":self.runtime,
        }
        self.scope=ReadScope(allow=("**",),deny=())

    async def test_adapter_registers_exact_manifest_tools_and_durable_calls(self):
        async def fake_turn(gateway, *, identity_observer, **_kwargs):
            self.assertEqual(
                gateway.allowed,
                frozenset({"search_repository","read_source","submit_apply_design"}),
            )
            identity_observer("thread","thread-fixture")
            identity_observer("turn","turn-fixture")
            response=await gateway.dispatch_async(
                "search_repository",{"query":"any","domains":["product"]},
            )
            self.assertIn("ok",response)
            return CodexAppServerOutcome(
                text="DONE",thread_id="thread-fixture",turn_id="turn-fixture",
                usage=None,calls=tuple(gateway.calls),
            )
        with patch(
            "agents.adapters.codex_app_server.codex_adapter.run_guarded_codex_turn",
            new=fake_turn,
        ):
            result=await CodexAppServerAdapter(timeout_seconds=120)._run_turn(
                self.state,prompt="Search manifest",
                tools=(),read_scope=self.scope,
                tool_call_guard=lambda *_args:None,agent_key="apply_design",
            )
        self.assertEqual(result.status,"COMPLETED")
        with CodexTurnLedger(self.worktree) as ledger:
            self.assertEqual(ledger.reconcile(),1)
            row=ledger.db.execute(
                "SELECT status,thread_id,turn_id,calls_json FROM turns",
            ).fetchone()
            self.assertEqual(row["thread_id"],"thread-fixture")
            self.assertEqual(row["turn_id"],"turn-fixture")
            self.assertIn("search_repository",row["calls_json"])

    async def test_foreign_manifest_or_agent_key_rejected(self):
        wrong=dict(self.state)
        wrong["capability_manifest"]=AgentCapabilityManifest(
            change_id="other",repo_root=self.worktree,
            read_domains={},readable_sources={},prior_designs={},
        )
        adapter=CodexAppServerAdapter(timeout_seconds=120)
        for state,key in ((wrong,"apply_design"),(self.state,"apply_implement")):
            with self.assertRaises(CodexProviderError) as caught:
                await adapter._run_turn(
                    state,prompt="No dispatch",tools=(),
                    read_scope=self.scope,tool_call_guard=lambda *_args:None,
                    agent_key=key,
                )
            self.assertIn("CAPABILITY_MANIFEST_UNAUTHORIZED",str(caught.exception))
