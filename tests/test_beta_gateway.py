import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from beta_gateway.server import Ledger,Denied,Handler,validated_body
from chatseek_beta_client import activate,apply_beta_config,gateway_url,settings


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.ledger=Ledger(Path(self.temp.name)/'ledger.sqlite3')
    def tearDown(self):self.temp.cleanup()
    def invite(self,**kwargs):return self.ledger.issue('test',**kwargs)
    def test_device_binding_expiry_and_revocation(self):
        code=self.invite(days=7)
        first=self.ledger.activate(code,'a'*32)
        with self.ledger.connect() as db:
            expiry=db.execute('SELECT expires_at FROM invites').fetchone()[0]
        second=self.ledger.activate(code,'a'*32)
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT expires_at FROM invites').fetchone()[0],expiry)
        with self.assertRaises(Denied):self.ledger.quota(first['token'])
        with self.assertRaises(Denied):self.ledger.activate(code,'b'*32)
        with self.ledger.connect() as db:db.execute('UPDATE invites SET revoked=1')
        with self.assertRaises(Denied):self.ledger.quota(second['token'])
    def test_concurrent_activation_cannot_exceed_seat_cap(self):
        with self.ledger.connect() as db:db.execute("UPDATE config SET value=1 WHERE key='seats'")
        codes=[self.invite(),self.invite()]
        def activate_one(code):
            try:self.ledger.activate(code,'d'*32);return True
            except Denied:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(activate_one,codes)),1)
    def test_concurrent_reservation_and_all_quota_limits(self):
        token=self.ledger.activate(self.invite(calls=2,daily=2,tokens=200),'a'*32)['token']
        def reserve(_):
            try:return self.ledger.reserve(token,100)
            except Denied:return None
        with ThreadPoolExecutor(5) as pool:results=list(pool.map(reserve,range(5)))
        ids=[v for v in results if v]
        self.assertEqual(len(ids),1)
        self.ledger.settle(ids[0],20)
        self.ledger.settle(self.ledger.reserve(token,100),10)
        self.assertEqual(self.ledger.quota(token)['calls_remaining'],0)
        self.assertEqual(self.ledger.quota(token)['tokens_remaining'],170)
        with self.assertRaises(Denied):self.ledger.reserve(token,1)
    def test_uncertain_failures_keep_reservations_and_global_budget(self):
        token=self.ledger.activate(self.invite(),'a'*32)['token']
        with self.ledger.connect() as db:db.execute("UPDATE config SET value=100 WHERE key='tokens'")
        call=self.ledger.reserve(token,100);self.ledger.settle(call,None)
        with self.assertRaises(Denied):self.ledger.reserve(token,1)
    def test_expired_invite_and_invalid_bearer(self):
        token=self.ledger.activate(self.invite(),'a'*32)['token']
        with self.ledger.connect() as db:db.execute('UPDATE invites SET expires_at=0')
        for value in (token,'wrong'):
            with self.assertRaises(Denied):self.ledger.reserve(value,100)
    def test_prompt_and_tokens_are_not_persisted(self):
        code=self.invite();token=self.ledger.activate(code,'a'*32)['token']
        with self.ledger.connect() as db:dump='\n'.join(db.iterdump())
        self.assertNotIn(code,dump);self.assertNotIn(token,dump)
    def test_expensive_options_removed_and_large_inputs_rejected(self):
        value,reserve=validated_body({'model':'deepseek-flash','messages':[{'role':'user','content':'你好'}],
                                     'max_tokens':123,'n':8,'tools':[{}],'thinking':{'type':'enabled'}})
        self.assertNotIn('n',value);self.assertNotIn('tools',value)
        self.assertEqual(value['thinking'],{'type':'disabled'});self.assertGreater(reserve,123)
        for body in ({'model':'expensive','messages':[]},
                     {'model':'deepseek-flash','messages':[{'role':'user','content':'x'*192001}]},
                     {'model':'deepseek-flash','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://private.example'}}]}]}):
            with self.assertRaises(Denied):validated_body(body)


class GatewayHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.ledger=Ledger(Path(self.temp.name)/'usage.sqlite3')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.ledger=self.ledger;self.server.api_key='server-only-placeholder'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def test_activation_client_and_sse_proxy_count_every_model_request(self):
        private=Path(self.temp.name)/'client'
        code=self.ledger.issue('test',calls=2,daily=2)
        activate(private,self.base,code)
        config=apply_beta_config({},private)
        self.assertEqual(config['CHATSEEK_COMPLETIONS_URL'],self.base+'/v1/chat/completions')
        token=settings(private)['token']
        body={'model':'deepseek-flash','stream':True,'messages':[{'role':'user','content':'private-test-evidence'}]}
        provider={'choices':[{'message':{'content':'ok [M1]'},'finish_reason':'stop'}],'usage':{'total_tokens':23}}
        def call():
            c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            c.request('POST','/v1/chat/completions',json.dumps(body),{'Authorization':'Bearer '+token})
            r=c.getresponse();value=(r.status,r.read());c.close();return value
        with patch('beta_gateway.server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(provider).encode())) as upstream:
            status,raw=call();self.assertEqual(status,200);self.assertIn(b'[DONE]',raw)
            self.assertEqual(upstream.call_args.args[0].get_header('Authorization'),'Bearer server-only-placeholder')
            self.assertFalse(json.loads(upstream.call_args.args[0].data)['stream'])
        with patch('beta_gateway.server.urllib.request.urlopen',side_effect=OSError):self.assertEqual(call()[0],502)
        with patch('beta_gateway.server.urllib.request.urlopen') as upstream:
            self.assertEqual(call()[0],429);upstream.assert_not_called()
        with self.ledger.connect() as db:self.assertNotIn('private-test-evidence','\n'.join(db.iterdump()))
        self.assertNotIn('server-only-placeholder',raw.decode())
    def test_non_https_remote_gateway_rejected(self):
        for value in ('http://api.example.com','https://user:password@example.com','https://example.com/?secret=1'):
            with self.assertRaises(ValueError):gateway_url(value)
