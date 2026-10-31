"""Real local HTTP/browser flows; no JavaScript or administrative browser token."""
import http.client
import os
import re
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch

from qa_lab.console import Console, SessionStore, check_password, password_hash, query_options

PASSWORD = "synthetic-local-operator-password"
API_TOKEN = "synthetic-server-side-token-123456789"
RUN_ID = str(uuid.uuid4())


class FakeAPI:
    def __init__(self):
        self.submitted = []
        self.paths = []
    def request(self, method, path, payload=None):
        self.paths.append((method, path))
        if method == "GET" and path.startswith("/v1/runs?"):
            return {"runs": [{"run_id": RUN_ID, "project": '<script>bad</script>',
                              "source_sha": "a" * 40, "status": "PENDING", "recipe": "python-smoke"}],
                    "next_offset": 25}
        if method == "GET" and path == "/v1/runs/" + RUN_ID:
            return {"run_id": RUN_ID, "project": '<script>alert(1)</script>', "status": "BLOCKED",
                    "recipe": "python-smoke", "source_sha": "a" * 40,
                    "receipt": {"stderr_tail": '<script>alert("secret")</script>', "authority": "execution-evidence-only"}}
        if method == "GET" and path == "/v1/evidence/" + RUN_ID:
            return {"verified": True, "authority": "execution-evidence-only"}
        if method == "POST" and path == "/v1/runs":
            self.submitted.append(payload)
            return {"run_id": RUN_ID, "reused": False}
        raise AssertionError((method, path))


class ConsoleHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = password_hash(PASSWORD, salt=b"0123456789abcdef")

    def setUp(self):
        self.api = FakeAPI()
        self.store = SessionStore(ttl=1800)
        self.env = patch.dict(os.environ, {"QA_UI_COOKIE_SECURE": "0", "QA_API_TOKEN": API_TOKEN})
        self.env.start()
        self.handler = type("TestConsole", (Console,), {
            "api": self.api, "sessions": self.store, "password_hash_value": self.encoded})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.env.stop()

    def request(self, method, path, fields=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        base = dict(headers or {})
        if cookie is not None:
            base["Cookie"] = cookie
        if fields is not None:
            base["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields)
        else:
            body = None
        try:
            conn.request(method, path, body=body, headers=base)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            conn.close()

    def login(self):
        code, headers, body = self.request("POST", "/login", {"password": PASSWORD})
        self.assertEqual(code, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotIn(PASSWORD, headers["Set-Cookie"])
        return self.cookie

    def csrf(self):
        code, _, body = self.request("GET", "/new", cookie=self.cookie)
        self.assertEqual(code, 200)
        return re.search(r'name="csrf" value="([^"]+)"', body).group(1)

    def test_password_hash_verification(self):
        self.assertTrue(check_password(PASSWORD, self.encoded))
        self.assertFalse(check_password("wrong password", self.encoded))
        self.assertFalse(check_password(PASSWORD, "pbkdf2$wrong"))
        with self.assertRaises(ValueError):
            password_hash("short")

    def test_login_logout_and_session_revocation(self):
        self.assertEqual(self.request("GET", "/")[0], 303)
        self.assertEqual(self.request("GET", "/login")[0], 200)
        self.assertEqual(self.request("POST", "/login", {"password": "bad"})[0], 401)
        self.login()
        code, headers, body = self.request("GET", "/", cookie=self.cookie)
        self.assertEqual(code, 200)
        self.assertIn("Content-Security-Policy", headers)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(API_TOKEN, body)
        token = self.csrf()
        code, headers, _ = self.request("POST", "/logout", {"csrf": token}, cookie=self.cookie)
        self.assertEqual(code, 303)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/", cookie=self.cookie)[0], 303)

    def test_submission_requires_login_csrf_and_approved_recipe(self):
        self.login()
        token = self.csrf()
        fields = {"csrf": token, "idempotency_key": "a" * 32,
                  "project": "synthetic", "source_sha": "b" * 40, "recipe": "python-smoke"}
        self.assertEqual(self.request("POST", "/runs", {**fields, "csrf": "wrong"},
                                      cookie=self.cookie)[0], 403)
        self.assertEqual(self.request("POST", "/runs", {**fields, "recipe": "shell"},
                                      cookie=self.cookie)[0], 400)
        self.assertEqual(self.request("POST", "/runs", {**fields, "source_sha": "bad"},
                                      cookie=self.cookie)[0], 400)
        self.assertEqual(self.request("POST", "/runs", fields)[0], 401)
        self.assertEqual(self.request("POST", "/runs", fields, cookie=self.cookie,
                                      headers={"Origin": "https://evil.example"})[0], 403)
        code, headers, _ = self.request("POST", "/runs", fields, cookie=self.cookie)
        self.assertEqual(code, 303)
        self.assertEqual(headers["Location"], "/runs/" + RUN_ID)
        self.assertEqual(self.api.submitted[0]["idempotency_key"], "a" * 32)
        self.assertEqual(len(self.api.submitted), 1)

    def test_html_escaping_detail_and_pagination(self):
        self.login()
        code, _, body = self.request("GET", "/?q=bad&status=PENDING", cookie=self.cookie)
        self.assertEqual(code, 200)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertIn("offset=25", body)
        self.assertIn("q=bad", self.api.paths[-1][1])
        code, _, body = self.request("GET", "/runs/" + RUN_ID, cookie=self.cookie)
        self.assertEqual(code, 200)
        self.assertNotIn('<script>alert("secret")</script>', body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("سليمة حسابيًا", body)
        self.assertEqual(self.request("GET", "/runs/not-a-uuid", cookie=self.cookie)[0], 400)
        self.assertEqual(self.request("GET", "/?offset=-1", cookie=self.cookie)[0], 400)

    def test_local_host_policy_and_headers(self):
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertEqual(self.request("GET", "/static/console.css")[0], 200)
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 400)
        code, headers, _ = self.request("GET", "/login")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])

    def test_failed_login_throttled_and_expired_session(self):
        for _ in range(5):
            self.assertEqual(self.request("POST", "/login", {"password": "bad"})[0], 401)
        self.assertEqual(self.request("POST", "/login", {"password": PASSWORD})[0], 429)
        self.store.clear_failures("127.0.0.1")
        self.login()
        for record in self.store.sessions.values():
            record["expires"] = 0
        self.assertEqual(self.request("GET", "/", cookie=self.cookie)[0], 303)

    def test_query_strictness(self):
        for q in ("status=BOGUS", "q=%0A", "q=1&q=2", "offset=10001", "limit=2"):
            with self.subTest(q=q), self.assertRaises(ValueError):
                query_options(q)


if __name__ == "__main__":
    unittest.main()
