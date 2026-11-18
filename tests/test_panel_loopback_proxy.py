"""Panel health is a direct loopback request even with system proxies."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import threading
import unittest
from unittest.mock import patch

from opsx_cli.service import _http


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/health":
            self.send_error(404)
            return
        if self.headers.get("X-OPSX-Token") != "local-test-token":
            self.send_error(401)
            return
        payload=json.dumps({"instance_id":"fixture", "project_root":"fixture"}).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class PanelLoopbackProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_bypasses_proxy_and_preserves_token(self):
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),_Health)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            settings={
                "HTTP_PROXY":"http://127.0.0.1:1",
                "HTTPS_PROXY":"http://127.0.0.1:1",
                "http_proxy":"http://127.0.0.1:1",
                "https_proxy":"http://127.0.0.1:1",
                "NO_PROXY":"",
                "no_proxy":"",
            }
            with patch.dict(os.environ,settings):
                state={
                    "url":f"http://127.0.0.1:{server.server_port}",
                    "token":"local-test-token",
                }
                response=await asyncio.wait_for(_http(state,"/api/health"),timeout=4)
                self.assertEqual(response["instance_id"],"fixture")
                self.assertEqual(response["project_root"],"fixture")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_non_loopback_url_still_rejected(self):
        state={"url":"http://example.com:8765","token":"x"}
        from opsx_cli.context import OpsxError
        with self.assertRaises(OpsxError) as caught:
            await _http(state,"/api/health")
        self.assertEqual(caught.exception.code,"PANEL_STATE_INVALID")
