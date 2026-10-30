"""Read-only operator view of metadata-only NATS rejection audit records.

Environment-wide operator token, not per-project authorization. No payload
retrieval, replay, disposition changes, release approval, or schema creation.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode

from .pg_dashboard import ReadOnlyRuns

REASONS = frozenset({'invalid_json', 'invalid_event', 'event_id_collision', 'oversized_event'})
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
FIELDS = ('rejection_id', 'stream_name', 'consumer_name', 'stream_sequence',
          'subject_tag', 'subject_sha256', 'payload_sha256', 'payload_size',
          'reason_code', 'review_state', 'first_seen', 'last_seen',
          'observations', 'max_delivery_count')
COLUMNS = ','.join(FIELDS)
ORDERING = 'rejection-id-desc-not-chronological'


class ReviewUnavailable(RuntimeError):
    """Stored metadata is not safe to interpret; no upstream details exposed."""


def rejection_id(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError('invalid rejection ID')
    return value


def filters(query):
    if not isinstance(query, str) or len(query) > 2048:
        raise ValueError('invalid rejection query')
    params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
    if set(params) - {'reason', 'stream', 'limit', 'after'} or any(len(v) != 1 for v in params.values()):
        raise ValueError('unknown or repeated rejection filter')
    result = {key: values[0] for key, values in params.items() if values[0]}
    if 'reason' in result and result['reason'] not in REASONS:
        raise ValueError('invalid reason')
    if 'stream' in result and not NAME.fullmatch(result['stream']):
        raise ValueError('invalid stream')
    limit = result.get('limit', '50')
    if not limit.isascii() or not limit.isdecimal() or not 1 <= int(limit) <= 100:
        raise ValueError('invalid limit')
    result['limit'] = int(limit)
    if 'after' in result:
        rejection_id(result['after'])
    return result


def safe_row(row):
    """Explicit metadata projection also refuses corrupt/out-of-contract values."""
    if len(row) != len(FIELDS):
        raise ReviewUnavailable('invalid stored rejection')
    item = dict(zip(FIELDS, row))
    if any(not isinstance(item[k], str) or not SHA256.fullmatch(item[k])
           for k in ('rejection_id', 'subject_sha256', 'payload_sha256')):
        raise ReviewUnavailable('invalid stored fingerprint')
    if any(not isinstance(item[k], str) or not NAME.fullmatch(item[k])
           for k in ('stream_name', 'consumer_name')):
        raise ReviewUnavailable('invalid stored broker identity')
    if (item['reason_code'] not in REASONS or item['review_state'] != 'REVIEW_REQUIRED'
            or item['subject_tag'] not in ('qa.jobs.created', 'qa.jobs.finished', 'other')):
        raise ReviewUnavailable('invalid stored classification')
    for key in ('stream_sequence', 'payload_size', 'observations', 'max_delivery_count'):
        minimum = 0 if key == 'payload_size' else 1
        if type(item[key]) is not int or not minimum <= item[key] <= 9223372036854775807:
            raise ReviewUnavailable('invalid stored count')
    for key in ('first_seen', 'last_seen'):
        value = item[key]
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ReviewUnavailable('invalid stored timestamp')
        item[key] = value.isoformat()
    return item


class ReadOnlyRejections(ReadOnlyRuns):
    """Use the existing read-only transaction and three-second statement limit."""

    def list(self, options):
        # The repository revalidates trusted and HTTP callers alike.
        options = filters(urlencode(options))
        clauses, values = [], []
        if options.get('reason'):
            clauses.append('reason_code=%s')
            values.append(options['reason'])
        if options.get('stream'):
            clauses.append('stream_name=%s')
            values.append(options['stream'])
        if options.get('after'):
            clauses.append('rejection_id < %s')
            values.append(options['after'])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        limit = options['limit']
        with self.connection() as db:
            rows = db.execute('SELECT ' + COLUMNS + ' FROM qa_nats_rejections' + where +
                              ' ORDER BY rejection_id DESC LIMIT %s', (*values, limit + 1)).fetchall()
        items = [safe_row(row) for row in rows[:limit]]
        return {'rejections': items, 'page_count': len(items),
                'next_cursor': items[-1]['rejection_id'] if len(rows) > limit else None,
                'ordering': ORDERING, 'authority': 'read-only',
                'scope': 'environment-operator', 'production_accepted': False}

    def detail(self, rid):
        rid = rejection_id(rid)
        with self.connection() as db:
            row = db.execute('SELECT ' + COLUMNS + ' FROM qa_nats_rejections '
                             'WHERE rejection_id=%s', (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return {'rejection': safe_row(row), 'authority': 'read-only',
                'scope': 'environment-operator', 'production_accepted': False,
                'payload_retained': False, 'replay_available': False}


def render(data, options=None):
    esc = lambda value: html.escape(str(value), quote=True)
    page = ['<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>مراجعة رسائل المختبر</title><body><a href="/">التشغيلات</a>',
            '<h1>رسائل تحتاج إلى مراجعة</h1>',
            '<p>عرض تشغيلي للقراءة فقط على مستوى البيئة؛ لا يعرض محتوى الرسائل ولا يعيد إرسالها.</p>']
    if 'rejections' in data:
        options = options or {}
        page += ['<form method="get" action="/rejections"><label>السبب <select name="reason">',
                 '<option value="">الكل</option>']
        for reason in sorted(REASONS):
            selected = ' selected' if options.get('reason') == reason else ''
            page.append(f'<option{selected}>{reason}</option>')
        page += ['</select></label><label>المجرى <input name="stream" value="' +
                 esc(options.get('stream', '')) + '"></label><button>تصفية</button></form>',
                 '<p>ترتيب ثابت بالبصمة، وليس بتاريخ الإنشاء. العدد المعروض يخص الصفحة فقط.</p>',
                 '<table><tr><th>المعرف</th><th>السبب</th><th>المجرى</th><th>المشاهدات</th></tr>']
        for item in data['rejections']:
            rid = rejection_id(item['rejection_id'])
            page.append(f'<tr><td><a href="/rejections/{rid}">{rid}</a></td>' +
                        ''.join('<td>' + esc(item[k]) + '</td>'
                                for k in ('reason_code', 'stream_name', 'observations')) + '</tr>')
        page.append('</table>')
        if not data['rejections']:
            page.append('<p>لا توجد سجلات ضمن هذه الصفحة والمرشحات؛ ليس هذا حكمًا على صحة النظام.</p>')
        if data.get('next_cursor'):
            page.append('<a href="/rejections?' + esc(urlencode({**options, 'after': data['next_cursor']})) + '">التالي</a>')
    else:
        page += ['<a href="/rejections">قائمة المراجعة</a>',
                 '<pre>' + esc(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)) + '</pre>']
    return ''.join(page) + '</body></html>'


def response(queue, target, store=None):
    store = store if store is not None else ReadOnlyRejections(queue)
    as_json = target.path.startswith('/api/')
    if target.path in ('/rejections', '/api/rejections'):
        options = filters(target.query)
        data = store.list(options)
        return data if as_json else render(data, options), as_json
    if target.query:
        raise ValueError('detail query not supported')
    prefix = '/api/rejections/' if as_json else '/rejections/'
    rid = rejection_id(target.path[len(prefix):])
    data = store.detail(rid)
    return data if as_json else render(data), as_json
