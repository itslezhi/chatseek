import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import chatseek_llm as llm
import chatseek_setup as setup
import wechat_rag as rag
import wechat_media as media
from chatseek_beta_client import private_json


class PersonalModelTests(unittest.TestCase):
    def test_generic_provider_uses_own_url_key_model_without_deepseek_extensions(self):
        response={'choices':[{'message':{'content':'synthetic answer'}}]}
        config={'LLM_BASE_URL':'https://models.example.test/compatible/v1','LLM_API_KEY':'personal-placeholder',
                'LLM_MODEL':'custom/text-model','LLM_MAX_OUTPUT_TOKENS':'2048'}
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(response).encode())) as provider:
            self.assertEqual(rag.model_completion(config,'system',{'text':'synthetic'}),'synthetic answer')
        request=provider.call_args.args[0];body=json.loads(request.data)
        self.assertEqual(request.full_url,'https://models.example.test/compatible/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'),'Bearer personal-placeholder')
        self.assertEqual(body['model'],'custom/text-model');self.assertEqual(body['max_tokens'],2048)
        self.assertNotIn('thinking',body);self.assertNotIn('personal-placeholder',request.data.decode())

    def test_json_option_is_optional_but_legacy_deepseek_thinking_is_preserved(self):
        response={'choices':[{'message':{'content':'{}'}}]}
        config={'DEEPSEEK_API_KEY':'placeholder','LLM_JSON_MODE':'false'}
        with patch.object(rag.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(response).encode())) as provider:
            rag.model_completion(config,'system',{},json_mode=True)
        body=json.loads(provider.call_args.args[0].data)
        self.assertNotIn('response_format',body);self.assertEqual(body['thinking'],{'type':'disabled'})

    def test_base_url_allows_local_service_but_rejects_remote_cleartext_and_credentials(self):
        self.assertEqual(llm.validate_base_url('http://127.0.0.1:11434/v1/chat/completions'),'http://127.0.0.1:11434/v1')
        for value in ('http://example.test/v1','https://name:password@example.test/v1','https://example.test/v1?key=x','https://example.test/v1\nINJECTED=1'):
            with self.subTest(value=value),self.assertRaises(ValueError):llm.validate_base_url(value)

    def test_saving_personal_config_and_public_status_never_expose_key(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)),patch.dict(os.environ,{},clear=True),patch.object(setup,'accounts',return_value=[]):
            private=Path(folder)
            setup.save_api(private,'personal-secret-placeholder',base_url='https://models.example.test/v1',
                           model='vendor/model',vision_model='vendor/vision',context_tokens=64000,max_output_tokens=2048,json_mode=False)
            config=rag.env_config()
            self.assertEqual(config['DEEPSEEK_MODEL'],'vendor/model')
            self.assertEqual(config['DEEPSEEK_CONTEXT_TOKENS'],'64000')
            status=setup.status(private)
            self.assertTrue(status['api_configured']);self.assertEqual(status['model_config']['vision_model'],'vendor/vision')
            self.assertNotIn('personal-secret-placeholder',json.dumps(status))
            self.assertEqual((private/'.env').stat().st_mode&0o777,0o600)

    def test_existing_beta_credential_is_ignored_without_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)),patch.dict(os.environ,{},clear=True):
            private=Path(folder)
            private_json(private/'beta.json',{'token':'old-token','url':'https://relay.example.test'})
            (private/'.env').write_text('LLM_API_KEY=own-placeholder\nLLM_BASE_URL=https://models.example.test/v1\nLLM_MODEL=custom\n')
            config=rag.env_config()
            self.assertEqual(config['DEEPSEEK_API_KEY'],'own-placeholder')
            self.assertEqual(llm.completion_url(config),'https://models.example.test/v1/chat/completions')
            self.assertNotIn('CHATSEEK_COMPLETIONS_URL',config)

    def test_vision_model_is_explicit_for_other_providers(self):
        config={'LLM_BASE_URL':'https://models.example.test/v1','LLM_API_KEY':'own-placeholder','LLM_MODEL':'custom'}
        with patch.object(media.urllib.request,'urlopen') as provider,self.assertRaisesRegex(RuntimeError,'未配置视觉模型'):
            media.visual_description([(b'synthetic','frame')],'synthetic',config)
        provider.assert_not_called()
        response={'choices':[{'message':{'content':'synthetic picture'}}]}
        config['LLM_VISION_MODEL']='custom-vision'
        with patch.object(media.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(response).encode())) as provider:
            result,model=media.visual_description([(b'synthetic','frame')],'synthetic',config)
        self.assertEqual(model,'custom-vision');self.assertEqual(result,'synthetic picture')
        self.assertEqual(provider.call_args.args[0].full_url,'https://models.example.test/v1/chat/completions')
        self.assertNotIn('thinking',json.loads(provider.call_args.args[0].data))

    def test_generic_environment_fields_override_legacy_fields(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(rag,'PRIVATE',Path(folder)),patch.dict(os.environ,{'LLM_MODEL':'env-model','LLM_API_KEY':'env-key'},clear=True):
            path=Path(folder)/'.env';path.write_text('LLM_MODEL=file-model\nDEEPSEEK_MODEL=legacy\nDEEPSEEK_API_KEY=old-key\n')
            value=rag.env_config(path)
            self.assertEqual(value['DEEPSEEK_MODEL'],'env-model');self.assertEqual(value['DEEPSEEK_API_KEY'],'env-key')

    def test_model_names_cannot_inject_configuration_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):setup.save_api(Path(folder),'own-key',model='custom\nLLM_API_KEY=other')
            self.assertFalse((Path(folder)/'.env').exists())
