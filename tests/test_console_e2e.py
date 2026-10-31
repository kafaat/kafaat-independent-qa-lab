"""Real socket-level end-to-end: browser -> console -> API -> SQLite -> worker -> receipt."""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch

from qa_lab.api import Handler
from qa_lab.console import Console, LabAPI, SessionStore, password_hash
from qa_lab.core import Lab

TOKEN = "test-only-backend-bearer-123456789012345"
PASSWORD = "test-local-e2e-operator-password"


class ConsoleE2E(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"QA_API_TOKEN": TOKEN, "QA_UI_COOKIE_SECURE": "0",
                                           "QA_REQUIRE_GIT_SHA": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.lab = Lab(Path(self.temp.name) / "data")
        api = type("TestAPI", (Handler,), {"lab": self.lab, "log_message": lambda *args: None})
        self.api_server = ThreadingHTTPServer(("127.0.0.1", 0), api)
        self.start(self.api_server)
        client = LabAPI("http://127.0.0.1:" + str(self.api_server.server_port), TOKEN)
        console = type("TestConsole", (Console,), {
            "api": client,
            "sessions": SessionStore(),
            "password_hash_value": password_hash(PASSWORD, salt=b"0123456789abcdef"),
        })
        self.console_server = ThreadingHTTPServer(("127.0.0.1", 0), console)
        self.start(self.console_server)

    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(cleanup)

    def request(self, server, method, path, fields=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields)
        else:
            body = None
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            conn.close()

    def test_submit_execute_and_detect_tampering(self):
        status, _, _ = self.request(self.api_server, "GET", "/v1/runs")
        self.assertEqual(status, 401, "backend API must retain Bearer authentication")
        status, headers, _ = self.request(self.console_server, "POST", "/login",
                                          {"password": PASSWORD})
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, form = self.request(self.console_server, "GET", "/new", cookie=cookie)
        self.assertEqual(status, 200)
        csrf = re.search(r'name="csrf" value="([^"]+)"', form).group(1)
        idem = re.search(r'name="idempotency_key" value="([^"]+)"', form).group(1)
        params = {"csrf": csrf, "idempotency_key": idem, "project": "reviewed-checkout",
                  "source_sha": "a" * 40, "recipe": "python-smoke"}
        status, headers, _ = self.request(self.console_server, "POST", "/runs", params, cookie)
        self.assertEqual(status, 303)
        location = headers["Location"]
        self.assertTrue(location.startswith("/runs/"))
        rid = location.split("/")[-1]
        self.assertEqual(self.lab.get(rid)["status"], "PENDING")
        self.assertEqual(self.request(self.console_server, "POST", "/runs", params, cookie)[1]["Location"], location)
        with closing(self.lab.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(self.lab.execute(rid, self.temp.name)["status"], "PASSED")
        self.assertTrue(self.lab.verify(rid))
        status, _, body = self.request(self.console_server, "GET", location, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("qa-smoke-ok", body)
        self.assertIn("سليمة حسابيًا", body)
        self.assertNotIn(TOKEN, body)
        receipt = self.lab.get(rid)["receipt"]
        receipt["sha256"] = "0" * 64
        with closing(self.lab.connect()) as db, db:
            db.execute("UPDATE runs SET receipt=? WHERE id=?", (json.dumps(receipt), rid))
        self.assertFalse(self.lab.verify(rid))
        status, _, body = self.request(self.console_server, "GET", location, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("غير متحققة", body)
        self.assertNotIn(TOKEN, body)


if __name__ == "__main__":
    unittest.main()
