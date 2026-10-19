from legacy_support import isolate
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch

from wechat_rag import Store, sync, ask, evidence, display_result, Reader
import wechat_rag as app


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

    def account_id(self):
        return 'self'

    def sender_names(self, ids):
        return {key: {'alice':'小王', 'bob':'小李', 'self':'本人'}.get(key,key) for key in ids}


class SyncTests(unittest.TestCase):
    def test_resource_database_does_not_block_message_shards(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)/'message'
            folder.mkdir()
            for name in ('message_0.db', 'message_1.db', 'message_resource.db'):
                (folder/name).touch()
            reader = Reader(root, Path(root)/'unused-keys.json')
            opened = []
            @contextlib.contextmanager
            def fake_open(path):
                opened.append(path.name)
                if path.name == 'message_resource.db':
                    raise RuntimeError('SQLCipher query failed (code 26)')
                class Database:
                    def query(self, sql): return [{'found': 1}]
                yield Database()
            with patch.object(reader, 'open', side_effect=fake_open):
                self.assertEqual(len(reader.paths('peer')), 2)
            self.assertEqual(opened, ['message_0.db', 'message_1.db'])

    def test_group_reader_decodes_text_and_removes_only_matching_sender_prefix(self):
        import zstandard
        reader = Reader('/fixture', '/unused')
        @contextlib.contextmanager
        def database(path):
            class Database:
                def query(self, sql):
                    if sql.startswith('SELECT local_id'):
                        raw = zstandard.ZstdCompressor().compress('alice:\n周六聚餐\n正文第二行'.encode())
                        return [{'local_id':'1','server_id':'10','local_type':'1',
                                 'create_time':'1700000000','real_sender_id':'7','body':raw.hex()}]
                    return [{'user_name':'alice'}]
            yield Database()
        with patch.object(reader,'paths',return_value=[(Path('/fixture/message_0.db'),'Msg_test')]), \
             patch.object(reader,'open',side_effect=database):
            rows=reader.read('room@chatroom')
            self.assertEqual(rows[0]['text'],'周六聚餐\n正文第二行')
            self.assertEqual(rows[0]['sender_id'],'alice')
            self.assertEqual(reader.read('private-peer')[0]['text'],'alice:\n周六聚餐\n正文第二行')

    def setUp(self):
        isolate(self)
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

    def test_group_senders_self_unknown_and_incremental_member(self):
        target = dict(self.target, chat_id='group@chatroom', self_id='self')
        self.reader.rows = [message(1,'周六聚餐','alice'), message(2,'六点见','bob'),
                            message(3,'收到','self'), message(4,'未知成员','stranger'), message(5,'身份缺失',None)]
        sync(self.store,self.reader,target)
        refs = evidence(self.store,self.store.retrieve('总结聊天'))
        self.assertEqual([r['sender'] for r in refs], ['小王','小李','我','stranger','未知发送者'])
        self.assertEqual(self.store.stats()['conversation_type'], 'group')
        self.reader.rows.append(message(6,'新成员到了','new-member'))
        sync(self.store,self.reader,target)
        self.assertIn('new-member',self.store.meta('sender_names'))
        self.store.set_meta('sender_names',{'alice':'同名','bob':'同名'})
        refs=evidence(self.store,self.store.retrieve('总结聊天'))
        self.assertNotEqual(refs[0]['sender'],refs[1]['sender'])

    def test_group_without_verified_self_never_labels_other_members_as_me(self):
        self.reader.rows=[message(1,'群成员发言','alice')]
        sync(self.store,self.reader,dict(self.target,chat_id='group@chatroom'))
        self.assertEqual(evidence(self.store,self.store.retrieve('总结'))[0]['sender'],'小王')

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


class SelectionTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.private = Path(self.tmp.name)
        self.patch = patch.object(app, 'PRIVATE', self.private)
        self.patch.start()
        self.target = {'chat_id': 'peer', 'display_name': '原好友', 'db_root': self.tmp.name}
        (self.private/'target.json').write_text(json.dumps(self.target))
        self.reader = FakeReader(self.private, [message(1, '原好友专属消息')])
        self.contact = {'username': 'other', 'nick_name': '新好友', 'remark': '小王'}
        self.reader.resolve = lambda name: [self.contact]
        self.runtime = patch.object(app, 'load_runtime', side_effect=lambda: (
            json.loads((self.private/'target.json').read_text()), self.reader))
        self.runtime.start()
        store = Store(self.private/'chat.sqlite3')
        sync(store, self.reader, self.target)
        store.close()

    def tearDown(self):
        self.runtime.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def test_missing_and_ambiguous_names_keep_original_selection(self):
        before = (self.private/'target.json').read_bytes()
        self.reader.resolve = lambda name: []
        self.assertEqual(app.select_contact('不存在')['message'], '没有此联系人或群聊')
        self.reader.resolve = lambda name: [self.contact, dict(self.contact, username='duplicate')]
        result = app.select_contact('小王')
        self.assertFalse(result['selected'])
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual((self.private/'target.json').read_bytes(), before)

    def test_selection_isolated_and_return_preserves_legacy_index(self):
        self.reader.rows = [message(1, '新好友专属消息', sender='other')]
        self.assertTrue(app.select_contact('小王')['selected'])
        selected = json.loads((self.private/'target.json').read_text())
        self.assertEqual(selected['display_name'], '小王')
        self.assertNotEqual(app.store_path(selected), (self.private/'chat.sqlite3').resolve())
        store = Store(app.store_path(selected))
        self.assertEqual(store.db.execute('SELECT text FROM messages').fetchone()[0], '新好友专属消息')
        store.close()
        self.reader.resolve = lambda name: [dict(self.contact, username='peer', remark='原好友')]
        self.reader.rows = [message(1, '原好友专属消息')]
        app.select_contact('原好友')
        selected = json.loads((self.private/'target.json').read_text())
        self.assertEqual(app.store_path(selected), (self.private/'chat.sqlite3').resolve())
        self.assertEqual((self.private/'target.json').stat().st_mode & 0o777, 0o600)

    def test_id_disambiguates_and_failed_sync_does_not_switch(self):
        self.reader.resolve = lambda name: [self.contact, dict(self.contact, username='duplicate')]
        before = (self.private/'target.json').read_bytes()
        with self.assertRaises(ValueError): app.select_contact('小王', 'not-a-match')
        with patch.object(app, 'sync', side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError): app.select_contact('小王', 'other')
        self.assertEqual((self.private/'target.json').read_bytes(), before)
        self.assertTrue(app.select_contact('小王', 'other')['selected'])

    def test_contact_without_local_messages_keeps_selection(self):
        before = (self.private/'target.json').read_bytes()
        with patch.object(self.reader, 'snapshot', side_effect=RuntimeError('conversation_has_no_local_message_table')):
            result = app.select_contact('小王')
        self.assertTrue(result['found'])
        self.assertFalse(result['selected'])
        self.assertEqual((self.private/'target.json').read_bytes(), before)

    def test_group_selection_and_switch_back_keep_indexes_separate(self):
        self.contact = dict(self.contact,username='room@chatroom',nick_name='测试群',remark='')
        self.reader.rows=[message(1,'群消息','alice'),message(2,'我在群里','self')]
        result=app.select_contact('测试群')
        self.assertTrue(result['selected'])
        self.assertEqual(result['conversation_type'],'group')
        group=json.loads((self.private/'target.json').read_text())
        self.assertEqual(group['self_id'],'self')
        store=Store(app.store_path(group))
        refs=evidence(store,store.retrieve('总结'))
        self.assertEqual([r['sender'] for r in refs],['小王','我'])
        store.close()
        self.contact=dict(self.contact,username='peer',nick_name='原好友')
        self.reader.rows=[message(1,'原好友专属消息')]
        result=app.select_contact('原好友')
        self.assertEqual(result['conversation_type'],'direct')
        self.assertNotIn('self_id',json.loads((self.private/'target.json').read_text()))

    def test_watcher_reloads_contact_each_cycle(self):
        next_target = dict(self.target, chat_id='other', index_file='contacts/other/chat.sqlite3')
        with patch.object(app, 'load_runtime', side_effect=[(self.target, self.reader), (next_target, self.reader)]), \
             patch.object(app, 'sync', return_value={'changed_messages': 0}) as poll, \
             patch.object(app.time, 'sleep', side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt): app.run_watch(5)
        self.assertEqual([call.args[2]['chat_id'] for call in poll.call_args_list], ['peer', 'other'])

    def test_named_history_does_not_fall_back_to_previous_contact(self):
        self.reader.resolve = lambda name: []
        output = io.StringIO()
        with patch('sys.argv', ['wechat_rag.py', 'history', '--contact', '不存在']), \
             contextlib.redirect_stdout(output), patch.object(app, 'evidence') as read:
            app.main()
        self.assertEqual(json.loads(output.getvalue())['message'], '没有此联系人或群聊')
        read.assert_not_called()


if __name__=='__main__': unittest.main()
