import datetime as dt
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import wechat_query as query
import wechat_rag as rag

NOW=dt.datetime(2026,10,19,12,tzinfo=ZoneInfo('Asia/Shanghai'))

def plan(**updates):
    return {'intent':'search','queries':['查找会议链接'],'time':{'mode':'all'},'message_ids':[],**updates}

class QueryPlanTests(unittest.TestCase):
    def test_model_receives_full_question_history_and_clock(self):
        with patch.object(rag,'model_completion',return_value=json.dumps(plan())) as model:
            result=query.plan_question('那会议链接呢',[{'question':'讨论会议'}],{},NOW)
        self.assertEqual(result['intent'],'search')
        self.assertIn('current_time',model.call_args.args[2]);self.assertTrue(model.call_args.kwargs['json_mode'])
    def test_explicit_all_history_clears_inherited_date_and_ids(self):
        old={'intent':'message','message_ids':[1],'time':{'mode':'inherit'},'queries':['找会议链接']}
        history=[{'source_ids':['M1'],'query_context':{'date_range':rag.question_time_range('今天',NOW)}}]
        result=query.validate(json.dumps(plan(**old)),'查找所有历史，不是今天',history,NOW)
        self.assertIsNone(result['period']);self.assertEqual(result['message_ids'],[]);self.assertEqual(result['intent'],'search')
    def test_event_date_does_not_filter_message_date(self):
        result=query.validate(json.dumps(plan(event_date='10月18日')),'10月18日会议的链接',[],NOW)
        self.assertIsNone(result['period']);self.assertEqual(result['event_date'],'10月18日')
    def test_today_summary_and_relative_date_are_checked(self):
        value=plan(intent='summary',time={'mode':'range','start_date':'2026-10-19','end_date':'2026-10-19','evidence':'今天'})
        result=query.validate(json.dumps(value),'今天主要聊了什么',[],NOW)
        self.assertEqual(result['period']['start_date'],'2026-10-19')
        value['time']['start_date']='2026-10-18'
        with self.assertRaises(RuntimeError):query.validate(json.dumps(value),'今天主要聊了什么',[],NOW)
    def test_unquoted_invented_date_wrong_ids_and_malformed_json_are_rejected(self):
        values=['bad json',json.dumps(plan(queries=[])),json.dumps(plan(message_ids=[999])),
                json.dumps(plan(time={'mode':'range','start_date':'2026-10-18','end_date':'2026-10-18','evidence':'昨天'}))]
        for value in values:
            with self.subTest(value=value),self.assertRaises(RuntimeError):query.validate(value,'那会议链接呢',[],NOW)
    def test_same_topic_followup_can_inherit_date(self):
        period=rag.question_time_range('昨天',NOW)
        result=query.validate(json.dumps(plan(time={'mode':'inherit'})),'那地点呢',[{'query_context':{'date_range':period}}],NOW)
        self.assertEqual(result['period'],period)
    def test_explicit_id_cannot_be_replaced(self):
        result=query.validate(json.dumps(plan()),'解释M3',[],NOW)
        self.assertEqual(result['message_ids'],[3]);self.assertEqual(result['intent'],'message')
    def test_summary_does_not_require_a_semantic_search_query(self):
        result=query.validate(json.dumps(plan(intent='summary',queries=[])),'总结所有历史聊天',[],NOW)
        self.assertEqual(result['intent'],'summary');self.assertIsNone(result['period'])

if __name__=='__main__':unittest.main()
