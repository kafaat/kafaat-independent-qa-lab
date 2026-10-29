"""Evidence-based parity matrix; absent evidence is NOT_RUN, never PASS."""
import json
from pathlib import Path

STATES={'VERIFIED','PARTIAL','FAILED','NOT_RUN'}
def evaluate(cases,receipts):
    results=[]
    for case in cases:
        if not isinstance(case,dict) or not all(k in case for k in ('id','legacy_ref','new_ref')):
            raise ValueError('invalid parity case')
        old=receipts.get((case['id'],'legacy'))
        new=receipts.get((case['id'],'new'))
        if not old or not new:state='NOT_RUN'
        elif old.get('input_digest')!=new.get('input_digest'):state='FAILED'
        elif old.get('status')!='PASSED' or new.get('status')!='PASSED':state='FAILED'
        elif old.get('output_digest')==new.get('output_digest'):state='VERIFIED'
        else:state='PARTIAL'
        results.append({'id':case['id'],'status':state,'legacy_ref':case['legacy_ref'],'new_ref':case['new_ref']})
    return results
def summary(rows):
    return {s:sum(row['status']==s for row in rows) for s in sorted(STATES)}
