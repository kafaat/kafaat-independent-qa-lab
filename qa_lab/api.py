"""Local HTTP interface; deliberately read/submit only, no remote command execution."""
import json
import os
import hmac
from .intelligence import classify
from .legacy_guard import LegacyProbeGuard
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .core import Lab

class Handler(BaseHTTPRequestHandler):
    lab=None
    legacy_guard=LegacyProbeGuard()
    def send(self, status, payload):
        data=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers();self.wfile.write(data)
    def authorized(self):
        token=os.environ.get("QA_API_TOKEN","")
        values=self.headers.get_all("Authorization",[])
        return bool(token) and len(values)==1 and hmac.compare_digest(values[0],"Bearer "+token)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=="/healthz": return self.send(200,{"status":"ok"})
        if not self.authorized(): return self.send(401,{"error":"unauthorized"})
        if path=="/v1/runs":
            from .run_catalog import filters, list_runs
            try: return self.send(200,list_runs(self.lab,filters(urlsplit(self.path).query)))
            except (ValueError,TypeError): return self.send(400,{"error":"invalid_filter"})
        if path=="/v1/legacy/capabilities":
            from .legacy_bridge import probe
            try: return self.send(200,self.legacy_guard.call(lambda: probe("catalog")))
            except (OSError,ValueError,RuntimeError): return self.send(503,{"error":"legacy_unavailable"})
        if path.startswith("/v1/evidence/"):
            rid=path.removeprefix("/v1/evidence/")
            try:
                run=self.lab.get(rid)
                return self.send(200,{"run_id":rid,"verified":self.lab.verify(rid),"receipt":run["receipt"],"authority":"execution-evidence-only"})
            except KeyError: return self.send(404,{"error":"not_found"})
        if path.startswith("/v1/reconcile/"):
            try: return self.send(200,self.lab.reconcile(path.removeprefix("/v1/reconcile/")))
            except KeyError: return self.send(404,{"error":"not_found"})
        if path.startswith("/v1/triage/"):
            try: return self.send(200,classify(self.lab.get(path.removeprefix("/v1/triage/"))))
            except KeyError: return self.send(404,{"error":"not_found"})
        if path.startswith("/v1/runs/"):
            try: return self.send(200,self.lab.get(path.removeprefix("/v1/runs/")))
            except KeyError: return self.send(404,{"error":"not_found"})
        self.send(404,{"error":"not_found"})
    def do_POST(self):
        if not self.authorized(): return self.send(401,{"error":"unauthorized"})
        if urlsplit(self.path).path!="/v1/runs":return self.send(404,{"error":"not_found"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if size<1 or size>8192: return self.send(413,{"error":"size_limit"})
            request=json.loads(self.rfile.read(size))
            return self.send(202,self.lab.submit(request))
        except (ValueError,TypeError,json.JSONDecodeError) as e:
            return self.send(400,{"error":str(e)})

def serve(root,host="127.0.0.1",port=8765):
    handler=type("LabHandler",(Handler,),{"lab":Lab(root)})
    if not os.environ.get("QA_API_TOKEN"): raise RuntimeError("QA_API_TOKEN is required")
    server=ThreadingHTTPServer((host,port),handler)
    server.serve_forever()
