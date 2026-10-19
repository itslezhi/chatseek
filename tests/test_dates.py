from legacy_support import isolate
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import wechat_rag as app

ZONE=ZoneInfo('Asia/Shanghai')
NOW=dt.datetime(2026,10,16,12,tzinfo=ZONE)


def message(number,when,text,kind=1):
    return {'database':'message_0.db','local_id':number,'server_id':number*10,
            'create_time':int(dt.datetime.fromisoformat(when).replace(tzinfo=ZONE).timestamp()),
            'sender_id':'peer','local_type':kind,'text':text}


class DateTests(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.tmp=tempfile.TemporaryDirectory()
        self.store=app.Store(Path(self.tmp.name)/'index.sqlite3')
        self.clock=patch.object(app,'local_now',return_value=NOW);self.clock.start()
        self.rows=[message(1,'2026-10-14T12:00:00','讨论报价'),
                   message(2,'2026-10-15T23:59:59','昨天的安排'),
                   message(3,'2026-10-16T00:00:00','已完成文章'),
                   message(4,'2026-10-16T00:01:00',None,3),
                   message(5,'2026-10-16T23:59:59','健身计划'),
                   message(6,'2026-10-17T00:00:00','明天记录')]
        with self.store.db:
            self.store.upsert(self.rows)
            self.store.set_meta('peer_id','peer');self.store.set_meta('display_name','测试会话')

    def tearDown(self):
        self.clock.stop();self.store.close();self.tmp.cleanup()

    def ids(self,question):
        return [row['id'] for row in self.store.retrieve(question)]

    def test_today_summary_uses_only_today_including_midnight(self):
        self.assertEqual(self.ids('今天聊了什么'),[3,4,5])
        self.assertEqual(self.ids('今天主要完成了什么'),[3,4,5])

    def test_keyword_media_and_context_never_cross_day_boundary(self):
        self.assertEqual(self.ids('今天图片'),[3,4])
        self.assertTrue(set(self.ids('今天健身')).issubset({3,4,5}))
        self.assertEqual(self.ids('今天 M2 的内容'),[])
        self.assertEqual(self.ids('今天 M3 的内容'),[3])

    def test_yesterday_day_before_and_explicit_date(self):
        self.assertEqual(self.ids('昨天聊了什么'),[2])
        self.assertEqual(self.ids('前天主要完成什么'),[1])
        self.assertEqual(self.ids('总结2026-10-14的聊天'),[1])
        self.assertEqual(self.ids('10月14日聊了什么'),[1])

    def test_beijing_date_is_independent_of_utc_calendar_day(self):
        period=app.question_time_range('今天',dt.datetime(2026,10,15,16,5,tzinfo=dt.timezone.utc))
        self.assertEqual(period['start_date'],'2026-10-16')
        self.assertEqual(period['end']-period['start'],86400)

    def test_recent_days_week_and_invalid_date(self):
        self.assertEqual(self.ids('最近3天聊了什么'),[1,2,3,4,5])
        self.assertEqual(app.question_time_range('本周')['start_date'],'2026-10-12')
        self.assertEqual(app.question_time_range('上周')['end_date_exclusive'],'2026-10-12')
        self.assertIsNone(app.question_time_range('报价10-20元是什么意思'))
        with self.assertRaises(ValueError):app.question_time_range('2026-02-30聊了什么')

    def test_all_history_correction_does_not_match_rejected_today(self):
        for question in ('查找所有历史聊天记录，不是今天','总结所有历史，不要只看今天',
                         '会议链接，不限日期','不要仅看今天，查询所有历史',
                         '总结全部聊天记录','读取历史聊天记录','不要只看今天','别仅看今天'):
            with self.subTest(question=question):
                self.assertIsNone(app.question_time_range(question))
                self.assertTrue(app.all_history_scope(question))
        self.assertEqual(self.ids('查找所有历史聊天记录，不是今天'),[1,2,3,4,5,6])

    def test_rejected_date_is_not_a_filter_and_new_date_still_works(self):
        self.assertEqual(app.question_time_range('不是今天，是昨天')['start_date'],'2026-10-15')
        self.assertEqual(app.question_time_range('不是昨天，是今天')['start_date'],'2026-10-16')
        self.assertEqual(app.question_time_range('总结今天所有聊天记录')['start_date'],'2026-10-16')
        self.assertEqual(app.question_time_range('查询历史聊天记录中10月14日的讨论')['start_date'],'2026-10-14')

    def test_explicit_no_date_scope_is_not_reparsed_by_store(self):
        self.assertEqual([row['id'] for row in self.store.retrieve('总结今天聊天',time_range=None)],[1,2,3,4,5,6])
        self.assertEqual(self.ids('总结今天聊天'),[3,4,5])

    def test_no_today_records_returns_local_answer_without_model_or_old_sources(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM messages WHERE id>=3')
        with patch.object(app,'env_config',return_value={'DEEPSEEK_API_KEY':'dummy-test-placeholder'}), \
             patch.object(app.urllib.request,'urlopen') as model:
            result=app.ask(self.store,'今天主要完成了什么')
        model.assert_not_called()
        self.assertFalse(result['api_called']);self.assertEqual(result['sources'],[])
        self.assertIn('2026-10-16',result['answer']);self.assertIn('2026-10-15',result['answer'])
        self.assertEqual(result['query_context']['available_messages'],0)
        self.assertEqual(result['media_processing']['attempted'],[])

    def test_current_time_and_date_scope_are_sent_to_answer_model(self):
        response=json.dumps({'choices':[{'message':{'content':'已完成文章。[M3]'}}]}).encode()
        with patch.object(app,'env_config',return_value={'DEEPSEEK_API_KEY':'dummy-test-placeholder'}), \
             patch.object(app.urllib.request,'urlopen',return_value=io.BytesIO(response)) as model:
            result=app.ask(self.store,'今天 M3 完成了什么')
        sent=json.loads(json.loads(model.call_args.args[0].data)['messages'][1]['content'])
        self.assertEqual(sent['query_context']['current_time'],'2026-10-16T12:00:00+08:00')
        self.assertEqual(sent['query_context']['date_range']['start_date'],'2026-10-16')
        self.assertEqual([r['id'] for r in sent['evidence']],['M3'])
        self.assertEqual(result['query_context']['selected_messages'],1)
        self.assertEqual(result['query_context']['available_messages'],3)


if __name__=='__main__':unittest.main()
