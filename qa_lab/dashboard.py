"""Minimal authenticated read-only dashboard (no release authority)."""
import html
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import os
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path!='/':self.send_error(404);return
        content='<!doctype html><html lang="en"><meta charset="utf-8"><title>Independent QA Lab</title><body><h1>Independent QA Lab</h1><p>Execution evidence only. No release approval.</p><p>Use the authenticated API to inspect runs.</p></body></html>'.encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(content)
def main():
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','8780'))),Dashboard).serve_forever()
if __name__=='__main__':main()
