"""Isolate existing source/date/media tests from expensive local models.

The upgraded planner and semantic retriever have separate end-to-end tests.
These fixtures preserve the old test inputs to exercise unchanged downstream
citation, history, media, concurrency and stream behavior.
"""
from unittest.mock import patch
import wechat_rag as rag
from wechat_summary import batches


def old_plan(question,history,config,now):
    resolved=rag.resolve_followup(question,history,config,now)
    unrestricted=rag.all_history_scope(question) or (rag.bare_history_request(question) and rag.question_time_range(question,now) is None)
    period=None if unrestricted else rag.question_time_range(question,now) or rag.question_time_range(resolved,now)
    explicit=bool(rag.message_references(question))
    query=question if rag.summary_question(question,period) and not rag.bare_history_request(question) and not explicit else resolved
    if unrestricted and not explicit:
        import re
        query=re.sub(r'(?<![A-Za-z0-9_])M[1-9]\d*(?![A-Za-z0-9_])','',query,flags=re.I)
    ids=rag.message_references(query)
    summary=rag.summary_question(query,period) and not ids
    if summary and period is None and '最近' in query and not unrestricted:period=rag.question_time_range('最近7天',now)
    return {'intent':'message' if ids else 'summary' if summary else 'search','queries':[query],
            'period':period,'message_ids':ids,'api_called':bool(history)}


def isolate(test):
    def retrieve(store,question,*,mode,period,on_progress):
        rows=store.retrieve(question,time_range=period)
        return rows,{'mode':mode,'eligible_chunks':len(rows),'selected_chunks':len(rows),'vector_candidates':len(rows),'keyword_candidates':0}
    mocks=[patch.object(rag,'plan_question',side_effect=old_plan),patch.object(rag,'semantic_retrieve',side_effect=retrieve),
           patch.object(rag,'direct_fits',side_effect=lambda config,system,payload:len(list(batches(payload['evidence'])))<=1),
           patch('wechat_vectors.request_index')]
    for mock in mocks:mock.start();test.addCleanup(mock.stop)
