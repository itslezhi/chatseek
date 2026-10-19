import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import wechat_embedding_client as client


CHILD = r'''
import json,os,sys
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:
 request=json.loads(line)
 if request.get('crash'):os._exit(11)
 print(json.dumps({'tokens':[1,2,3]}),flush=True)
'''


class EmbeddingProcessTests(unittest.TestCase):
    def tearDown(self):client.close_worker();client._stopping.clear()
    def child(self):
        child=subprocess.Popen([sys.executable,'-u','-c',CHILD],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,bufsize=0)
        client._process=child;client._buffer=b''
        self.assertTrue(client._read(child,3)['ready'])
        return child
    def test_child_exit_is_converted_to_controlled_error_and_can_retry(self):
        child=self.child()
        with patch.object(client,'_start',return_value=child),contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError,'网页服务仍在运行'):
            client.request(Path('.'),{'crash':True})
        self.assertEqual(child.wait(timeout=3),11)
        self.assertIsNone(client._process)
        retry=self.child()
        with patch.object(client,'_start',return_value=retry):
            self.assertEqual(client.request(Path('.'),{'action':'tokens','text':'合成内容'})['tokens'],[1,2,3])
    def test_worker_protocol_does_not_echo_question_in_error_logs(self):
        child=self.child();output=io.StringIO()
        with patch.object(client,'_start',return_value=child),contextlib.redirect_stdout(output),self.assertRaises(RuntimeError):
            client.request(Path('.'),{'crash':True,'text':'PRIVATE-QUESTION-DO-NOT-LOG'})
        self.assertNotIn('PRIVATE-QUESTION',output.getvalue())
        self.assertIn('embedding_worker_error',output.getvalue())
    def test_worker_start_limits_threads_and_does_not_pass_api_secret(self):
        from unittest.mock import MagicMock
        process=MagicMock();process.poll.return_value=None
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-placeholder-secret'}), \
             patch.object(client.subprocess,'Popen',return_value=process) as popen, \
             patch.object(client,'_read',return_value={'ready':True}):
            self.assertIs(client._start(Path('/example/private')),process)
            config=popen.call_args.kwargs
            self.assertNotIn('DEEPSEEK_API_KEY',config['env'])
            self.assertEqual(config['env']['OMP_NUM_THREADS'],'1')
            self.assertEqual(config['env']['TOKENIZERS_PARALLELISM'],'false')
            self.assertNotIn('test-placeholder-secret',str(popen.call_args.args))
        # Avoid treating a mock process as a real child during cleanup.
        client._process=None
    def test_close_terminates_worker_and_releases_pipes(self):
        child=self.child();client.close_worker()
        self.assertIsNotNone(child.poll());self.assertTrue(child.stdin.closed);self.assertTrue(child.stdout.closed)
    def test_shutdown_interrupts_a_waiting_request(self):
        child=subprocess.Popen([sys.executable,'-u','-c','import time;time.sleep(30)'],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        client._process=child;client._buffer=b''
        started=threading.Event();errors=[]
        def start(_):started.set();return child
        def run():
            try:client.request(Path('.'),{'action':'tokens','text':'synthetic'})
            except RuntimeError as exc:errors.append(exc)
        with patch.object(client,'_start',side_effect=start),contextlib.redirect_stdout(io.StringIO()):
            thread=threading.Thread(target=run);thread.start()
            self.assertTrue(started.wait(2));before=time.monotonic()
            client.shutdown_worker();thread.join(timeout=3)
        self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-before,5)
        self.assertEqual(len(errors),1);self.assertIsNotNone(child.poll())

if __name__=='__main__':unittest.main()
