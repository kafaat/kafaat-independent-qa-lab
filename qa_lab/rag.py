"""Conservative, local lexical retrieval. No LLM inference is claimed."""
import re
def search(documents,query,limit=5):
    if not isinstance(query,str) or not query.strip():raise ValueError('query required')
    if not 1<=limit<=20:raise ValueError('invalid limit')
    terms=set(re.findall(r'\w+',query.lower()))
    ranked=[]
    for doc in documents:
        if not isinstance(doc,dict) or not all(k in doc for k in ('id','text','source_sha')):continue
        tokens=set(re.findall(r'\w+',str(doc['text']).lower()))
        score=len(terms & tokens)
        if score:ranked.append((score,str(doc['id']),doc))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    return [{'id':doc['id'],'source_sha':doc['source_sha'],'score':score,'excerpt':str(doc['text'])[:500],'status':'REFERENCE_ONLY'} for score,_,doc in ranked[:limit]]
