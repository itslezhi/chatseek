import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import chatseek_runtime as runtime
import chatseek_setup as setup
import wechat_rag as rag
import wechat_web as web

class DesktopSetupTests(unittest.TestCase):
    def test_frozen_child_entrypoints_never_launch_python_source(self):
        with patch.object(runtime.sys,'frozen',True,create=True),patch.object(runtime.sys,'executable','/App/ChatSeekRuntime'):
            self.assertEqual(runtime.child_command('worker','/cache'),['/App/ChatSeekRuntime','--chatseek-worker','/cache'])
            self.assertEqual(runtime.child_command('watch','--interval','5'),['/App/ChatSeekRuntime','--chatseek-watch','--interval','5'])
    def test_new_install_state_does_not_read_wechat_or_call_models(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)),patch.object(rag,'load_runtime') as read:
            self.assertEqual(web.query('state',{}),{'setup_required':True});read.assert_not_called()
    def test_personal_key_is_private_and_switches_out_of_beta(self):
        from chatseek_beta_client import private_json,settings
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)):
            private=Path(folder);private_json(private/'beta.json',{'token':'invite-secret','device':'test-device','url':'https://example.com'})
            setup.save_api(private,'personal-test-key')
            self.assertEqual(rag.env_config()['DEEPSEEK_API_KEY'],'personal-test-key')
            self.assertNotIn('token',settings(private))
            self.assertEqual((private/'.env').stat().st_mode&0o777,0o600)
    def test_account_selection_and_explicit_bootstrap_ack(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(setup,'accounts',return_value=[Path('/test/db_storage')]),patch.object(setup,'status',return_value={}):
            setup.choose_account(Path(folder),'/test/db_storage')
            self.assertEqual(json.loads((Path(folder)/'account.json').read_text())['db_root'],'/test/db_storage')
            with self.assertRaises(ValueError):setup.choose_account(folder,'/another/account')
            with self.assertRaises(ValueError):setup.start_bootstrap(folder,False)
