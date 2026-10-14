import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch

from wechat_rag import Store, sync, ask, evidence, display_result


def message(number,text,sender='peer',kind=1):
    return {'database':'message_0.db','local_id':str(number),'server_id':str(number*10),
            'create_time':str(1700000000+number),'sender_id':sender,'local_type':str(kind),'text':text}


class FakeReader:
    def __init__(self,root,rows):
        self.root=Path(root)
        self.rows=rows
        self.salt='test-salt'

    def snapshot(self,chat):
        return {'cursor':{'message_0.db':max([int(r['local_id']) for r in self.rows],default=0)},
                'salts':{'message_0.db':self.salt}}

    def read(self,chat,limit=200,after=None):
        rows=[dict(r) for r in self.rows]
        if after is None: return rows[-limit:]
        return [r for r in rows if int(r['local_id'])>after.get('message_0.db',0)][:limit]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'index.db')
        self.target={'chat_id':'peer','display_name':'测试好友'}
        self.reader=FakeReader(self.tmp.name,[message(1,'周六聚餐先定五点','self'),
                                            message(2,'改到六点，地点在北门'),
                                            message(3,None,kind=47)])

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_initial_incremental_and_repeated_poll(self):
        self.assertEqual(sync(self.store,self.reader,self.target)['changed_messages'],3)
        self.assertEqual(sync(self.store,self.reader,self.target)['changed_messages'],0)
        self.reader.rows.append(message(4,'六点见'))
        self.assertEqual(sync(self.store,self.reader,self.target)['changed_messages'],1)
        self.assertEqual(self.store.stats()['messages'],4)
        self.assertEqual(self.store.stats()['text_messages'],3)

    def test_more_than_one_page_survives_restart(self):
        self.reader.rows=[message(i,'连续消息') for i in range(1,452)]
        sync(self.store,self.reader,self.target)
        self.store.close();self.store=Store(Path(self.tmp.name)/'index.db')
        self.assertEqual(sync(self.store,self.reader,self.target)['changed_messages'],0)
        self.assertEqual(self.store.stats()['messages'],451)

    def test_failed_batch_does_not_advance_cursor(self):
        real=self.store.upsert
        def fail(rows):
            real(rows)
            raise RuntimeError('simulated interruption')
        with patch.object(self.store,'upsert',side_effect=fail):
            with self.assertRaises(RuntimeError): sync(self.store,self.reader,self.target)
        self.assertIsNone(self.store.meta('cursor'))
        self.assertEqual(self.store.stats()['messages'],0)
        self.assertEqual(sync(self.store,self.reader,self.target)['changed_messages'],3)

    def test_cross_account_or_contact_index_rejected(self):
        sync(self.store,self.reader,self.target)
        with self.assertRaises(RuntimeError):
            sync(self.store,self.reader,dict(self.target,chat_id='different-peer'))
        self.reader.root=Path('/different-account')
        with self.assertRaises(RuntimeError): sync(self.store,self.reader,self.target)

    def test_replaced_database_and_cursor_regression_rejected(self):
        sync(self.store,self.reader,self.target)
        self.reader.salt='new-salt'
        with self.assertRaises(RuntimeError): sync(self.store,self.reader,self.target)
        self.reader.salt='test-salt';self.reader.rows=self.reader.rows[:1]
        with self.assertRaises(RuntimeError): sync(self.store,self.reader,self.target)

    def test_retrieval_keeps_later_correction_and_sender(self):
        sync(self.store,self.reader,self.target)
        refs=evidence(self.store,self.store.retrieve('聚餐几点'))
        self.assertEqual([r['text'] for r in refs if r['content_recognized']],['周六聚餐先定五点','改到六点，地点在北门'])
        self.assertEqual([r['sender'] for r in refs[:2]],['我','测试好友'])
        self.assertEqual(refs[-1]['message_type'],'表情包')
        self.assertFalse(refs[-1]['content_recognized'])

    def test_summary_and_history_preserve_unrecognized_media(self):
        self.reader.rows.extend([message(4,None,kind=3),message(5,None,kind=34)])
        sync(self.store,self.reader,self.target)
        refs=evidence(self.store,self.store.retrieve('总结最近的聊天内容'))
        self.assertEqual([r['message_type'] for r in refs],['文字','文字','表情包','图片','语音'])
        self.assertTrue(all('尚未识别' in r['text'] for r in refs if not r['content_recognized']))
        self.assertTrue(any(r['message_type']=='表情包' for r in evidence(self.store,self.store.retrieve('表情包'))))

    def test_readable_answer_and_optional_all_sources(self):
        sync(self.store,self.reader,self.target)
        refs=evidence(self.store,self.store.retrieve('总结聊天'))
        result={'answer':'聚餐改到六点。[M2]','sources':refs}
        output=io.StringIO()
        with contextlib.redirect_stdout(output): display_result(result)
        self.assertIn('聚餐改到六点。[M2]',output.getvalue())
        self.assertIn('表情包 1 条',output.getvalue())
        self.assertNotIn('周六聚餐先定五点',output.getvalue())
        output=io.StringIO()
        with contextlib.redirect_stdout(output): display_result(result,all_sources=True)
        self.assertIn('周六聚餐先定五点',output.getvalue())

    def test_recent_update_removes_old_search_terms(self):
        sync(self.store,self.reader,self.target)
        self.reader.rows[0]['text']='已取消原计划'
        sync(self.store,self.reader,self.target)
        self.assertEqual(self.store.retrieve('聚餐'),[])
        self.assertTrue(self.store.retrieve('取消'))

    def test_no_evidence_does_not_call_api(self):
        sync(self.store,self.reader,self.target)
        with patch('wechat_rag.env_config',return_value={'DEEPSEEK_API_KEY':'test-key-not-real'}),\
             patch('wechat_rag.urllib.request.urlopen') as network:
            result=ask(self.store,'恐龙化石')
            self.assertFalse(result['api_called']);network.assert_not_called()

    def test_missing_key_fails_before_any_network(self):
        with patch('wechat_rag.env_config',return_value={}),\
             patch('wechat_rag.urllib.request.urlopen') as network:
            with self.assertRaises(RuntimeError): ask(self.store,'总结聊天')
            network.assert_not_called()


if __name__=='__main__': unittest.main()
