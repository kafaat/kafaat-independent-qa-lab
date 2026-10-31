"""SQLite listing contract: bounded, parameterized, authenticated at HTTP boundary."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from qa_lab.core import Lab
from qa_lab.api import Handler
from qa_lab.run_catalog import filters, list_runs

TOKEN = "test-token-1234567890-1234567890"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lab = Lab(Path(self.tmp.name) / "db")
        self.ids = []
        for i, project in enumerate(("safe", "other", "safe", "<script>")):
            result = self.lab.submit({"idempotency_key": str(i), "project": project,
                                      "source_sha": "a" * 40, "recipe": "python-smoke"})
            self.ids.append(result["run_id"])
            with closing(self.lab.connect()) as db, db:
                db.execute("UPDATE runs SET created=? WHERE id=?", (float(i), result["run_id"]))

    def test_filters_are_bounded_and_reject_duplicates(self):
        for query in ("limit=0", "limit=101", "offset=10001", "offset=-1", "offset=1.5",
                      "status=UNKNOWN", "project=a&project=b", "foo=bar",
                      "project=%0A", "limit=%E2%85%A1", "q=x&q=y"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                filters(query)
        self.assertEqual(filters("status=PASSED&limit=2&offset=4")["offset"], 4)

    def test_ordered_pagination_and_search(self):
        a = list_runs(self.lab, filters("limit=2"))
        self.assertEqual([x["run_id"] for x in a["runs"]], self.ids[::-1][:2])
        self.assertEqual(a["next_offset"], 2)
        b = list_runs(self.lab, filters("limit=2&offset=2"))
        self.assertEqual([x["run_id"] for x in b["runs"]], self.ids[::-1][2:])
        self.assertIsNone(b["next_offset"])
        c = list_runs(self.lab, filters("q=safe"))
        self.assertEqual(len(c["runs"]), 2)
        self.assertEqual({x["project"] for x in c["runs"]}, {"safe"})
        self.assertNotIn("receipt", c["runs"][0])

    def test_sql_injection_is_literal(self):
        self.assertEqual(list_runs(self.lab, filters("project=x%27%20OR%201%3D1%20--"))["runs"], [])
        self.assertEqual(list_runs(self.lab, filters("q=%25"))["runs"], [])
        self.assertEqual(list_runs(self.lab, filters("project=%3Cscript%3E"))["runs"][0]["project"], "<script>")

    def test_http_requires_bearer_and_sanitizes_invalid_queries(self):
        env = patch.dict(os.environ, {"QA_API_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        handler = type("CatalogHandler", (Handler,), {"lab": self.lab, "log_message": lambda *args: None})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(close)

        def get(path, token=TOKEN):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
            try:
                headers = {"Authorization": "Bearer " + token} if token is not None else {}
                conn.request("GET", path, headers=headers)
                res = conn.getresponse()
                return res.status, json.loads(res.read())
            finally:
                conn.close()

        self.assertEqual(get("/v1/runs", None)[0], 401)
        code, data = get("/v1/runs?project=safe&limit=1")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["next_offset"], 1)
        self.assertEqual(get("/v1/runs?status=FAKE")[0], 400)
        self.assertEqual(get("/v1/runs?limit=100000")[0], 400)
        self.assertEqual(get("/v1/runs?status=FAILED")[1]["runs"], [])


if __name__ == "__main__":
    unittest.main()
