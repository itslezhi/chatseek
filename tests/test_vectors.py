import contextlib
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from zoneinfo import ZoneInfo

import wechat_rag as rag
import wechat_vectors as vectors

NOW=dt.datetime(2026,10,19,12,tzinfo=ZoneInfo('Asia/Shanghai'))

class Encoder:
    """Deterministic semantic fixture; real pretrained-model eval runs separately."""
    version=vectors.VERSION
    calls=[]
    def tokens(self,text):return list(text)
    def split(self,text,budget=360):
        for i in range(0,len(text),budget):yield text[i:i+budget]
    def encode(self,texts,*,query=False):
        self.calls.extend(texts)
        output=[]
        for text in texts:
            v=np.array([float(any(word in text for word in words)) for words in [
                ('投简历','跳槽'),('东门','聚餐'),('旅行','出游'),('恐龙','化石')]],dtype='float32')
            if not v.any():v[-1]=.01
            output.append(v/np.linalg.norm(v))
        return np.stack(output)

class VectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.private=Path(self.tmp.name)
        self.store=rag.Store(self.private/'chat.sqlite3')
        self.encoder=Encoder();self.encoder.calls=[]
        self.mock=patch.object(vectors,'get_encoder',return_value=self.encoder);self.mock.start();self.addCleanup(self.mock.stop)
        with self.store.db:
            self.store.upsert([self.row(1,'准备投简历',days=-10),self.row(2,'今晚聚餐，在东门',days=0),self.row(3,'明天去旅行',days=1)])
    def row(self,n,text,days=0):
        return {'database':'fixture.db','local_id':n,'server_id':n,'create_time':int(NOW.timestamp())+days*86400,
                'sender_id':'peer','local_type':1,'text':text}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_semantic_match_with_no_lexical_query_overlap_and_no_keyword_call(self):
        with patch.object(self.store,'retrieve',side_effect=AssertionError('pure vector called keyword search')), \
             patch.object(self.store,'keyword_hits',side_effect=AssertionError('pure vector called keyword search')):
            rows,report=vectors.retrieve(self.store,'有跳槽打算吗',self.private)
        self.assertEqual([row['id'] for row in rows],[1]);self.assertEqual(report['keyword_candidates'],0)
        self.assertTrue(vectors.status(self.store)['ready'])
        self.assertTrue((vectors.folder(self.store,self.private)/'vectors.faiss').exists())
    def test_same_model_encodes_both_corpus_and_question(self):
        vectors.retrieve(self.store,'聚餐地点',self.private)
        self.assertTrue(any('今晚聚餐，在东门' in text for text in self.encoder.calls))
        self.assertIn('聚餐地点',self.encoder.calls)
    def test_date_filter_precedes_top_k_and_context_does_not_cross_it(self):
        with self.store.db:self.store.upsert([self.row(4,'今晚聚餐，在东门',days=-1)])
        rows,report=vectors.retrieve(self.store,'聚餐地点',self.private,period=rag.question_time_range('今天',NOW),limit=1)
        self.assertEqual([row['id'] for row in rows],[2]);self.assertEqual(report['eligible_chunks'],1)
    def test_incremental_update_reuses_unchanged_embeddings_and_removes_stale(self):
        vectors.ensure(self.store,self.private);count=len(self.encoder.calls)
        vectors.ensure(self.store,self.private);self.assertEqual(len(self.encoder.calls),count)
        with self.store.db:self.store.upsert([self.row(1,'今晚聚餐，在东门',days=-10)])
        self.assertFalse(vectors.status(self.store)['ready'])
        vectors.ensure(self.store,self.private);self.assertEqual(len(self.encoder.calls)-count,1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM rag_chunks').fetchone()[0],3)
        with self.store.db:self.store.db.execute('DELETE FROM messages WHERE id=1')
        vectors.ensure(self.store,self.private)
        self.assertFalse(any(1 in json.loads(row[0]) for row in self.store.db.execute('SELECT message_ids FROM rag_chunks')))
    def test_failed_embedding_keeps_previous_generation_unpublished(self):
        vectors.ensure(self.store,self.private)
        state=self.store.db.execute('SELECT revision FROM rag_state').fetchone()[0]
        with self.store.db:self.store.upsert([self.row(1,'换一段话',days=-10)])
        with patch.object(self.encoder,'encode',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):vectors.ensure(self.store,self.private)
        self.assertEqual(self.store.db.execute('SELECT revision FROM rag_state').fetchone()[0],state)
        self.assertFalse(vectors.status(self.store)['ready'])
    def test_media_description_updates_trigger_reembedding(self):
        vectors.ensure(self.store,self.private)
        with self.store.db:self.store.db.execute("INSERT INTO media_results(message_id,assets,scope,labels,description,model) VALUES(1,'[]','缩略图','[]','画面中有人在东门','fixture')")
        self.assertFalse(vectors.status(self.store)['ready'])
        vectors.ensure(self.store,self.private)
        self.assertTrue(any('画面中有人在东门' in text for text in self.encoder.calls))
    def test_contacts_are_isolated(self):
        other=rag.Store(self.private/'other.sqlite3')
        try:
            rows,_=vectors.retrieve(other,'跳槽',self.private)
            self.assertEqual(rows,[])
            self.assertNotEqual(vectors.folder(other,self.private),vectors.folder(self.store,self.private))
        finally:other.close()
    def test_hybrid_has_independent_lexical_recall(self):
        rows,report=vectors.retrieve(self.store,'东门',self.private,mode='hybrid')
        self.assertIn(2,[row['id'] for row in rows]);self.assertGreater(report['keyword_candidates'],0)
    def test_long_message_tail_preserved_and_chunks_stay_within_budget(self):
        text='重复文本'*800+'最后：准备投简历'
        rows=[{'id':1,'sender':'peer','created_at':int(NOW.timestamp()),'text':text,'description':None}]
        chunks=list(vectors.pieces(rows,self.encoder))
        self.assertIn('最后：准备投简历',chunks[-1]['text'])
        self.assertTrue(all(len(self.encoder.tokens(chunk['text']))<=400 for chunk in chunks))
        with self.store.db:self.store.upsert([self.row(5,text,days=2)])
        vectors.ensure(self.store,self.private)
        self.assertTrue(vectors.status(self.store)['ready'])
        self.assertEqual(self.store.db.execute('SELECT text FROM messages WHERE local_id=5').fetchone()[0],text)
    def test_build_never_publishes_changed_source_revision(self):
        original=self.encoder.encode
        def race(texts,**kwargs):
            with self.store.db:self.store.upsert([self.row(1,'准备投简历'+str(len(self.encoder.calls)),days=-10)])
            return original(texts,**kwargs)
        with patch.object(self.encoder,'encode',side_effect=race),self.assertRaisesRegex(RuntimeError,'持续更新'):
            vectors.ensure(self.store,self.private)
        self.assertIsNone(self.store.db.execute('SELECT * FROM rag_state').fetchone())

if __name__=='__main__':unittest.main()
