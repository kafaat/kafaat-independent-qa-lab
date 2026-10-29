"""Deterministic retrieval evaluation with explicit denominators."""
def evaluate(cases,retrieve):
    if not cases:raise ValueError('evaluation cases required')
    results=[]
    for case in cases:
        if not all(k in case for k in ('id','query','expected_ids')):raise ValueError('invalid evaluation case')
        expected=set(case['expected_ids'])
        if not expected:raise ValueError('expected IDs required')
        hits=retrieve(case['query'])
        found={hit['id'] for hit in hits}
        correct=len(expected & found)
        results.append({'id':case['id'],'recall_at_k':correct/len(expected),'hit':correct>0,'expected_count':len(expected),'returned_count':len(hits)})
    return {'cases':len(results),'hit_rate':sum(x['hit'] for x in results)/len(results),'mean_recall_at_k':sum(x['recall_at_k'] for x in results)/len(results),'results':results}
