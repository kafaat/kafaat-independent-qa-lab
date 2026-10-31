"""Advisory-only triage service; deterministic classification, no model claim."""
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .reader import ReadOnlyLab
from .intelligence import classify

class TriageHandler(BaseHTTPRequestHandler):
    lab=None
    def reply(self,code,payload):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/healthz':
            try:
                self.lab.ready()
            except (OSError, sqlite3.Error):
                return self.reply(503,{'error':'storage_unavailable'})
            return self.reply(200,{'status':'ok','service':'triage','model':'rules-only'})
        token=os.environ.get('QA_API_TOKEN','')
        if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.reply(401,{'error':'unauthorized'})
        if not path.startswith('/v1/triage/'):return self.reply(404,{'error':'not_found'})
        try:
            output=classify(self.lab.get(path[len('/v1/triage/'):]))
            return self.reply(200,{'result':output,'advisory_only':True,'model':'rules-only'})
        except KeyError:return self.reply(404,{'error':'not_found'})
        except (OSError, sqlite3.Error, ValueError):
            return self.reply(503,{'error':'storage_unavailable'})

def serve():
    if not os.environ.get('QA_API_TOKEN'):raise RuntimeError('QA_API_TOKEN required')
    handler=type('TriageService',(TriageHandler,),{'lab':ReadOnlyLab(os.environ.get('QA_DATA_DIR','/data'))})
    ThreadingHTTPServer((os.environ.get('QA_BIND_HOST','0.0.0.0'),int(os.environ.get('QA_TRIAGE_PORT','8767'))),handler).serve_forever()
if __name__=='__main__':serve()
