from legacy_support import isolate
import datetime as dt
import contextlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import wechat_rag as rag
import wechat_web as web
from wechat_summary import batches, summary_inputs, INPUT_CHARS


NOW = dt.datetime(2026,10,18,12,tzinfo=ZoneInfo('Asia/Shanghai'))


class SummaryTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = rag.Store(Path(self.tmp.name)/'chat.sqlite3')
        self.clock = patch.object(rag,'local_now',return_value=NOW)
        self.clock.start()
        self.config = patch.object(rag,'env_config',return_value={'DEEPSEEK_API_KEY':'test-placeholder-key'})
        self.config.start()
        with self.store.db:
            self.store.set_meta('peer_id','peer')
            self.store.set_meta('display_name','测试会话')
            self.store.upsert([self.row(i,'日常讨论 '+str(i)) for i in range(1,217)]+[
                self.row(217,'昨天的记录',offset=-86400), self.row(218,'明天的记录',offset=86400),
                self.row(219,'撤回消息',kind=10000)])

    def row(self,number,text,*,offset=0,kind=1):
        return {'database':'m.db','local_id':number,'server_id':number,
                'create_time':int(NOW.timestamp())-3600+number+offset,
                'sender_id':'peer','local_type':kind,'text':text}

    def tearDown(self):
        self.config.stop(); self.clock.stop(); self.store.close(); self.tmp.cleanup()

    def test_entire_day_includes_216_messages_and_early_decisions(self):
        with self.store.db:
            self.store.upsert([self.row(1,'已完成报告，客户确认通过'),self.row(216,'后来取消了聚餐')])
        rows = self.store.retrieve('今天主要聊了什么')
        self.assertEqual([r['id'] for r in rows],list(range(1,217)))
        self.assertIn('报告',rows[0]['text']);self.assertIn('取消',rows[-1]['text'])

    def test_specific_lookup_still_uses_matching_context_and_explicit_ids(self):
        with self.store.db:
            self.store.upsert([self.row(1,'聚餐原定五点'),self.row(2,'改到六点')])
        rows = self.store.retrieve('今天聚餐几点')
        self.assertIn(2,[r['id'] for r in rows]);self.assertLess(len(rows),216)
        self.assertEqual([r['id'] for r in self.store.retrieve('总结今天 M1')],[1])

    def test_all_batches_are_read_before_final_answer(self):
        calls = []
        def model(config,system,payload,**kwargs):
            calls.append(payload)
            refs = payload['evidence']
            if isinstance(refs[0].get('id'),str):
                return '本批讨论内容。['+refs[0]['id']+'] ['+refs[-1]['id']+']'
            return '今天讨论了多个主题。[M1] [M216]'
        with patch.object(rag,'model_completion',side_effect=model):
            result = rag.ask(self.store,'今天主要聊了什么')
        read_ids = [ref['id'] for payload in calls[:-1] for ref in payload['evidence']]
        self.assertEqual(read_ids,[f'M{i}' for i in range(1,217)])
        self.assertEqual(len(result['sources']),216)
        scope = result['query_context']
        self.assertEqual(scope['selected_messages'],216);self.assertEqual(scope['available_messages'],216)
        self.assertEqual(scope['retrieval_mode'],'full_summary')
        self.assertEqual(scope['summary_processing']['model_read_messages'],216)
        self.assertEqual(result['citation_check'],'passed')

    def test_full_summary_ignores_old_subset_ids_in_followup_rewrite(self):
        def model(config,system,payload,**kwargs):
            refs = payload['evidence']
            return '讨论。['+refs[0]['id']+']' if 'id' in refs[0] else '汇总。[M1]'
        with patch.object(rag,'resolve_followup',return_value='总结今天 M200 M201'), \
             patch.object(rag,'model_completion',side_effect=model):
            result = rag.ask(self.store,'今天主要聊了什么',history=[{'question':'旧问题'}])
        self.assertEqual(len(result['sources']),216)

    def test_recent_summary_does_not_keep_previous_subset_ids(self):
        def model(config,system,payload,**kwargs):
            refs = payload['evidence']
            return '讨论。['+refs[0]['id']+']' if 'id' in refs[0] else '汇总。[M1]'
        with patch.object(rag,'resolve_followup',return_value='总结最近的聊天内容 M200 M201'), \
             patch.object(rag,'model_completion',side_effect=model):
            result = rag.ask(self.store,'总结最近的聊天内容',history=[{'question':'旧问题'}])
        self.assertEqual(len(result['sources']),217)
        self.assertEqual(result['query_context']['retrieval_mode'],'full_summary')

    def test_colloquial_summary_does_not_become_keyword_search(self):
        for question in ('今天聊了啥','今天说了些什么','梳理今天的讨论','今天讨论了哪些'):
            with self.subTest(question=question):
                self.assertEqual(len(self.store.retrieve(question)),216)

    def test_long_message_tail_is_read_without_truncation(self):
        text = '长文本\n"\\'*18000+'最后明确：合作已取消。'
        with self.store.db:
            self.store.db.execute('DELETE FROM messages')
            self.store.upsert([self.row(1,text)])
        seen = []
        def model(config,system,payload,**kwargs):
            seen.extend(ref['text'] for ref in payload['evidence'] if ref.get('id')=='M1')
            return '合作取消。[M1]'
        with patch.object(rag,'model_completion',side_effect=model):
            result = rag.ask(self.store,'总结今天的聊天')
        self.assertEqual(''.join(seen),text)
        self.assertEqual(result['sources'][0]['text'],text)
        for group in batches(result['sources']):
            self.assertLessEqual(len(json.dumps(group,ensure_ascii=False)),INPUT_CHARS)

    def test_failed_batch_cannot_be_presented_as_full_summary(self):
        calls = 0
        def model(config,system,payload,**kwargs):
            nonlocal calls
            calls += 1
            if calls==2:
                raise RuntimeError('test model timeout')
            return '批次摘要。[M1]'
        with patch.object(rag,'model_completion',side_effect=model), self.assertRaisesRegex(RuntimeError,'timeout'):
            rag.ask(self.store,'今天聊了什么')

    def test_invalid_batch_citation_stops_summary(self):
        with patch.object(rag,'model_completion',return_value='不存在的内容。[M9999]'), \
             self.assertRaisesRegex(RuntimeError,'有效原文引用'):
            rag.ask(self.store,'今天聊了什么')

    def test_small_summary_uses_one_model_call_and_reports_unread_media(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM messages')
            self.store.upsert([self.row(1,'完成了报告'),self.row(2,None,kind=34)])
        with patch.object(rag,'model_completion',return_value='完成报告。[M1]') as model:
            result = rag.ask(self.store,'今天主要完成什么')
        self.assertEqual(model.call_count,1)
        self.assertEqual(result['query_context']['summary_processing']['unrecognized_messages'],1)

    def test_reference_adjacent_to_chinese_is_not_labeled_full_summary(self):
        with patch.object(rag,'model_completion',return_value='摘要。[M1]'):
            result = rag.ask(self.store,'总结今天M1')
        self.assertEqual(result['query_context']['retrieval_mode'],'message')
        self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['query_context']['available_messages'],216)

    def test_recent_summary_has_explicit_seven_day_scope(self):
        with patch.object(rag,'model_completion',side_effect=lambda config,system,payload,**kw:
                '记录。['+payload['evidence'][0]['id']+']' if 'id' in payload['evidence'][0] else '记录。[M1]'):
            result = rag.ask(self.store,'总结最近的聊天内容')
        self.assertEqual(result['query_context']['date_range']['start_date'],'2026-10-12')
        self.assertEqual(result['query_context']['selected_messages'],217)

    def test_many_batches_are_reduced_with_original_citations(self):
        refs = [{'id':f'M{i}','text':'正文'*3000,'message_type':'文字','content_recognized':True} for i in range(1,81)]
        raw_ids = []
        def complete(system,payload):
            items = payload['evidence']
            if 'id' in items[0]:
                raw_ids.extend(item['id'] for item in items)
                return '讨论主题。'*350+'['+items[0]['id']+']'
            return '合并主题。['+items[0]['source_ids'][0]+']'
        notes,report = summary_inputs(refs,'总结全部聊天',{},complete)
        self.assertEqual(raw_ids,[ref['id'] for ref in refs])
        self.assertGreater(report['reduction_levels'],0)
        self.assertEqual(len(list(batches(notes))),1)

    def test_web_full_summary_survives_new_connections(self):
        target = {'db_root':self.tmp.name,'chat_id':'peer'}
        data = {'context':web.context_id(target),'question':'今天主要聊了什么'}
        def model(config,system,payload,**kwargs):
            refs=payload['evidence']
            return '主题。['+refs[0]['id']+']' if 'id' in refs[0] else '全日总结。[M1]'
        with patch.object(rag,'runtime_lock',contextlib.nullcontext), \
             patch.object(rag,'load_runtime',return_value=(target,None)), \
             patch.object(rag,'store_path',return_value=self.store.path), \
             patch.object(rag,'model_completion',side_effect=model):
            initial = web.query('dialog-history',data)
            data['session'] = initial['dialog_id']
            web.query('ask',data)
            restored = web.query('dialog-history',data)['turns']
        self.assertEqual(len(restored),1)
        self.assertEqual(len(restored[0]['sources']),216)
        self.assertEqual(restored[0]['query_context']['retrieval_mode'],'full_summary')

    def test_reopening_store_does_not_release_another_connections_file_lock(self):
        # A second process (the watcher) may close its last connection while an
        # ask is still open. It must not delete the ask's live WAL underneath it.
        with self.store.db:self.store.set_meta('before',1)
        other = rag.Store(self.store.path)
        other.close()
        subprocess.run([sys.executable,'-c',
            'import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);'
            'db.execute("SELECT count(*) FROM metadata").fetchone();db.close()',
            str(self.store.path)],check=True,capture_output=True)
        with self.store.db:self.store.set_meta('after',2)
        reopened = rag.Store(self.store.path)
        try:self.assertEqual(reopened.meta('after'),2)
        finally:reopened.close()

    def test_bad_batch_citation_retries_without_reusing_prior_subset_ids(self):
        refs = [{'id':f'M{i}','text':'原文','message_type':'文字','content_recognized':True} for i in range(1,122)]
        calls = []
        def complete(system,payload):
            calls.append(payload)
            if len(calls)==1:return '错误的引用。[M9999]'
            return '阅读结果。['+payload['allowed_source_ids'][0]+']'
        notes,report = summary_inputs(refs,'总结今天',{'resolved_question':'旧答案 M9999'},complete)
        self.assertEqual(report['model_read_messages'],121)
        self.assertTrue(calls[1]['retry_invalid_citations'])
        self.assertNotIn('resolved_question',calls[0]['query_context'])
        self.assertEqual(len(notes),2)


if __name__=='__main__':unittest.main()
