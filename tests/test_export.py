import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import wechat_rag as rag
import wechat_export as export
import wechat_web as web
from test_sync import FakeReader,message


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.private=Path(self.tmp.name)
        scope=patch.object(rag,'PRIVATE',self.private);scope.start();self.addCleanup(scope.stop)
        self.target={'chat_id':'peer','db_root':str(self.private/'source'),'display_name':'测试好友<unsafe>'}
        self.reader=FakeReader(self.target['db_root'],[message(1,'长文字🙂\n'+'原文'*2500),
            message(2,'<script>alert(1)</script>'),message(3,None,kind=43)])
        runtime=patch.object(rag,'load_runtime',return_value=(self.target,self.reader));runtime.start();self.addCleanup(runtime.stop)

    def test_export_keeps_full_text_order_and_media_markers_without_model(self):
        with patch.object(rag,'model_completion') as model:
            output,name=export.prepare(web.context_id(self.target),'json')
            with output:document=json.load(output)
            self.assertEqual(document['count'],3)
            self.assertEqual(document['messages'][0]['text'],self.reader.rows[0]['text'])
            self.assertEqual(document['messages'][2]['text'],'[视频；媒体文件未包含]')
            self.assertNotIn('<',name);model.assert_not_called()

    def test_html_is_escaped_and_wrong_context_cannot_read_another_contact(self):
        output,_=export.prepare(web.context_id(self.target),'html')
        with output:body=output.read().decode('utf-8')
        self.assertIn('&lt;script&gt;',body);self.assertNotIn('<script>',body)
        with patch.object(rag,'sync') as sync:
            with self.assertRaises(ValueError):export.prepare('stale','json')
            sync.assert_not_called()

    def test_export_drains_more_than_the_single_sync_batch_limit(self):
        self.reader.rows=[message(i,'历史原文') for i in range(1,20003)]
        with patch.object(rag,'sync',wraps=rag.sync) as sync:
            output,_=export.prepare(web.context_id(self.target),'json')
            with output:document=json.load(output)
        self.assertEqual(document['count'],20002)
        self.assertEqual(document['messages'][-1]['id'],'M20002')
        self.assertEqual(sync.call_count,2)

    def test_empty_export_and_nonprogressing_sync(self):
        self.reader.rows=[]
        output,_=export.prepare(web.context_id(self.target),'json')
        with output:self.assertEqual(json.load(output)['messages'],[])
        self.reader.rows=[message(1,'test')]
        with patch.object(rag,'sync'):
            with self.assertRaisesRegex(RuntimeError,'未导出不完整'):export.prepare(web.context_id(self.target),'txt')
        with self.assertRaises(ValueError):export.prepare(web.context_id(self.target),'../html')

    def test_export_requires_the_existing_app_access_consent(self):
        self.assertTrue(web.needs_access_confirmation('export',{}))
