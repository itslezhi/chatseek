#!/usr/bin/env python3
"""Compare real BGE, independent keyword retrieval and RRF on the same fixtures.

No private chats or cloud model calls are used. Recall is measured at the same
8-result budget; scores describe this small synthetic set, not general accuracy.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wechat_rag as rag
import wechat_vectors as vectors


def main():
    cases=json.loads((ROOT/'tests/fixtures/retrieval_cases.json').read_text())
    results=[]
    with tempfile.TemporaryDirectory(prefix='chatseek-evaluation-') as temporary:
        store=rag.Store(Path(temporary)/'chat.sqlite3')
        try:
            with store.db:
                store.upsert([{'database':'synthetic.db','local_id':i,'server_id':i,'create_time':1700000000+i*7200,
                               'sender_id':'fixture','local_type':1,'text':case['text']} for i,case in enumerate(cases,1)])
            vectors.ensure(store,rag.PRIVATE)
            for i,case in enumerate(cases,1):
                entry={'question':case['question'],'type':case['type'],'expected_id':i}
                for mode in ('keyword','vector','hybrid'):
                    start=time.monotonic()
                    if mode=='keyword':rows=store.keyword_hits(case['question'],limit=8,time_range=None)
                    else:rows,_=vectors.retrieve(store,case['question'],rag.PRIVATE,mode=mode,limit=8)
                    ids=[row['id'] for row in rows]
                    entry[mode]={'hit':i in ids,'source_ids':ids,'latency_ms':round((time.monotonic()-start)*1000,1)}
                results.append(entry)
        finally:store.close()
    summary={}
    for group in ('all','semantic','exact'):
        subset=[case for case in results if group=='all' or case['type']==group]
        summary[group]={'questions':len(subset),**{mode:round(sum(case[mode]['hit'] for case in subset)/len(subset),4) for mode in ('keyword','vector','hybrid')}}
    report={'fixture':'synthetic original texts; no private chats','embedding_model':vectors.MODEL,'metric':'Recall@8',
            'summary':summary,'cases':results,'limits':'Small synthetic set; measures retrieval only, not factual correctness of generated answers.'}
    rag.PRIVATE.mkdir(mode=0o700,exist_ok=True)
    target=rag.PRIVATE/'retrieval-evaluation.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2));target.chmod(0o600)
    print(json.dumps(summary,ensure_ascii=False));print('Report:',target)

if __name__=='__main__':main()
