import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import chatseek_platform as platform
import chatseek_windows as windows
import chatseek_setup as setup
import wechat_rag as rag


class WindowsTests(unittest.TestCase):
    def test_local_app_data_and_custom_directory(self):
        with patch.object(platform.sys,'platform','win32'),patch.dict(os.environ,{'LOCALAPPDATA':'C:/Users/Test/AppData/Local'}):
            self.assertEqual(platform.desktop_data_dir(),Path('C:/Users/Test/AppData/Local/ChatSeek'))

    def test_discovery_and_validation_of_custom_account(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'xwechat_files/user_123/db_storage'
            (root/'contact').mkdir(parents=True);(root/'message').mkdir()
            (root/'contact/contact.db').touch();(root/'message/message_0.db').touch()
            with patch.object(windows,'documents_dir',return_value=Path(folder)):
                self.assertIn(root.resolve(),windows.accounts())
            for value in ('relative/path',r'\\server\share\db_storage',str(root.parent),None):
                with self.assertRaises(ValueError):windows.validate_root(value)

    def test_nonblocking_lock_excludes_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lock'
            code="import sys;import chatseek_platform as p\nf=open(sys.argv[1],'a')\ntry:p.flock(f,p.LOCK_EX|p.LOCK_NB)\nexcept BlockingIOError:sys.exit(7)\n"
            with path.open('a') as handle:
                platform.flock(handle,platform.LOCK_EX)
                self.assertEqual(subprocess.run([sys.executable,'-c',code,str(path)]).returncode,7)
            self.assertEqual(subprocess.run([sys.executable,'-c',code,str(path)]).returncode,0)

    def test_capture_only_accepts_verified_keys_for_selected_account(self):
        from sqlcipher_probe import DB,load_key
        try:import sqlcipher3
        except ImportError:self.skipTest('SQLCipher dependency not installed')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'测试账号/db_storage'
            paths=[root/'contact/contact.db',root/'message/message_0.db']
            keys=['11'*32,'22'*32];literals=[]
            for path,key in zip(paths,keys):
                path.parent.mkdir(parents=True,exist_ok=True)
                with DB(path,key,fixture_write=True) as db:db.query('CREATE TABLE toy(value);')
                with path.open('rb') as handle:salt=handle.read(16).hex()
                literals.append(("x'"+key+salt+"'").encode())
            # Wrong account salt and wrong key must not satisfy the required DB set.
            chunk=b"x'"+b'aa'*48+b"'"+literals[0]+literals[1]
            result=windows.capture(root,threading.Event(),lambda _:None,pids=[1],chunks=lambda *_:[chunk])
            self.assertEqual(result['keys'],{'contact/contact.db':keys[0],'message/message_0.db':keys[1]})
            with self.assertRaisesRegex(RuntimeError,'未能验证全部'):
                windows.capture(root,threading.Event(),lambda _:None,pids=[1],chunks=lambda *_:[literals[0]])
            manifest=Path(folder)/'keys.json';manifest.write_text(json.dumps(result),encoding='utf-8');manifest.chmod(0o600)
            self.assertEqual(load_key(manifest,paths[1]),keys[1])

    def test_explicit_ack_is_required_before_windows_capture(self):
        with patch.object(setup.sys,'platform','win32'),patch.object(setup,'start_windows_bootstrap') as capture:
            with self.assertRaises(ValueError):setup.start_bootstrap(Path('.'),False)
            capture.assert_not_called()

    def test_windows_environment_check_never_launches_lldb(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(setup.sys,'platform','win32'),\
             patch.object(setup,'_environment',return_value={'ready':True,'checks':[],'signature':'windows'}),\
             patch.object(setup.subprocess,'run') as command:
            self.assertTrue(setup.check_environment(folder)['verified'])
            command.assert_not_called()

    def test_watcher_termination_does_not_kill_unrelated_process(self):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            self.assertFalse(platform.terminate_watcher(child.pid,[sys.executable,'wrong-script','watch']))
            self.assertIsNone(child.poll())
        finally:child.terminate();child.wait(timeout=5)
