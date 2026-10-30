"""Authenticated read-only PostgreSQL run browser; no execution/release authority."""
from __future__ import annotations

import hmac
import html
import json
import os
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

STATUSES = frozenset({'PENDING', 'RUNNING', 'PASSED', 'FAILED', 'BLOCKED', 'TIMED_OUT'})
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
           'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}


def run_id(value):
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or not parsed.int:
            raise ValueError('invalid run ID')
        return value
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid run ID') from None


def filters(query):
    if len(query) > 2048:
        raise ValueError('query too long')
    params = parse_qs(query, keep_blank_values=True, max_num_fields=8)
    if set(params) - {'status', 'project', 'limit', 'after'} or any(len(v) != 1 for v in params.values()):
        raise ValueError('unknown or repeated filter')
    result = {key: values[0] for key, values in params.items() if values[0]}
    if 'status' in result and result['status'] not in STATUSES:
        raise ValueError('invalid status')
    if 'project' in result and (len(result['project']) > 128 or any(ord(c) < 32 for c in result['project'])):
        raise ValueError('invalid project')
    raw_limit = result.get('limit', '50')
    if not raw_limit.isascii() or not raw_limit.isdecimal() or not 1 <= int(raw_limit) <= 100:
        raise ValueError('limit must be between 1 and 100')
    result['limit'] = int(raw_limit)
    if 'after' in result:
        run_id(result['after'])
    return result


class ReadOnlyRuns:
    def __init__(self, queue):
        self.queue = queue

    @contextmanager
    def connection(self):
        with self.queue._connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout = '3000ms'")
            yield db

    def list(self, options):
        clauses, values = [], []
        if options.get('status'):
            clauses.append('status=%s')
            values.append(options['status'])
        if options.get('project'):
            clauses.append("request->>'project'=%s")
            values.append(options['project'])
        if options.get('after'):
            clauses.append('id < %s::uuid')
            values.append(run_id(options['after']))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        limit = options['limit']
        with self.connection() as db:
            rows = db.execute("SELECT id,status,request->>'project',request->>'recipe' FROM qa_jobs" +
                              where + ' ORDER BY id DESC LIMIT %s', (*values, limit + 1)).fetchall()
        items = [{'run_id': str(r[0]), 'status': r[1], 'project': str(r[2])[:128],
                  'recipe': str(r[3])[:128]} for r in rows[:limit]]
        return {'runs': items, 'next_cursor': items[-1]['run_id'] if len(rows) > limit else None,
                'ordering': 'run-id-desc-not-chronological', 'authority': 'read-only'}

    def detail(self, rid):
        rid = run_id(rid)
        with self.connection() as db:
            row = db.execute("SELECT id,status,request->>'project',request->>'recipe',"
                             "request->>'source_sha',receipt FROM qa_jobs WHERE id=%s::uuid", (rid,)).fetchone()
            if row is None:
                raise KeyError(rid)
            events = db.execute("SELECT o.id,o.subject,o.published_at IS NOT NULL,"
                                "i.event_id IS NOT NULL,COALESCE(o.subject=i.subject AND o.payload=i.payload,false) "
                                "FROM qa_outbox o LEFT JOIN qa_inbox i ON i.event_id=o.id "
                                "WHERE o.payload->>'run_id'=%s ORDER BY o.subject,o.id LIMIT 101", (rid,)).fetchall()
        allowed = ('status', 'exit_code', 'recipe', 'source_sha', 'authority', 'environment_claim',
                   'reason', 'stdout_tail', 'stderr_tail')
        receipt = {key: value if type(value) in (int, float, bool) or value is None else str(value)[:2000]
                   for key, value in (row[5] or {}).items() if key in allowed}
        return {'run_id': str(row[0]), 'status': row[1], 'project': str(row[2])[:128],
                'recipe': str(row[3])[:128], 'requested_source_sha': row[4], 'receipt': receipt,
                'events': [{'event_id': str(r[0]), 'subject': r[1], 'published': r[2],
                            'received': r[3], 'payload_matches': r[4]} for r in events[:100]],
                'events_truncated': len(events) > 100, 'authority': 'read-only',
                'source_attestation': 'request-reference-only', 'production_accepted': False}


def render(data, options=None):
    esc = lambda value: html.escape(str(value), quote=True)
    page = ['<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>Independent QA Lab</title><body><nav><a href="/rejections">مراجعة الرسائل المرفوضة</a></nav><h1>المختبر المستقل — أدلة التنفيذ</h1>',
            '<p>قراءة فقط. مرجع المصدر في الطلب ليس إثباتًا مستقلًا للشيفرة المختبرة.</p>']
    if 'runs' in data:
        options = options or {}
        page += ['<form method="get"><label>المشروع <input name="project" value="' +
                 esc(options.get('project', '')) + '"></label><label>الحالة <select name="status">',
                 '<option value="">الكل</option>']
        for status in sorted(STATUSES):
            selected = ' selected' if options.get('status') == status else ''
            page.append(f'<option{selected}>{status}</option>')
        page += ['</select></label><button>تصفية</button></form>',
                 '<p>ترتيب ثابت بمعرف التشغيل، وليس بتاريخ الإنشاء.</p>',
                 '<table><tr><th>التشغيل</th><th>الحالة</th><th>المشروع</th><th>الوصفة</th></tr>']
        for item in data['runs']:
            rid = run_id(item['run_id'])
            page.append(f'<tr><td><a href="/runs/{rid}">{rid}</a></td>' +
                        ''.join('<td>' + esc(item[k]) + '</td>' for k in ('status', 'project', 'recipe')) + '</tr>')
        page.append('</table>')
        if data.get('next_cursor'):
            query = urlencode({**options, 'after': data['next_cursor']})
            page.append('<a href="/?' + esc(query) + '">التالي</a>')
    else:
        page += ['<a href="/">التشغيلات</a><h2>' + esc(data['run_id']) + '</h2>',
                 '<pre>' + esc(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)) + '</pre>']
    return ''.join(page) + '</body></html>'


def valid_token(token):
    return isinstance(token, str) and 24 <= len(token) <= 512 and '${{' not in token and not any(c.isspace() for c in token)


class Dashboard(BaseHTTPRequestHandler):
    queue = None
    store = None
    rejections_store = None

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, code, value, as_json=True):
        raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) if as_json else value).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8' if as_json else 'text/html; charset=utf-8')
        for key, header in HEADERS.items():
            self.send_header(key, header)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        token = os.environ.get('QA_DASHBOARD_TOKEN', '')
        values = self.headers.get_all('Authorization', [])
        return valid_token(token) and len(values) == 1 and hmac.compare_digest(values[0].encode('utf-8'), ('Bearer ' + token).encode('utf-8'))

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {'error': 'unauthorized'})
        if len(self.path) > 4096:
            return self.reply(414, {'error': 'uri_too_long'})
        target = urlsplit(self.path)
        store = self.store if self.store is not None else ReadOnlyRuns(self.queue)
        try:
            if (target.path in ('/rejections', '/api/rejections')
                    or target.path.startswith(('/rejections/', '/api/rejections/'))):
                from .rejection_review import response
                data, as_json = response(self.queue, target, self.rejections_store)
                return self.reply(200, data, as_json)
            if target.path in ('/', '/api/runs'):
                options = filters(target.query)
                data = store.list(options)
                return self.reply(200, data if target.path == '/api/runs' else render(data, options), target.path == '/api/runs')
            for prefix in ('/runs/', '/api/runs/'):
                if target.path.startswith(prefix):
                    if target.query:
                        raise ValueError('detail query not supported')
                    data = store.detail(run_id(target.path[len(prefix):]))
                    return self.reply(200, data if prefix == '/api/runs/' else render(data), prefix == '/api/runs/')
            return self.reply(404, {'error': 'not_found'})
        except (ValueError, TypeError):
            return self.reply(400, {'error': 'invalid_request'})
        except KeyError:
            return self.reply(404, {'error': 'not_found'})
        except Exception:
            return self.reply(503, {'error': 'storage_unavailable'})

    def do_POST(self):
        return self.reply(405 if self.authorized() else 401, {'error': 'read_only'})

    def log_message(self, format, *args):
        # Do not put filters, project names, or request headers into access logs.
        pass


def main():
    if not valid_token(os.environ.get('QA_DASHBOARD_TOKEN', '')):
        raise RuntimeError('valid QA_DASHBOARD_TOKEN required')
    dsn = os.environ.get('QA_POSTGRES_DSN')
    if not dsn:
        raise RuntimeError('QA_POSTGRES_DSN required')
    from .pg_queue import PostgresQueue
    handler = type('PGDashboard', (Dashboard,), {'queue': PostgresQueue(dsn)})
    with ThreadingHTTPServer((os.environ.get('QA_BIND_HOST', '127.0.0.1'), int(os.environ.get('PORT', '8780'))), handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
