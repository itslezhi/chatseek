from legacy_support import isolate
import http.client
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import wechat_web as web


class WebTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.private = tempfile.TemporaryDirectory()
        self.private_patch = patch.object(web.rag,'PRIVATE',Path(self.private.name))
        self.private_patch.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.private_patch.stop()
        self.private.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        body = None if data is None else json.dumps(data)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        status, payload = response.status, response.read()
        connection.close()
        return status, payload

    def test_only_allowlisted_public_assets_are_served(self):
        self.assertEqual(self.request('GET', '/')[0], 200)
        status, mascot = self.request('GET', '/assets/chatseek-pet.png')
        self.assertEqual(status, 200)
        self.assertTrue(mascot.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue(self.request('GET', '/assets/chatseek-pet-body.png')[1].startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(self.request('GET', '/pet.js')[0], 200)
        self.assertEqual(self.request('GET', '/assets/chatseek-mark.svg')[0], 200)
        for path in ('/.env', '/.private/target.json', '/web/../.env', '/wechat_rag.py'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/assets/../../.env')[0], 404)

    def test_external_origin_and_rebound_host_cannot_access_data(self):
        with patch.object(web, 'query') as query:
            for headers in ({'Host': 'attacker.example'}, {'Origin': 'https://attacker.example'}, {'Sec-Fetch-Site': 'cross-site'}):
                self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 403)
            self.assertEqual(self.request('POST', '/api/ask', {'question': 'private'})[0], 403)
            query.assert_not_called()

    def test_select_missing_contact_response_and_invalid_payload(self):
        headers = {'X-WeChat-RAG': '1', 'Content-Type': 'application/json'}
        with patch.object(web.rag, 'select_contact', return_value={'found': False, 'selected': False, 'message': '没有此联系人'}):
            status, body = self.request('POST', '/api/select', {'name': 'unknown','access_approved':True}, headers)
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)['selected'])
        self.assertEqual(self.request('POST', '/api/select', {'name': 123,'access_approved':True}, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/history', [], headers)[0], 400)

    def test_contact_change_rejected_before_model_or_index_access(self):
        import contextlib
        target = {'db_root': '/example/account', 'chat_id': 'new-peer'}
        with patch.object(web.rag, 'runtime_lock', contextlib.nullcontext), \
             patch.object(web.rag, 'load_runtime', return_value=(target, None)), \
             patch.object(web.rag, 'Store') as store, patch.object(web.rag, 'ask') as ask:
            with self.assertRaises(ValueError): web.query('ask', {'context': 'stale', 'question': 'hello'})
            store.assert_not_called()
            ask.assert_not_called()

    def test_question_uses_selected_index_and_returns_cited_model_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            target = {'db_root': folder, 'chat_id': 'test-peer', 'display_name': '测试好友'}
            store = web.rag.Store(Path(folder)/'chat.sqlite3')
            with store.db:
                store.upsert([{'database': 'message_0.db', 'local_id': 1, 'server_id': 10,
                               'create_time': 1700000000, 'sender_id': 'test-peer',
                               'local_type': 1, 'text': '周六六点聚餐'}])
                store.set_meta('peer_id', 'test-peer')
                store.set_meta('display_name', '测试好友')
            store.close()
            response = json.dumps({'choices': [{'message': {'content': '聚餐定在周六六点。[M1]'}}]}).encode()
            with patch.object(web.rag, 'PRIVATE', Path(folder)), \
                 patch.object(web.rag, 'load_runtime', return_value=(target, None)), \
                 patch.object(web.rag, 'env_config', return_value={'DEEPSEEK_API_KEY': 'test-placeholder-key'}), \
                 patch.object(web.rag.urllib.request, 'urlopen', return_value=io.BytesIO(response)) as model:
                status, body = self.request('POST', '/api/ask',
                    {'context': web.context_id(target), 'question': '聚餐时间','access_approved':True}, {'X-WeChat-RAG': '1'})
            result = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(result['citation_check'], 'passed')
            self.assertEqual(result['sources'][0]['text'], '周六六点聚餐')
            self.assertNotIn('test-placeholder-key', body.decode())
            sent = json.loads(model.call_args.args[0].data)
            self.assertIn('聚餐时间', sent['messages'][1]['content'])

    def test_ask_mode_stops_access_before_data_or_model_work(self):
        headers={'X-WeChat-RAG':'1'}
        with patch.object(web,'query') as query,patch.object(web.rag,'select_contact') as select:
            for action,data in [('ask',{'question':'今天聊了什么'}),('sync',{}),('select',{'name':'x'}),('rag-index',{}),
                                ('media-preview',{'id':'M1'}),('media-recognize',{'id':'M1'}),('watch',{'enabled':True})]:
                with self.subTest(action=action):
                    status,body=self.request('POST','/api/'+action,data,headers)
                    self.assertEqual(status,409)
                    self.assertTrue(json.loads(body)['access_confirmation_required'])
            query.assert_not_called();select.assert_not_called()

    def test_access_preference_persists_and_full_mode_skips_app_confirmation(self):
        headers={'X-WeChat-RAG':'1'}
        self.assertEqual(json.loads(self.request('GET','/api/access')[1])['mode'],'ask')
        status,body=self.request('POST','/api/access',{'mode':'full'},headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{'mode':'full','system_permission':'manual'})
        config=web.rag.PRIVATE/'web-access.json'
        self.assertEqual(config.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(self.request('GET','/api/access')[1])['mode'],'full')
        with patch.object(web,'query',return_value={'answer':'test'}) as query:
            self.assertEqual(self.request('POST','/api/ask',{'question':'test'},headers)[0],200)
            query.assert_called_once()
        self.request('POST','/api/access',{'mode':'ask'},headers)
        self.assertEqual(self.request('POST','/api/ask',{'question':'test'},headers)[0],409)

    def test_broken_preference_defaults_to_ask_and_untrusted_origin_cannot_change_it(self):
        (web.rag.PRIVATE/'web-access.json').write_text('invalid json')
        self.assertEqual(web.access_settings()['mode'],'ask')
        self.assertEqual(self.request('POST','/api/access',{'mode':'full'},
                         {'X-WeChat-RAG':'1','Origin':'https://attacker.example'})[0],403)
        self.assertEqual(web.access_settings()['mode'],'ask')
        self.assertEqual(self.request('POST','/api/access',{'mode':'unknown'},{'X-WeChat-RAG':'1'})[0],400)

    def test_settings_button_opens_only_the_system_pane(self):
        with patch.object(web.sys,'platform','darwin'),patch.object(web.subprocess,'run') as run:
            status,body=self.request('POST','/api/access-settings',{}, {'X-WeChat-RAG':'1'})
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['opened'])
        self.assertEqual(run.call_args.args[0],['/usr/bin/open','x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'])
        self.assertEqual(web.access_settings(),{'mode':'ask','system_permission':'manual'})


if __name__ == '__main__': unittest.main()
