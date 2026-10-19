from legacy_support import isolate
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import wechat_rag as rag
import wechat_web as web
from wechat_dialog import DialogMemory, model_history


NOW = dt.datetime(2026, 10, 17, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
CONFIG = {'DEEPSEEK_API_KEY':'test-placeholder-key'}


def completion(content):
    return io.BytesIO(json.dumps({'choices':[{'message':{'content':content}}]}).encode())


class DialogTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'chat.sqlite3'
        self.store = rag.Store(self.path)
        with self.store.db:
            self.store.set_meta('peer_id', 'peer')
            self.store.set_meta('display_name', '测试好友')
            self.store.upsert([
                {'database':'m.db','local_id':1,'server_id':1,'create_time':int(NOW.timestamp())-86400,
                 'sender_id':'peer','local_type':1,'text':'昨天聚餐已改到六点，地点在东门。'},
                {'database':'m.db','local_id':2,'server_id':2,'create_time':int(NOW.timestamp()),
                 'sender_id':'peer','local_type':1,'text':'今天完成了文章。'}])
        self.clock = patch.object(rag, 'local_now', return_value=NOW)
        self.clock.start()
        self.config = patch.object(rag, 'env_config', return_value=CONFIG)
        self.config.start()
        self.prior = [{'question':'昨天聚餐几点？','answer':'聚餐改为六点。[M1]',
                       'source_ids':['M1'],'query_context':{'date_range':rag.question_time_range('昨天')}}]

    def tearDown(self):
        self.config.stop(); self.clock.stop()
        self.store.close(); self.tmp.cleanup()

    def test_followup_resolves_topic_and_rereads_original_evidence(self):
        with patch.object(rag.urllib.request, 'urlopen', side_effect=[
                completion('{"question":"2026-10-16聚餐地点在哪？"}'),
                completion('聚餐地点是东门。[M1]')]) as model:
            result = rag.ask(self.store, '那地点呢？', history=self.prior)
        self.assertEqual(model.call_count, 2)
        rewrite = json.loads(json.loads(model.call_args_list[0].args[0].data)['messages'][1]['content'])
        answer = json.loads(json.loads(model.call_args_list[1].args[0].data)['messages'][1]['content'])
        self.assertEqual(rewrite['conversation_history'], self.prior)
        self.assertEqual(answer['question'], '那地点呢？')
        self.assertEqual(answer['evidence'][0]['text'], '昨天聚餐已改到六点，地点在东门。')
        self.assertEqual(result['query_context']['date_range']['start_date'], '2026-10-16')
        self.assertEqual(result['query_context']['memory_turns'], 1)
        self.assertEqual(result['citation_check'], 'passed')

    def test_reformatting_can_retrieve_prior_sources_without_old_answer_as_evidence(self):
        with patch.object(rag.urllib.request, 'urlopen', side_effect=[
                completion('{"question":"根据 M1 用一句话概括聚餐时间"}'),
                completion('六点聚餐。[M1]')]) as model:
            result = rag.ask(self.store, '简短一点', history=self.prior)
        payload = json.loads(json.loads(model.call_args.args[0].data)['messages'][1]['content'])
        self.assertEqual(payload['conversation_history'], self.prior)
        self.assertEqual([r['id'] for r in payload['evidence']], ['M1'])
        self.assertEqual(result['citation_check'], 'passed')

    def test_current_date_overrides_resolver_and_excludes_old_sources(self):
        with patch.object(rag.urllib.request, 'urlopen', side_effect=[
                completion('{"question":"2026-10-16聊了什么"}'),
                completion('今天完成了文章。[M2]')]):
            result = rag.ask(self.store, '今天聊了什么', history=self.prior)
        self.assertEqual(result['query_context']['date_range']['start_date'], '2026-10-17')
        self.assertEqual([r['id'] for r in result['sources']], ['M2'])

    def test_all_history_correction_cancels_old_date_and_source_ids(self):
        prior=[{'question':'今天聚餐时间在哪','answer':'今日没有依据。[M2]',
                'source_ids':['M2'],'query_context':{'date_range':rag.question_time_range('今天')}}]
        with patch.object(rag.urllib.request,'urlopen',side_effect=[
                completion('{"question":"今天聚餐时间 M2"}'),
                completion('历史记录中的聚餐改为六点。[M1]')]) as model:
            result=rag.ask(self.store,'查找所有历史聊天记录，不是今天',history=prior)
        payload=json.loads(json.loads(model.call_args.args[0].data)['messages'][1]['content'])
        self.assertIsNone(result['query_context']['date_range'])
        self.assertEqual(result['query_context']['time_scope'],'all_history')
        self.assertEqual(result['query_context']['retrieval_mode'],'vector')
        self.assertEqual(result['query_context']['available_messages'],2)
        self.assertIn('M1',[source['id'] for source in payload['evidence']])
        self.assertEqual(rag.message_references(payload['resolved_question']),[])
        self.assertEqual(result['citation_check'],'passed')

    def test_no_topic_history_request_reads_the_whole_index(self):
        with patch.object(rag.urllib.request,'urlopen',return_value=completion('历史中聊了聚餐和文章。[M1][M2]')):
            result=rag.ask(self.store,'查找所有历史聊天记录，不是今天')
        self.assertIsNone(result['query_context']['date_range'])
        self.assertEqual(result['query_context']['retrieval_mode'],'full_summary')
        self.assertEqual([source['id'] for source in result['sources']],['M1','M2'])

    def test_entire_history_summary_does_not_inherit_recent_or_today(self):
        with self.store.db:
            self.store.upsert([{'database':'m.db','local_id':3,'server_id':3,
                               'create_time':int(NOW.timestamp())-30*86400,
                               'sender_id':'peer','local_type':1,'text':'上个月完成了早期方案'}])
        for question in ('总结全部聊天记录','总结所有历史聊天，不是今天'):
            with self.subTest(question=question),patch.object(rag,'resolve_followup',return_value='总结最近今天 M2'), \
                 patch.object(rag,'model_completion',return_value='完整总结。[M3][M1][M2]'):
                result=rag.ask(self.store,question,history=self.prior)
            self.assertIsNone(result['query_context']['date_range'])
            self.assertEqual(result['query_context']['selected_messages'],3)
            self.assertEqual([source['id'] for source in result['sources']],['M3','M1','M2'])

    def test_all_history_scope_does_not_limit_message_date_to_event_date(self):
        with self.store.db:
            self.store.upsert([{'database':'m.db','local_id':1,'server_id':1,
                               'create_time':int(NOW.timestamp())-86400,'sender_id':'peer','local_type':1,
                               'text':'10月18日直播会议链接：https://example.test/meeting'}])
        with patch.object(rag,'resolve_followup',return_value='10月18日直播会议链接'), \
             patch.object(rag,'model_completion',return_value='历史记录中有该会议链接。[M1]'):
            result=rag.ask(self.store,'在所有历史中找10月18日直播会议链接',history=self.prior)
        self.assertIsNone(result['query_context']['date_range'])
        self.assertIn('M1',[source['id'] for source in result['sources']])

    def test_explicit_today_still_narrows_a_previous_all_history_query(self):
        prior=[{'question':'所有历史聊了什么','answer':'讨论了聚餐。[M1]',
                'source_ids':['M1'],'query_context':{'date_range':None,'time_scope':'all_history'}}]
        with patch.object(rag,'resolve_followup',return_value='所有历史聊了什么'), \
             patch.object(rag,'model_completion',return_value='今天完成文章。[M2]'):
            result=rag.ask(self.store,'今天聊了什么',history=prior)
        self.assertEqual(result['query_context']['time_scope'],'date_range')
        self.assertEqual([source['id'] for source in result['sources']],['M2'])

    def test_new_topic_does_not_reuse_previous_evidence_or_dates(self):
        with patch.object(rag.urllib.request, 'urlopen', return_value=completion('{"question":"恐龙化石"}')) as model:
            result = rag.ask(self.store, '恐龙化石', history=self.prior)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['sources'], [])
        self.assertIsNone(result['query_context']['date_range'])

    def test_unknown_reference_or_invalid_resolver_output_cannot_search_arbitrary_message(self):
        for output in ('{"question":"M999 的内容"}', 'not json', '{"question":42}'):
            with self.subTest(output=output), patch.object(rag.urllib.request, 'urlopen', return_value=completion(output)):
                with self.assertRaisesRegex(RuntimeError, '未能可靠理解'):
                    rag.ask(self.store, '那张呢？', history=self.prior)

    def test_history_is_persistent_and_new_dialog_stops_using_it(self):
        memory = DialogMemory(self.store)
        session = memory.session()
        memory.append(session, 0, '问题', {'answer':'旧回答','sources':[]})
        reopened = rag.Store(self.path)
        try:
            restored = DialogMemory(reopened)
            self.assertEqual(restored.session(), session)
            self.assertEqual(restored.recent(session)[0]['answer'], '旧回答')
            new_session = restored.new(session)
            self.assertNotEqual(new_session, session)
            self.assertEqual(restored.recent(new_session), [])
            with self.assertRaises(ValueError): restored.check_session(session)
            with self.assertRaises(RuntimeError): memory.append(session, 1, '迟到问题', {'answer':'迟到回答'})
        finally:
            reopened.close()

    def test_parallel_answers_do_not_silently_merge_inconsistent_histories(self):
        memory = DialogMemory(self.store); session = memory.session()
        memory.append(session, 0, '第一问', {'answer':'回答','sources':[]})
        with self.assertRaisesRegex(RuntimeError, '另一窗口'):
            memory.append(session, 0, '并行问题', {'answer':'不同上下文的回答'})
        self.assertEqual(len(memory.recent(session)), 1)

    def test_each_contact_index_has_separate_sessions_and_turns(self):
        memory = DialogMemory(self.store); session = memory.session()
        memory.append(session, 0, '私聊问题', {'answer':'私聊回答','sources':[]})
        other = rag.Store(Path(self.tmp.name)/'group.sqlite3')
        try:
            group = DialogMemory(other)
            self.assertNotEqual(group.session(), session)
            self.assertEqual(group.recent(session), [])
        finally:
            other.close()

    def test_context_budget_is_bounded_and_uses_recent_complete_turns(self):
        turns = [{'question':str(n),'answer':'答'*4000,'sources':[{'id':'M1'}]} for n in range(12)]
        selected = model_history(turns)
        self.assertLessEqual(len(selected), 6)
        self.assertLessEqual(len(json.dumps(selected,ensure_ascii=False)), 24000)
        self.assertEqual(selected[-1]['question'], '11')

    def test_web_restores_server_history_and_failed_asks_are_not_remembered(self):
        target = {'db_root':self.tmp.name,'chat_id':'peer'}
        data = {'context':web.context_id(target)}
        with patch.object(rag,'runtime_lock',contextlib.nullcontext), \
             patch.object(rag,'load_runtime',return_value=(target,None)), \
             patch.object(rag,'store_path',return_value=self.path):
            restored = web.query('dialog-history', data)
            data['session'] = restored['dialog_id']
            with patch.object(rag,'ask',return_value={'answer':'六点。[M1]','sources':[{'id':'M1'}]}) as ask:
                web.query('ask', {**data,'question':'聚餐时间'})
                ask.assert_called_once()
                self.assertEqual(ask.call_args.kwargs['history'], [])
            with patch.object(rag,'ask',side_effect=RuntimeError('模型超时')) as ask:
                with self.assertRaises(RuntimeError): web.query('ask',{**data,'question':'那地点呢'})
                self.assertEqual(ask.call_args.kwargs['history'][0]['question'], '聚餐时间')
            self.assertEqual(len(web.query('dialog-history',data)['turns']), 1)
            new = web.query('dialog-new',data)
            with patch.object(rag,'ask') as ask:
                with self.assertRaises(ValueError):web.query('ask',{**data,'question':'旧窗口'})
                ask.assert_not_called()
            self.assertEqual(new['turns'], [])


if __name__ == '__main__':
    unittest.main()
