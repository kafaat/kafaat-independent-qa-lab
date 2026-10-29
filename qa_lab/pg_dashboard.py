"""Authenticated read-only PostgreSQL dashboard, no release authority."""
import hmac
import html
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .pg_queue import PostgresQueue

class Dashboard(BaseHTTPRequestHandler):
    queue=None
    def do_GET(self):
        token=os.environ.get('QA_DASHBOARD_TOKEN','')
        if len(token)<24 or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
            self.send_response(401);self.end_headers();return
        if self.path!='/':self.send_error(404);return
        with self.queue._connect() as db:
            rows=db.execute("SELECT id,status,request->>'project',request->>'recipe' FROM qa_jobs ORDER BY id DESC LIMIT 50").fetchall()
        page=['<!doctype html><html><meta charset="utf-8"><title>QA Dashboard</title><h1>Independent QA — execution evidence</h1><table><tr><th>Run</th><th>Status</th><th>Project</th><th>Recipe</th></tr>']
        for row in rows:page.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>')
        page.append('</table><p>Read-only; no case or release authority.</p>')
        data=''.join(page).encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
def main():
    if len(os.environ.get('QA_DASHBOARD_TOKEN',''))<24:raise RuntimeError('QA_DASHBOARD_TOKEN required')
    dsn=os.environ.get('QA_POSTGRES_DSN')
    if not dsn:raise RuntimeError('QA_POSTGRES_DSN required')
    q=PostgresQueue(dsn)
    handler=type('PGDashboard',(Dashboard,),{'queue':q})
    ThreadingHTTPServer((os.environ.get('QA_BIND_HOST','127.0.0.1'),int(os.environ.get('PORT','8780'))),handler).serve_forever()
if __name__=='__main__':main()
