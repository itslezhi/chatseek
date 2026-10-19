import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import chatseek_models as models
import chatseek_setup as setup
import wechat_rag as rag
from scripts import build_offline_source as distribution


class WizardChecksTests(unittest.TestCase):
    def test_environment_failure_cannot_mark_the_step_complete(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(setup, '_environment', return_value={
                'ready':False,'checks':[{'label':'Apple Silicon','passed':False}],'signature':'test'}), \
                patch.object(setup.subprocess,'run') as command:
            with self.assertRaisesRegex(RuntimeError,'环境检查未通过'):
                setup.check_environment(folder)
            command.assert_not_called()
            self.assertFalse((Path(folder)/'setup-checks.json').exists())

    def test_database_check_opens_only_the_contact_schema_and_invalidates_changed_keys(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(setup,'_require'):
            private=Path(folder);(private/'wechat-init').mkdir()
            (private/'account.json').write_text(json.dumps({'db_root':'/synthetic/db_storage'}))
            keys=private/'wechat-init/keys.json';keys.write_text('{"synthetic":"first"}')
            reader=MagicMock();reader.root=Path('/synthetic/db_storage')
            with patch.object(rag,'load_runtime',return_value=({},reader)):
                self.assertTrue(setup.check_wechat(private)['verified'])
            reader.open.assert_called_once_with(Path('/synthetic/db_storage/contact/contact.db'))
            reader.open.return_value.__enter__.return_value.query.assert_called_once_with('SELECT count(*) FROM sqlite_master')
            self.assertEqual(setup._checks(private)['wechat'],setup._wechat_signature(private))
            keys.write_text('{"synthetic":"changed"}')
            self.assertNotEqual(setup._checks(private)['wechat'],setup._wechat_signature(private))

    def test_invalid_api_response_does_not_replace_the_saved_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rag,'PRIVATE',Path(folder)), \
                patch.dict('os.environ',{},clear=True),patch.object(setup,'_require'), \
                patch.object(rag,'model_completion',return_value='not JSON'):
            private=Path(folder)
            setup.save_api(private,'previous-placeholder')
            original=(private/'.env').read_bytes()
            with self.assertRaisesRegex(RuntimeError,'API 验证未通过'):
                setup.verify_api(private,'new-placeholder',base_url='https://models.example.test/v1',model='custom')
            self.assertEqual(original,(private/'.env').read_bytes())
            self.assertNotIn('api',setup._checks(private))

    def test_successful_api_check_uses_a_short_request_and_can_reuse_the_saved_key(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)), \
                patch.dict('os.environ',{},clear=True),patch.object(setup,'_require'), \
                patch.object(rag,'model_completion',return_value='{"ok":true}') as provider:
            private=Path(folder)
            setup.save_api(private,'saved-placeholder',base_url='https://models.example.test/v1',model='custom')
            self.assertTrue(setup.verify_api(private,'',base_url='https://models.example.test/v1',model='custom')['verified'])
            self.assertEqual(provider.call_args.args[0]['LLM_API_KEY'],'saved-placeholder')
            self.assertEqual(provider.call_args.kwargs['max_output_tokens'],128)
            self.assertEqual(provider.call_args.kwargs['timeout'],45)
            self.assertEqual(setup._checks(private)['api'],setup._api_signature(rag.env_config()))
            setup.save_api(private,'saved-placeholder',base_url='https://models.example.test/v1',model='different')
            self.assertNotEqual(setup._checks(private)['api'],setup._api_signature(rag.env_config()))

    def test_provider_error_does_not_echo_sensitive_error_text(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{},clear=True), \
                patch.object(setup,'_require'),patch.object(rag,'model_completion',side_effect=RuntimeError('SENSITIVE-KEY')):
            with self.assertRaises(RuntimeError) as error:
                setup.verify_api(folder,'placeholder')
            self.assertNotIn('SENSITIVE-KEY',str(error.exception))

    def test_api_verification_cannot_skip_prior_checks(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)), \
                patch.object(rag,'model_completion') as provider:
            with self.assertRaisesRegex(RuntimeError,'前面的配置步骤'):
                setup.verify_api(folder,'placeholder')
            provider.assert_not_called()

    def test_model_download_does_not_block_status_polling(self):
        entered=threading.Event();release=threading.Event()
        def request(*_):
            entered.set();release.wait(3)
            return {'tokens':[1,2]}
        with tempfile.TemporaryDirectory() as folder,patch('wechat_embedding_client.request',side_effect=request):
            started=time.monotonic()
            setup.prepare_model(folder)
            self.assertLess(time.monotonic()-started,.5)
            self.assertTrue(entered.wait(1))
            self.assertTrue(setup.model_status(folder)['running'])
            release.set()
            for _ in range(100):
                if not setup.model_status(folder)['running']:break
                time.sleep(.01)
            self.assertFalse(setup.model_status(folder)['running'])
            self.assertFalse(setup.model_status(folder)['error'])


class BundledModelTests(unittest.TestCase):
    def fixture(self,path):
        for name in models.FILES:
            target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'synthetic')

    def test_bundled_model_takes_precedence_over_cache(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{},clear=True):
            root=Path(folder);private=root/'private';bundle=root/'models/bge-small-zh-v1.5'
            self.fixture(bundle);self.fixture(private/'models/models--BAAI--bge-small-zh-v1.5/snapshots'/models.REVISION)
            with patch.object(models,'ROOT',root):
                self.assertEqual(models.locate(private)['source'],'bundled')
                self.assertEqual(models.locate(private)['path'],bundle.resolve())

    def test_explicit_incomplete_model_does_not_silently_download_another_model(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'CHATSEEK_EMBEDDING_MODEL_DIR':folder}):
            location=models.locate(folder)
            self.assertEqual(location['source'],'configured');self.assertFalse(location['available'])

    def test_distribution_rejects_modified_public_weights(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'models').mkdir();weights=root/'weights';weights.mkdir()
            data=b'synthetic-public-model';(weights/'model.safetensors').write_bytes(data)
            manifest={'revision':models.REVISION,'files':[{'path':'model.safetensors','sha256':hashlib.sha256(data).hexdigest()}]}
            (root/'models/bge-small-zh-v1.5.manifest.json').write_text(json.dumps(manifest))
            with patch.object(distribution,'ROOT',root):
                self.assertEqual(len(distribution.model_files(weights)),1)
                (weights/'model.safetensors').write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                    distribution.model_files(weights)
