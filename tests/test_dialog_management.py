import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import wechat_rag as rag
import wechat_web as web
from wechat_dialog import DialogMemory, model_history


class DialogManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'chat.sqlite3'
        self.store=rag.Store(self.path)
        self.memory=DialogMemory(self.store)
        self.session=self.memory.session()
        self.ids=[]
        previous=0
        for question in ('聚餐几点','地点在哪','需要带什么'):
            previous=self.memory.append(self.session,previous,question,{'answer':'测试回答。[M1]','sources':[{'id':'M1'}]})
            self.ids.append(previous)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_archive_is_persistent_and_excluded_from_model_memory(self):
        self.memory.manage(self.session,self.ids[0],'archive')
        rows=self.memory.recent(self.session)
        self.assertEqual([r['question'] for r in model_history(rows)],['地点在哪','需要带什么'])
        reopened=rag.Store(self.path)
        try:
            memory=DialogMemory(reopened)
            self.assertEqual(memory.recent(self.session,status='archived')[0]['turn_id'],self.ids[0])
            memory.manage(self.session,self.ids[0],'unarchive')
            self.assertEqual(len(memory.recent(self.session)),3)
        finally:reopened.close()

    def test_delete_is_recoverable_and_leaves_original_chats_untouched(self):
        with self.store.db:
            self.store.upsert([{'database':'test.db','local_id':1,'server_id':1,'create_time':1,
                               'sender_id':'peer','local_type':1,'text':'原始聊天仍在'}])
        self.memory.manage(self.session,self.ids[1],'delete')
        self.assertEqual(len(self.memory.recent(self.session)),2)
        self.assertEqual(self.memory.recent(self.session,status='deleted')[0]['question'],'地点在哪')
        self.assertEqual(self.store.db.execute('SELECT text FROM messages').fetchone()[0],'原始聊天仍在')
        self.memory.manage(self.session,self.ids[1],'restore')
        self.assertEqual([r['turn_id'] for r in self.memory.recent(self.session)],self.ids)

    def test_restore_deleted_archive_returns_to_archive(self):
        self.memory.manage(self.session,self.ids[0],'archive')
        self.memory.manage(self.session,self.ids[0],'delete')
        self.memory.manage(self.session,self.ids[0],'restore')
        self.assertEqual(len(self.memory.recent(self.session,status='archived')),1)
        self.assertEqual(len(self.memory.recent(self.session)),2)

    def test_wrong_session_invalid_id_or_state_cannot_change_records(self):
        for turn_id,action in ((True,'delete'),(0,'delete'),(999,'archive'),(self.ids[0],'restore'),(self.ids[0],'purge')):
            with self.subTest(turn_id=turn_id,action=action),self.assertRaises(ValueError):
                self.memory.manage(self.session,turn_id,action)
        with self.assertRaises(ValueError):self.memory.manage('other-session',self.ids[0],'delete')
        self.assertEqual(len(self.memory.recent(self.session)),3)

    def test_legacy_rows_migrate_without_losing_content(self):
        path=Path(self.tmp.name)/'legacy.sqlite3'
        store=rag.Store(path)
        try:
            store.db.execute('CREATE TABLE qa_turns(id INTEGER PRIMARY KEY,session TEXT NOT NULL,question TEXT NOT NULL,result TEXT NOT NULL)')
            store.db.execute('INSERT INTO qa_turns VALUES(1,?,?,?)',('legacy','旧提问',json.dumps({'answer':'旧回答','sources':[]})))
            store.db.commit()
            memory=DialogMemory(store)
            self.assertEqual(memory.recent('legacy')[0]['answer'],'旧回答')
        finally:store.close()

    def web_context(self):
        target={'db_root':self.tmp.name,'chat_id':'peer'}
        return target,{'context':web.context_id(target),'session':self.session}

    def test_new_question_after_archiving_latest_turn_uses_remaining_memory(self):
        self.memory.manage(self.session,self.ids[-1],'archive')
        target,data=self.web_context()
        with patch.object(rag,'runtime_lock',contextlib.nullcontext),patch.object(rag,'load_runtime',return_value=(target,None)), \
             patch.object(rag,'store_path',return_value=self.path),patch.object(rag,'ask',return_value={'answer':'新回答','sources':[]}) as ask:
            result=web.query('ask',{**data,'question':'接着聊'})
        self.assertEqual([r['question'] for r in ask.call_args.kwargs['history']],['聚餐几点','地点在哪'])
        self.assertIsInstance(result['turn_id'],int)
        self.assertEqual(self.memory.recent(self.session)[-1]['question'],'接着聊')

    def test_manage_during_model_request_rejects_answer_from_stale_history(self):
        target,data=self.web_context()
        def answer(*args,**kwargs):
            self.memory.manage(self.session,self.ids[0],'delete')
            return {'answer':'基于旧上下文的回答','sources':[]}
        with patch.object(rag,'runtime_lock',contextlib.nullcontext),patch.object(rag,'load_runtime',return_value=(target,None)), \
             patch.object(rag,'store_path',return_value=self.path),patch.object(rag,'ask',side_effect=answer), \
             self.assertRaisesRegex(RuntimeError,'更新对话'):
            web.query('ask',{**data,'question':'接着聊'})
        self.assertEqual(len(self.memory.recent(self.session)),2)

    def test_management_api_returns_each_category_and_checks_contact_context(self):
        target,data=self.web_context()
        with patch.object(rag,'runtime_lock',contextlib.nullcontext),patch.object(rag,'load_runtime',return_value=(target,None)), \
             patch.object(rag,'store_path',return_value=self.path):
            result=web.query('dialog-manage',{**data,'turn_id':self.ids[0],'action':'archive'})
            self.assertEqual(len(result['turns']),2)
            self.assertEqual(len(result['archived_turns']),1)
            self.assertEqual(result['deleted_turns'],[])
            with self.assertRaises(ValueError):
                web.query('dialog-manage',{**data,'context':'wrong','turn_id':self.ids[1],'action':'delete'})


if __name__=='__main__':unittest.main()
