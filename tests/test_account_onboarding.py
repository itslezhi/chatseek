import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import chatseek_setup as setup


class AccountOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.private=self.base/'private';self.private.mkdir()
        self.root=self.base/'wxid_synthetic_1234/db_storage';self.root.mkdir(parents=True)
        self.job=patch.dict(setup._job,{'running':False,'message':''},clear=True)
        self.job.start();self.addCleanup(self.job.stop)

    def select(self,root=None):
        (self.private/'account.json').write_text(json.dumps({'db_root':str(root or self.root)}))

    def keys(self):
        (self.private/'wechat-init').mkdir(exist_ok=True)
        (self.private/'wechat-init/keys.json').write_text('{"synthetic":"placeholder"}')

    def state(self,detected=None,verified=False):
        return setup.connection_status(self.private,[self.root] if detected is None else detected,verified)

    def test_brand_new_device_has_no_saved_credentials(self):
        value=self.state([])
        self.assertEqual(value['state'],'no_data')
        self.assertFalse(value['credentials_saved']);self.assertIsNone(value['selected'])

    def test_discovery_does_not_implicitly_select_an_account(self):
        value=self.state()
        self.assertEqual(value['state'],'choose_account')
        self.assertIsNone(value['selected']);self.assertFalse(value['credentials_saved'])
        self.assertNotEqual(value['choices'][0]['label'],self.root.parent.name)
        self.assertIn('目录标识',value['choices'][0]['label'])

    def test_selection_without_keys_is_explicitly_first_time_initialization(self):
        self.select();value=self.state()
        self.assertEqual(value['state'],'initialize');self.assertTrue(value['can_change_account'])
        self.assertFalse(value['credentials_saved'])

    def test_just_completed_initialization_is_not_described_as_a_previous_install(self):
        self.select();self.keys();setup._job['completed']=True
        self.assertEqual(self.state()['state'],'initialized')
        self.assertEqual(self.state()['selected']['label'],'本次接入的微信数据')

    def test_saved_selection_is_authoritative_even_when_it_is_not_the_first_discovered_folder(self):
        other=self.base/'other/db_storage';other.mkdir(parents=True)
        self.select();self.keys()
        value=self.state([other,self.root])
        self.assertEqual(value['selected']['id'],str(self.root.resolve()))
        self.assertEqual(value['state'],'saved_connection')
        self.assertFalse(value['can_change_account'])

    def test_status_never_reads_a_contact_database_to_guess_a_nickname(self):
        self.select();self.keys()
        with patch.object(setup,'_read_account_profile') as read:
            value=self.state()
        read.assert_not_called()
        self.assertFalse(value['selected']['nickname_verified'])
        self.assertEqual(value['selected']['label'],'上次接入的微信数据')

    def test_verified_nickname_is_bound_to_the_saved_account_and_key_file(self):
        self.select();self.keys()
        profile={'signature':setup._wechat_signature(self.private),'nickname':'合成测试昵称','username':'wxid_synthetic'}
        (self.private/'account-profile.json').write_text(json.dumps(profile))
        value=self.state(verified=True)
        self.assertEqual(value['state'],'verified')
        self.assertEqual(value['selected']['label'],'合成测试昵称')
        (self.private/'wechat-init/keys.json').write_text('{"synthetic":"changed"}')
        self.assertFalse(self.state()['selected']['nickname_verified'])

    def test_orphaned_keys_and_incomplete_initialization_require_attention(self):
        self.keys()
        self.assertEqual(self.state()['state'],'needs_attention')
        (self.private/'wechat-init/keys.json').unlink()
        self.select()
        self.assertEqual(self.state()['state'],'needs_attention')

    def test_profile_lookup_confirms_the_self_contact_and_does_not_use_another_contacts_name(self):
        reader=MagicMock();reader.root=self.root;reader.account_id.return_value='wxid_self'
        db=reader.open.return_value.__enter__.return_value
        db.query.return_value=[{'username':'wxid_self','nick_name':'合成昵称'}]
        self.assertEqual(setup._read_account_profile(reader)['nickname'],'合成昵称')
        db.query.return_value=[{'username':'wxid_other','nick_name':'不能作为账号昵称'}]
        self.assertEqual(setup._read_account_profile(reader),{})

    def test_missing_self_contact_keeps_identity_unknown(self):
        reader=MagicMock();reader.account_id.return_value=None
        self.assertEqual(setup._read_account_profile(reader),{})
        reader.open.assert_not_called()
