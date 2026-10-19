from legacy_support import isolate
import contextlib
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import wechat_rag as rag
import wechat_web as web
from wechat_dialog import DialogMemory


def sse(text=None, *, finish=None):
    choice = {'index':0, 'delta':{} if text is None else {'content':text}, 'finish_reason':finish}
    return ('data: '+json.dumps({'choices':[choice]},ensure_ascii=False)+'\n\n').encode()


class ProviderStreamTests(unittest.TestCase):
    def test_real_deltas_are_forwarded_before_provider_finishes(self):
        seen=[]
        class Response:
            def __iter__(self):
                yield b': keepalive\r\n'
                yield b'\r\n'
                yield from sse('你好').splitlines(keepends=True)
                self_test.assertEqual(seen,['你好'])
                yield from sse('。[M1]').splitlines(keepends=True)
                yield from sse(finish='stop').splitlines(keepends=True)
                yield b'data: [DONE]\r\n';yield b'\r\n'
        self_test=self
        self.assertEqual(rag.read_model_stream(Response(),seen.append),'你好。[M1]')
        self.assertEqual(seen,['你好','。[M1]'])

    def test_empty_malformed_error_truncated_and_unfinished_streams_fail(self):
        for body in (b'',sse('partial'),sse('partial')+sse(finish='stop'),
                     b'data: invalid\n\n',b'data: {"error":{"message":"never expose this"}}\n\n',
                     sse('partial')+sse(finish='length')+b'data: [DONE]\n\n',
                     b'data: [DONE]\n\n',b'data: \xff\n\n'):
            with self.subTest(body=body[:30]), self.assertRaises(RuntimeError) as raised:
                rag.read_model_stream(io.BytesIO(body),lambda _:None)
            self.assertNotIn('never expose',str(raised.exception))

    def test_model_request_enables_provider_streaming_but_json_mode_stays_normal(self):
        config={'DEEPSEEK_API_KEY':'test-placeholder-key'}
        body=sse('完成。[M1]')+sse(finish='stop')+b'data: [DONE]\n\n'
        seen=[]
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(body)) as request:
            self.assertEqual(rag.model_completion(config,'instructions',{},on_delta=seen.append),'完成。[M1]')
        self.assertTrue(json.loads(request.call_args.args[0].data)['stream'])
        self.assertEqual(seen,['完成。[M1]'])
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')) as request:
            rag.model_completion(config,'instructions',{},json_mode=True,on_delta=seen.append)
        self.assertFalse(json.loads(request.call_args.args[0].data)['stream'])


class StreamingHTTPTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'chat.sqlite3'
        self.target={'db_root':self.tmp.name,'chat_id':'peer','display_name':'测试会话'}
        self.patches=[patch.object(rag,'PRIVATE',Path(self.tmp.name)),
                      patch.object(rag,'runtime_lock',contextlib.nullcontext),
                      patch.object(rag,'load_runtime',return_value=(self.target,None)),
                      patch.object(rag,'store_path',return_value=self.path),
                      patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'test-placeholder-key'})]
        for item in self.patches:item.start()
        store=rag.Store(self.path)
        with store.db:
            store.set_meta('peer_id','peer')
            store.upsert([{'database':'test.db','local_id':1,'server_id':1,'create_time':1700000000,
                           'sender_id':'peer','local_type':1,'text':'周六六点聚餐'}])
        self.session=DialogMemory(store).session();store.close()
        self.data={'context':web.context_id(self.target),'session':self.session,'question':'聚餐时间',
                   'stream':True,'access_approved':True}
        self.server=ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.connection=None

    def tearDown(self):
        if self.connection:self.connection.close()
        self.server.shutdown();self.server.server_close();self.thread.join()
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def start_request(self, data=None, extra_headers=None):
        self.connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
        self.connection.request('POST','/api/ask',json.dumps(self.data if data is None else data),
                                {'X-WeChat-RAG':'1',**(extra_headers or {})})
        return self.connection.getresponse()

    def saved(self):
        store=rag.Store(self.path)
        try:return DialogMemory(store).recent(self.session)
        finally:store.close()

    def test_incremental_http_delivery_then_checked_answer_is_persisted(self):
        release=threading.Event()
        class DelayedResponse:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def __iter__(self):
                yield from sse('聚餐定在').splitlines(keepends=True)
                if not release.wait(3):raise RuntimeError('test wait expired')
                yield from sse('周六六点。[M1]').splitlines(keepends=True)
                yield from sse(finish='stop').splitlines(keepends=True)
                yield b'data: [DONE]\n';yield b'\n'
        with patch.object(rag.urllib.request,'urlopen',return_value=DelayedResponse()):
            response=self.start_request()
            self.assertEqual(response.status,200)
            self.assertIn('application/x-ndjson',response.getheader('Content-Type'))
            events=[]
            try:
                while not events or events[-1]['type']!='delta':events.append(json.loads(response.readline()))
                self.assertEqual(events[-1]['text'],'聚餐定在')
                self.assertFalse(release.is_set());self.assertEqual(self.saved(),[])
            finally:release.set()
            events.extend(json.loads(line) for line in response if line.strip())
        final=events[-1]
        self.assertEqual(final['type'],'done')
        self.assertEqual(final['result']['answer'],'聚餐定在周六六点。[M1]')
        self.assertEqual(final['result']['citation_check'],'passed')
        self.assertEqual(final['result']['sources'][0]['id'],'M1')
        self.assertIsInstance(final['result']['turn_id'],int)
        self.assertEqual(self.saved()[0]['answer'],final['result']['answer'])

    def test_provider_interruption_emits_error_and_never_saves_partial(self):
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(sse('尚未完成'))):
            events=[json.loads(line) for line in self.start_request() if line.strip()]
        self.assertIn('delta',[event['type'] for event in events])
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.saved(),[])

    def test_invalid_citations_replace_streamed_text_with_checked_notice(self):
        body=sse('错误引用。[M9999]')+sse(finish='stop')+b'data: [DONE]\n\n'
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(body)):
            events=[json.loads(line) for line in self.start_request() if line.strip()]
        result=events[-1]['result']
        self.assertEqual(result['citation_check'],'failed')
        self.assertNotIn('[M9999]',result['answer'])
        self.assertEqual(self.saved()[0]['answer'],result['answer'])

    def test_stream_is_subject_to_same_permission_and_origin_guards(self):
        for data,headers,expected in (({**self.data,'access_approved':False},{},409),
                                     (self.data,{'Origin':'https://attacker.example'},403)):
            with patch.object(web,'query') as query:
                response=self.start_request(data,headers)
                self.assertEqual(response.status,expected);response.read();query.assert_not_called()
            self.connection.close()

    def test_no_matching_records_complete_without_provider_call(self):
        with patch.object(rag.urllib.request,'urlopen') as model:
            events=[json.loads(line) for line in self.start_request({**self.data,'question':'M9999'}) if line.strip()]
        model.assert_not_called()
        self.assertEqual(events[-1]['type'],'done')
        self.assertEqual(events[-1]['result']['sources'],[])
        self.assertEqual(len(self.saved()),1)

    def test_client_disconnect_cancels_before_history_append(self):
        started=threading.Event();release=threading.Event();stopped=threading.Event()
        def ask(*_,on_event,**__):
            started.set()
            try:
                release.wait(3)
                on_event({'type':'delta','text':'temporary'})
                return {'answer':'should not save','sources':[]}
            finally:stopped.set()
        with patch.object(rag,'ask',side_effect=ask):
            response=self.start_request()
            self.assertEqual(json.loads(response.readline())['type'],'status')
            self.assertTrue(started.wait(2))
            response.close();self.connection.close();release.set()
            self.assertTrue(stopped.wait(2))
        self.assertEqual(self.saved(),[])

    def test_management_change_during_stream_rejects_stale_answer(self):
        def ask(*_,on_event,**__):
            on_event({'type':'delta','text':'临时回答。[M1]'})
            store=rag.Store(self.path)
            try:DialogMemory(store).new(self.session)
            finally:store.close()
            return {'answer':'临时回答。[M1]','sources':[]}
        with patch.object(rag,'ask',side_effect=ask):
            events=[json.loads(line) for line in self.start_request() if line.strip()]
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.saved(),[])

    def test_summary_progress_does_not_stream_intermediate_reading_notes(self):
        store=rag.Store(self.path)
        with store.db:
            store.upsert([{'database':'test.db','local_id':i,'server_id':i,'create_time':1700000000+i,
                           'sender_id':'peer','local_type':1,'text':'聊天内容'+str(i)} for i in range(2,133)])
        events=[]
        def complete(config,system,payload,**options):
            if 'on_delta' in options:
                options['on_delta']('最终汇总。[M1]')
                return '最终汇总。[M1]'
            return '内部阅读笔记。['+payload['evidence'][0]['id']+']'
        try:
            with patch.object(rag,'model_completion',side_effect=complete):
                result=rag.ask(store,'总结全部聊天',on_event=events.append)
        finally:store.close()
        self.assertEqual([event['text'] for event in events if event['type']=='delta'],['最终汇总。[M1]'])
        self.assertTrue(any('第 2/2 批' in event.get('message','') for event in events))
        self.assertEqual(result['query_context']['summary_processing']['model_read_messages'],132)

    def test_streamed_followup_searches_history_beyond_previous_today_scope(self):
        store=rag.Store(self.path)
        with store.db:
            store.upsert([{'database':'test.db','local_id':2,'server_id':2,'create_time':int(rag.local_now().timestamp()),
                           'sender_id':'peer','local_type':1,'text':'今天完成了文章'}])
        DialogMemory(store).append(self.session,0,'今天聊了什么',{
            'answer':'今天完成文章。[M2]','sources':[{'id':'M2'}],
            'query_context':{'date_range':rag.question_time_range('今天')}})
        store.close()
        def provider(request,**_):
            if json.loads(request.data)['stream']:
                return io.BytesIO(sse('历史聚餐定在六点。[M1]')+sse(finish='stop')+b'data: [DONE]\n\n')
            rewrite=json.dumps({'question':'今天聚餐时间 M2'},ensure_ascii=False)
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':rewrite}}]},ensure_ascii=False).encode())
        with patch.object(rag.urllib.request,'urlopen',side_effect=provider):
            events=[json.loads(line) for line in self.start_request({**self.data,'question':'查找所有历史聊天记录，不是今天'}) if line.strip()]
        self.assertEqual(events[-1]['type'],'done')
        result=events[-1]['result']
        self.assertIsNone(result['query_context']['date_range'])
        self.assertEqual(result['query_context']['time_scope'],'all_history')
        self.assertIn('M1',[source['id'] for source in result['sources']])
        self.assertEqual(self.saved()[-1]['query_context']['time_scope'],'all_history')


if __name__=='__main__':unittest.main()
