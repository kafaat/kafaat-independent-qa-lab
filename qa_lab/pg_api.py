"""PostgreSQL API surface, isolated from the legacy SQLite API.

Only accepts fixed recipe identifiers and records test requests.
No remote shell command or hospital case-management operations.
"""
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .pg_queue import PostgresQueue, canonical

ALLOWED_RECIPES = frozenset({'python-smoke'})

def validate_submission(req):
    if not isinstance(req, dict) or set(req) != {'idempotency_key','project','source_sha','recipe'}:
        raise ValueError('exact request fields required')
    if not all(isinstance(req[k],str) for k in req):
        raise ValueError('all fields must be strings')
    if not (1 <= len(req['idempotency_key']) <= 128 and 1 <= len(req['project']) <= 128):
        raise ValueError('invalid identifiers')
    if len(req['source_sha']) != 40 or any(c not in '0123456789abcdef' for c in req['source_sha']):
        raise ValueError('invalid source sha')
    if req['recipe'] not in ALLOWED_RECIPES:
        raise ValueError('recipe not allowed')
    return req

class PostgreSQLHandler(BaseHTTPRequestHandler):
    queue = None
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def log_message(self, format, *args):
        # Do not log request paths or supplied media.
        pass
    def reply(self, status, obj):
        data=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)
    def authorized(self):
        token=os.environ.get('QA_API_TOKEN','')
        values=self.headers.get_all('Authorization',[])
        return bool(token) and len(values)==1 and hmac.compare_digest(values[0].encode('utf-8'),('Bearer '+token).encode('utf-8'))
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/healthz': return self.reply(200,{'status':'ok','backend':'postgres'})
        if not self.authorized():return self.reply(401,{'error':'unauthorized'})
        if path.startswith('/v1/runs/'):
            try:return self.reply(200,self.queue.get(path[len('/v1/runs/'):]))
            except (KeyError,ValueError):return self.reply(404,{'error':'not_found'})
        return self.reply(404,{'error':'not_found'})
    def do_POST(self):
        if not self.authorized():return self.reply(401,{'error':'unauthorized'})
        if urlsplit(self.path).path=='/v1/audio-runs':
            from .audio_jobs import post
            return post(self)
        if urlsplit(self.path).path!='/v1/runs':return self.reply(404,{'error':'not_found'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 1<=length<=8192:return self.reply(413,{'error':'size_limit'})
            req=validate_submission(json.loads(self.rfile.read(length)))
            digest=hashlib.sha256(canonical(req).encode('utf-8')).hexdigest()
            return self.reply(202,self.queue.submit(req,digest))
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            return self.reply(400,{'error':str(exc)})

def serve():
    token=os.environ.get('QA_API_TOKEN','')
    if len(token)<24:raise RuntimeError('QA_API_TOKEN must be at least 24 characters')
    dsn=os.environ.get('QA_POSTGRES_DSN')
    if not dsn:raise RuntimeError('QA_POSTGRES_DSN required')
    queue=PostgresQueue(dsn)
    queue.init()
    handler=type('PGHandler',(PostgreSQLHandler,),{'queue':queue})
    ThreadingHTTPServer((os.environ.get('QA_BIND_HOST','127.0.0.1'),int(os.environ.get('PORT','8765'))),handler).serve_forever()

if __name__=='__main__':serve()
