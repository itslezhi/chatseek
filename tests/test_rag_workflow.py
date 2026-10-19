import contextlib
import datetime as dt
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from zoneinfo import ZoneInfo

import wechat_rag as rag
import wechat_vectors as vectors
import wechat_web as web
from wechat_context import ContextLimitError,OutputLimitError,context_error,direct_fits
from test_vectors import Encoder

NOW=dt.datetime(2026,10,19,12,tzinfo=ZoneInfo('Asia/Shanghai'))

def model_plan(intent='search',queries=None):
    return json.dumps({'intent':intent,'queries':queries or ['他有跳槽打算吗'],
                       'time':{'mode':'all'},'message_ids':[]})

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.private=Path(self.tmp.name);self.path=self.private/'chat.sqlite3'
        self.store=rag.Store(self.path)
        with self.store.db:
            self.store.upsert([self.row(1,'准备投简历',days=-10),self.row(2,'聚餐在东门',days=0)])
        self.target={'db_root':self.tmp.name,'chat_id':'peer'}
        for mock in [patch.object(rag,'PRIVATE',self.private),patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'test-placeholder-key'}),
                     patch.object(rag,'local_now',return_value=NOW),patch.object(vectors,'get_encoder',return_value=Encoder())]:
            mock.start();self.addCleanup(mock.stop)
    def row(self,i,text,days=0):
        return {'database':'fixture.db','local_id':i,'server_id':i,'create_time':int(NOW.timestamp())+days*86400+i,
                'sender_id':'peer','local_type':1,'text':text}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_default_ask_runs_real_planner_and_pure_vector_path(self):
        def complete(config,system,payload,**options):
            if options.get('json_mode'):return model_plan()
            self.assertIn('准备投简历',payload['evidence'][0]['text'])
            if options.get('on_delta'):options['on_delta']('他准备投简历。[M1]')
            return '他准备投简历。[M1]'
        events=[]
        with patch.object(rag,'model_completion',side_effect=complete),patch.object(self.store,'retrieve',side_effect=AssertionError('keyword accessed')):
            result=rag.ask(self.store,'他有跳槽打算吗',on_event=events.append)
        self.assertEqual(result['query_context']['retrieval_mode'],'vector')
        self.assertEqual([ref['id'] for ref in result['sources']],['M1'])
        self.assertEqual(result['citation_check'],'passed')
        self.assertIn('delta',[event['type'] for event in events])
    def test_large_summary_under_context_budget_is_one_complete_call(self):
        with self.store.db:self.store.upsert([self.row(i,'今天讨论内容'+str(i)) for i in range(3,220)])
        calls=[]
        def complete(config,system,payload,**options):
            if options.get('json_mode'):return model_plan('summary',['总结所有历史'])
            calls.append(payload);return '讨论了工作和聚餐。[M1][M2]'
        with patch.object(rag,'model_completion',side_effect=complete),patch.object(rag,'direct_fits',return_value=True):
            result=rag.ask(self.store,'总结所有历史')
        self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]['evidence']),219)
        self.assertEqual(result['query_context']['summary_processing']['mode'],'direct')
    def test_provider_context_error_falls_back_to_all_batches_only(self):
        with self.store.db:self.store.upsert([self.row(i,'今天讨论内容'+str(i)) for i in range(3,220)])
        attempts=[];read=[]
        def complete(config,system,payload,**options):
            if options.get('json_mode'):return model_plan('summary',['总结所有历史'])
            attempts.append(payload)
            if len(attempts)==1:raise ContextLimitError('context limit')
            refs=payload['evidence']
            if 'id' in refs[0]:
                read.extend(ref['id'] for ref in refs);return '本批内容。['+refs[0]['id']+']'
            return '各批讨论了工作与聚餐。[M1][M2]'
        events=[]
        with patch.object(rag,'model_completion',side_effect=complete),patch.object(rag,'direct_fits',return_value=True):
            result=rag.ask(self.store,'总结所有历史',on_event=events.append)
        self.assertEqual(set(read),{'M'+str(i) for i in range(1,220)})
        self.assertEqual(result['query_context']['summary_processing']['mode'],'batched')
        self.assertIn('reset',[event['type'] for event in events])
    def test_network_and_output_errors_do_not_trigger_input_batching(self):
        for error in (RuntimeError('network timeout'),OutputLimitError('output truncated')):
            calls=[]
            def complete(config,system,payload,**options):
                if options.get('json_mode'):return model_plan('summary',['总结所有历史'])
                calls.append(payload);raise error
            with self.subTest(error=error),patch.object(rag,'model_completion',side_effect=complete), \
                 patch.object(rag,'direct_fits',return_value=True),self.assertRaises(type(error)):
                rag.ask(self.store,'总结所有历史')
            self.assertEqual(len(calls),1)
    def test_http_context_error_is_typed_without_leaking_raw_response(self):
        exc=urllib.error.HTTPError('https://api.deepseek.com',400,'bad',{},io.BytesIO(b'{"error":{"code":"context_length_exceeded","message":"private"}}'))
        with patch.object(rag.urllib.request,'urlopen',side_effect=exc),self.assertRaises(ContextLimitError) as caught:
            rag.model_completion({'DEEPSEEK_API_KEY':'test-placeholder-key'},'system',{})
        self.assertNotIn('private',str(caught.exception))
        self.assertFalse(context_error(401,'context_length_exceeded'));self.assertFalse(context_error(400,'insufficient balance'))
    def test_empty_semantic_results_do_not_use_keyword_fallback(self):
        with patch.object(rag,'model_completion',return_value=model_plan(queries=['恐龙化石'])), \
             patch.object(self.store,'retrieve',side_effect=AssertionError('keyword fallback')):
            result=rag.ask(self.store,'恐龙化石')
        self.assertEqual(result['sources'],[])
    def test_invalid_retrieval_mode_is_rejected_before_cloud_calls(self):
        with patch.object(rag,'model_completion') as model,self.assertRaises(ValueError):
            rag.ask(self.store,'问题',retrieval_mode='keyword')
        model.assert_not_called()
    def test_streaming_http_default_persists_semantic_answer(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def complete(config,system,payload,**options):
            if options.get('json_mode'):return model_plan()
            options['on_delta']('他准备');options['on_delta']('投简历。[M1]');return '他准备投简历。[M1]'
        try:
            with patch.object(rag,'runtime_lock',contextlib.nullcontext),patch.object(rag,'load_runtime',return_value=(self.target,None)), \
                 patch.object(rag,'store_path',return_value=self.path),patch.object(rag,'model_completion',side_effect=complete):
                data={'context':web.context_id(self.target),'question':'他有跳槽打算吗','stream':True,'access_approved':True}
                connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
                connection.request('POST','/api/ask',json.dumps(data),{'X-WeChat-RAG':'1'})
                response=connection.getresponse();events=[json.loads(line) for line in response if line.strip()];connection.close()
            self.assertEqual(events[-1]['type'],'done')
            self.assertEqual(events[-1]['result']['query_context']['retrieval_mode'],'vector')
            self.assertEqual(sum(event['type']=='delta' for event in events),2)
            from wechat_dialog import DialogMemory
            memory=DialogMemory(self.store)
            self.assertEqual(memory.recent(memory.session())[0]['answer'],'他准备投简历。[M1]')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
