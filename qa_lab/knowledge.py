"""Source-bound local knowledge store, explicitly not an LLM or embedding model."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from .rag import search

class KnowledgeStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, source_sha TEXT NOT NULL, text TEXT NOT NULL, digest TEXT NOT NULL)")
    def add(self,doc):
        if not isinstance(doc,dict) or set(doc)!={'id','source_sha','text'}:raise ValueError('exact document fields required')
        if not all(isinstance(v,str) and v for v in doc.values()):raise ValueError('invalid document')
        if len(doc['text'].encode())>1024*1024:raise ValueError('document too large')
        digest=hashlib.sha256(doc['text'].encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO documents VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_sha=excluded.source_sha,text=excluded.text,digest=excluded.digest",(doc['id'],doc['source_sha'],doc['text'],digest))
        return {'id':doc['id'],'digest':digest}
    def query(self,text,limit=5):
        with closing(sqlite3.connect(self.path)) as db:
            docs=[{'id':a,'source_sha':b,'text':c} for a,b,c in db.execute('SELECT id,source_sha,text FROM documents')]
        return search(docs,text,limit)
