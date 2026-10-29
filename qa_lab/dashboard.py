"""Authenticated, read-only run dashboard for independent QA evidence."""
import hmac
import html
import os
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .core import Lab

class Dashboard(BaseHTTPRequestHandler):
    lab=None
    def do_GET(self):
        token=os.environ.get('QA_DASHBOARD_TOKEN','')
        if len(token)<24 or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
            self.send_response(401);self.send_header('WWW-Authenticate','Bearer');self.end_headers();return
        if self.path!='/':self.send_error(404);return
        with closing(self.lab.connect()) as db:
            rows=db.execute('SELECT id,project,status,recipe FROM runs ORDER BY created DESC LIMIT 50').fetchall()
        body=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Independent QA Lab</title><body><h1>Execution evidence</h1><table><tr><th>Run</th><th>Project</th><th>Status</th><th>Recipe</th></tr>']
        for row in rows:body.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>')
        body.append('</table><p>Read-only. No case closure or release authority.</p></body></html>')
        data=''.join(body).encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
def main():
    if len(os.environ.get('QA_DASHBOARD_TOKEN',''))<24:raise RuntimeError('QA_DASHBOARD_TOKEN required')
    handler=type('DashboardHandler',(Dashboard,),{'lab':Lab(os.environ.get('QA_DATA_DIR','/data'))})
    ThreadingHTTPServer((os.environ.get('QA_BIND_HOST','127.0.0.1'),int(os.environ.get('PORT','8780'))),handler).serve_forever()
if __name__=='__main__':main()
