"""Codex app-server uses bounded but sufficient JSON-RPC stream frames."""
from __future__ import annotations
import asyncio
import json
import sys
import time
import unittest

from opsx_cli.codex_app_server import (
    CODEX_RPC_FRAME_LIMIT_BYTES, _line,
)
from opsx_cli.codex_provider import CodexProviderError


class CodexRpcFrameTests(unittest.IsolatedAsyncioTestCase):
    async def _spawn(self, n: int):
        code=(
            "import json,sys\n"
            "payload={'method':'item/completed','params':"
            "{'item':{'type':'agentMessage','text':'X'*"
            +str(n)+"}}}\n"
            "sys.stdout.write(json.dumps(payload)+'\\n')\n"
            "sys.stdout.flush()\n"
        )
        return await asyncio.create_subprocess_exec(
            sys.executable,"-u","-c",code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=CODEX_RPC_FRAME_LIMIT_BYTES,
        )

    async def test_normal_large_rpc_frame_exceeding_default_64_kib(self):
        proc=await self._spawn(120_000)
        try:
            event=await _line(proc,time.monotonic()+10)
            self.assertEqual(len(event["params"]["item"]["text"]),120_000)
        finally:
            await proc.wait()

    async def test_oversized_rpc_frame_is_explicit_failure(self):
        proc=await self._spawn(CODEX_RPC_FRAME_LIMIT_BYTES+100)
        try:
            with self.assertRaises(CodexProviderError) as caught:
                await _line(proc,time.monotonic()+10)
            self.assertEqual(
                str(caught.exception),"CODEX_APP_SERVER_RPC_FRAME_TOO_LARGE",
            )
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
