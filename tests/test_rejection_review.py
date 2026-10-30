"""Behavioural operator-review tests, including real loopback HTTP."""
import copy
import http.client
import json
import os
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from qa_lab.pg_dashboard import Dashboard
from qa_lab.rejection_review import (
    FIELDS, ORDERING, ReadOnlyRejections, ReviewUnavailable,
    filters, rejection_id, render, safe_row,
)

TOKEN = 'local-only-review-test-token-123456'
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def row(rid='a' * 64, reason='invalid_event', stream='QA_JOBS'):
    return (rid, stream, 'qa-audit-inbox', 42, 'qa.jobs.created', 'b' * 64,
            'c' * 64, 32, reason, 'REVIEW_REQUIRED', NOW, NOW, 2, 3)


class DB:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows or [])
        self.calls = []
        self.error = error
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith('SELECT ') and self.error:
            raise self.error
        return self
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class FilterTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(filters(''), {'limit': 50})
    def test_valid_options(self):
        self.assertEqual(filters('reason=invalid_json&stream=QA_JOBS&limit=1&after='+'a'*64),
                         {'reason':'invalid_json','stream':'QA_JOBS','limit':1,'after':'a'*64})
    def test_reject_unknown_and_repeated(self):
        for query in ('project=x', 'reason=invalid_event&reason=invalid_json', 'limit=1&limit=2', 'after=x'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                filters(query)
    def test_reject_nonallowlisted_filters(self):
        for query in ('reason=all', 'stream=%27%20OR%201%3D1', 'stream=a%0Ab', 'stream=%3Cscript%3E'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                filters(query)
    def test_limit_bounds(self):
        for value in ('0', '101', '-1', '1.0', 'true', '%D9%A1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                filters('limit='+value)
    def test_query_bounds(self):
        with self.assertRaises(ValueError):
            filters('stream='+'a'*2049)
        with self.assertRaises(ValueError):
            filters('a=1&b=2&c=3&d=4&e=5')
    def test_id_validation(self):
        self.assertEqual(rejection_id('a'*64),'a'*64)
        for value in ('A'*64, 'a'*63, '../x', 'a'*64+'\n', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rejection_id(value)
    def test_blank_filters_are_optional(self):
        self.assertEqual(filters('reason=&stream=&after=&limit='), {'limit':50})


class RepositoryTests(unittest.TestCase):
    def make(self, rows=None, error=None):
        db = DB(rows, error)
        return ReadOnlyRejections(SimpleNamespace(_connect=lambda:db)), db
    def test_read_only_and_bound_sql(self):
        store, db = self.make([row()])
        result = store.list({'reason':'invalid_event','stream':'QA_JOBS','limit':2,'after':'f'*64})
        self.assertEqual(db.calls[:2], [('SET TRANSACTION READ ONLY',None), ("SET LOCAL statement_timeout = '3000ms'",None)])
        sql, args = db.calls[-1]
        self.assertNotIn('QA_JOBS', sql)
        self.assertNotIn('invalid_event', sql)
        self.assertEqual(args, ('invalid_event','QA_JOBS','f'*64,3))
        self.assertNotIn('SELECT *',sql)
        self.assertEqual(result['page_count'],1)
        self.assertFalse(result['production_accepted'])
    def test_limit_cursor_and_stable_order(self):
        store, db = self.make([row('c'*64),row('b'*64),row('a'*64)])
        result = store.list({'limit':2})
        self.assertEqual(result['next_cursor'],'b'*64)
        self.assertEqual(result['ordering'],ORDERING)
        self.assertEqual(len(result['rejections']),2)
        self.assertIn('ORDER BY rejection_id DESC LIMIT %s',db.calls[-1][0])
    def test_no_cursor_at_last_page(self):
        store, _ = self.make([row()])
        self.assertIsNone(store.list({'limit':2})['next_cursor'])
    def test_empty_is_not_health_claim(self):
        store, _ = self.make()
        result = store.list({})
        self.assertEqual(result['rejections'],[])
        self.assertNotIn('healthy',result)
        self.assertNotIn('total',result)
    def test_missing_table_does_not_become_empty(self):
        store, _ = self.make(error=RuntimeError('relation unavailable'))
        with self.assertRaises(RuntimeError):
            store.list({})
    def test_detail_metadata_only(self):
        store, db = self.make([row()])
        result = store.detail('a'*64)
        self.assertEqual(set(result['rejection']),set(FIELDS))
        self.assertFalse(result['payload_retained'])
        self.assertFalse(result['replay_available'])
        self.assertNotIn('payload',result['rejection'])
        self.assertNotIn('headers',result['rejection'])
        self.assertEqual(db.calls[-1][1],('a'*64,))
    def test_detail_missing_is_not_found(self):
        store, _ = self.make()
        with self.assertRaises(KeyError):
            store.detail('a'*64)
    def test_invalid_id_never_queries_database(self):
        store, db = self.make()
        with self.assertRaises(ValueError):
            store.detail('wrong')
        self.assertEqual(db.calls,[])
    def test_direct_repository_invalid_filters_are_rejected(self):
        store, db = self.make()
        with self.assertRaises(ValueError):
            store.list({'limit':-10})
        self.assertEqual(db.calls,[])
    def test_corrupt_metadata_fails_closed(self):
        for index, value in ((0,'secret'), (1,'<script>'), (8,'unknown'), (9,'APPROVED'), (10,NOW.replace(tzinfo=None)), (12,True)):
            broken=list(row());broken[index]=value
            with self.subTest(index=index), self.assertRaises(ReviewUnavailable):
                safe_row(broken)
    def test_timestamp_and_zero_payload_size(self):
        item=list(row());item[7]=0
        result=safe_row(item)
        self.assertEqual(result['first_seen'],NOW.isoformat())
        self.assertEqual(result['payload_size'],0)


class FakeReviewStore:
    def __init__(self):
        self.calls=[]
        self.error=None
    def list(self, options):
        self.calls.append(('list',options))
        if self.error:
            raise self.error
        return {'rejections':[safe_row(row())], 'page_count':1, 'next_cursor':None,
                'ordering':ORDERING,'authority':'read-only','production_accepted':False}
    def detail(self, rid):
        self.calls.append(('detail',rid))
        if self.error:
            raise self.error
        if rid != 'a'*64:
            raise KeyError(rid)
        return {'rejection':safe_row(row()),'authority':'read-only',
                'production_accepted':False,'payload_retained':False,'replay_available':False}


class ReviewHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=patch.dict(os.environ, {'QA_DASHBOARD_TOKEN':TOKEN})
        cls.env.start()
        cls.store=FakeReviewStore()
        handler=type('ReviewTestHandler',(Dashboard,),{'rejections_store':cls.store,
            'store':SimpleNamespace(list=lambda opts:{'runs':[],'next_cursor':None})})
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join();cls.server.server_close();cls.env.stop()
    def setUp(self):
        self.store.calls.clear();self.store.error=None
    def request(self,path='/api/rejections',token=TOKEN,method='GET',duplicate=False):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.putrequest(method,path)
            if token is not None:
                conn.putheader('Authorization','Bearer '+token)
            if duplicate:
                conn.putheader('Authorization','Bearer '+TOKEN)
            conn.endheaders()
            response=conn.getresponse()
            return response.status,dict(response.getheaders()),response.read().decode()
        finally:
            conn.close()
    def test_unauthenticated_and_wrong_token_rejected(self):
        for token in (None,'wrong'):
            for path in ('/rejections','/api/rejections','/api/rejections/'+'a'*64):
                self.assertEqual(self.request(path,token)[0],401)
        self.assertEqual(self.store.calls,[])
    def test_duplicate_authorization_rejected(self):
        self.assertEqual(self.request(duplicate=True)[0],401)
        self.assertEqual(self.store.calls,[])
    def test_json_list_filters(self):
        status, headers, raw=self.request('/api/rejections?reason=invalid_event&stream=QA_JOBS&limit=3')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['page_count'],1)
        self.assertEqual(self.store.calls,[('list',{'reason':'invalid_event','stream':'QA_JOBS','limit':3})])
        self.assertIn('application/json',headers['Content-Type'])
    def test_html_list(self):
        status,headers,raw=self.request('/rejections')
        self.assertEqual(status,200)
        self.assertIn('text/html',headers['Content-Type'])
        self.assertIn('invalid_event',raw)
        self.assertIn('/rejections/'+'a'*64,raw)
    def test_detail_and_missing(self):
        status,_,raw=self.request('/api/rejections/'+'a'*64)
        self.assertEqual(status,200)
        self.assertFalse(json.loads(raw)['replay_available'])
        self.assertEqual(self.request('/api/rejections/'+'b'*64)[0],404)
    def test_html_detail(self):
        status,_,raw=self.request('/rejections/'+'a'*64)
        self.assertEqual(status,200)
        self.assertIn('REVIEW_REQUIRED',raw)
    def test_invalid_query_and_id(self):
        for path in ('/api/rejections?project=one','/api/rejections?limit=101',
                     '/api/rejections?reason=x','/api/rejections?stream=a&stream=b',
                     '/api/rejections/bad','/api/rejections/'+'a'*64+'?limit=1'):
            self.assertEqual(self.request(path)[0],400,path)
        self.assertEqual(self.store.calls,[])
    def test_missing_table_sanitized_unavailable(self):
        self.store.error=RuntimeError('postgres://user:secret@server / raw payload')
        status,_,raw=self.request()
        self.assertEqual(status,503)
        self.assertEqual(json.loads(raw),{'error':'storage_unavailable'})
        self.assertNotIn('secret',raw)
    def test_headers(self):
        _,headers,_=self.request()
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
    def test_post_never_updates_or_replays(self):
        for path in ('/api/rejections','/api/rejections/'+'a'*64,'/api/rejections/replay'):
            self.assertEqual(self.request(path,method='POST')[0],405)
            self.assertEqual(self.request(path,token=None,method='POST')[0],401)
        self.assertEqual(self.store.calls,[])
    def test_uri_limit(self):
        self.assertEqual(self.request('/api/rejections?stream='+'x'*4096)[0],414)
        self.assertEqual(self.store.calls,[])
    def test_old_run_route_still_works(self):
        status,_,raw=self.request('/')
        self.assertEqual(status,200)
        self.assertIn('href="/rejections"',raw)
    def test_raw_payload_never_in_response(self):
        _,_,raw=self.request('/api/rejections/'+'a'*64)
        item=json.loads(raw)['rejection']
        self.assertEqual(set(item),set(FIELDS))
        for key in ('payload','data','headers','exception','token'):
            self.assertNotIn(key,item)


class RenderingTests(unittest.TestCase):
    def test_html_escapes_untrusted_values(self):
        item=safe_row(row());item['stream_name']='<script>alert(1)</script>'
        page=render({'rejections':[item]}, {'stream':'"><svg onload=alert(1)>'})
        self.assertNotIn('<script>',page)
        self.assertNotIn('<svg',page)
        self.assertIn('&lt;script&gt;',page)
    def test_empty_page_not_global_success(self):
        page=render({'rejections':[]})
        self.assertIn('ليس هذا حكمًا',page)
    def test_next_cursor_preserves_filters(self):
        page=render({'rejections':[safe_row(row())],'next_cursor':'a'*64}, {'reason':'invalid_json','limit':1})
        self.assertIn('reason=invalid_json',page)
        self.assertIn('after='+'a'*64,page)
        self.assertIn('&amp;',page)


if __name__ == '__main__':
    unittest.main()
