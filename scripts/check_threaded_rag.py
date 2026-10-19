#!/usr/bin/env python3
"""Regression for macOS native crashes: real BGE + FAISS + threaded HTTP.

Uses synthetic records and stubs only cloud generation. No private chats/API
calls. Exercises a cold model start on an HTTP request thread, simultaneous
vector calls, and recovery after this test's own model child is killed.
"""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wechat_rag as rag
import wechat_vectors as vectors
import wechat_web as web
import wechat_embedding_client as client


def main():
    model_cache=rag.PRIVATE
    report={'synthetic_only':True,'cold_http_successes':0,'parallel_vector_successes':0,'recovery_http_successes':0}
    with tempfile.TemporaryDirectory(prefix='chatseek-native-http-') as temporary:
        path=Path(temporary)/'chat.sqlite3';target={'db_root':temporary,'chat_id':'fixture'}
        encoder=vectors.LocalEncoder(model_cache)
        def model(config,system,payload,**options):
            if options.get('json_mode'):
                return json.dumps({'intent':'search','queries':['他有跳槽打算吗'],'time':{'mode':'all'},'message_ids':[]})
            answer='他准备投简历，有换工作的打算。[M1]'
            if options.get('on_delta'):options['on_delta'](answer)
            return answer
        with contextlib.ExitStack() as stack:
            for mock in [patch.object(rag,'PRIVATE',Path(temporary)),patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'test-placeholder-key'}),
                         patch.object(rag,'load_runtime',return_value=(target,None)),patch.object(rag,'runtime_lock',contextlib.nullcontext),
                         patch.object(rag,'store_path',return_value=path),patch.object(rag,'model_completion',side_effect=model),
                         patch.object(vectors,'get_encoder',return_value=encoder),patch.object(vectors,'request_index')]:stack.enter_context(mock)
            store=rag.Store(path)
            with store.db:
                store.set_meta('peer_id','fixture')
                store.upsert([{'database':'synthetic.db','local_id':i,'server_id':i,'create_time':1700000000+i*86400,
                               'sender_id':'fixture','local_type':1,'text':text} for i,text in enumerate([
                    '这家公司待不下去了，准备投简历。','聚餐地点是东门，晚上六点。','明年打算去海边旅游。'],1)])
            vectors.ensure(store,Path(temporary));store.close()
            client.close_worker()  # Critical: cold model load must occur on HTTP thread.
            server=ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            port=server.server_address[1]
            def ask():
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=180)
                data={'context':web.context_id(target),'question':'他有没有跳槽打算','stream':True,'access_approved':True}
                connection.request('POST','/api/ask',json.dumps(data),{'X-WeChat-RAG':'1'})
                response=connection.getresponse();events=[json.loads(line) for line in response if line.strip()];connection.close()
                assert response.status==200 and events[-1]['type']=='done',events[-1]['type']
                assert events[-1]['result']['citation_check']=='passed'
            try:
                for _ in range(6):ask();report['cold_http_successes']+=1
                assert 'torch' not in sys.modules,'HTTP process unexpectedly imported PyTorch'
                def vector_call(_):
                    store=rag.Store(path)
                    try:
                        rows,_=vectors.retrieve(store,'有跳槽打算吗',Path(temporary))
                        assert 1 in [row['id'] for row in rows]
                        return True
                    finally:store.close()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    report['parallel_vector_successes']=sum(pool.map(vector_call,range(12)))
                first_pid=client._process.pid
                client._process.kill();client._process.wait(timeout=5)
                for _ in range(2):ask();report['recovery_http_successes']+=1
                assert client._process.pid!=first_pid
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                connection.request('GET','/api/state');response=connection.getresponse();response.read();connection.close()
                assert response.status==200
                report['health_after_worker_restart']=response.status
                report['torch_in_http_process']='torch' in sys.modules
            finally:server.shutdown();server.server_close();thread.join();client.close_worker()
    output=ROOT/'.private/threaded-rag-check.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2));output.chmod(0o600)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
