"""Local BGE embeddings and exact FAISS cosine retrieval, scoped to one index.

SQLite is the authoritative vector cache. FAISS files are disposable snapshots;
filtered searches build an exact index from eligible cached vectors before top-k.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from zoneinfo import ZoneInfo

MODEL = 'BAAI/bge-small-zh-v1.5'
VERSION = MODEL + ':conversation-v1-400tokens-30messages-10min'
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'
_jobs_lock = threading.Lock()
_jobs = {}


def schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS rag_revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
        INSERT OR IGNORE INTO rag_revision VALUES(1,0);
        CREATE TABLE IF NOT EXISTS rag_chunks(
            id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            text TEXT NOT NULL, start_at INTEGER NOT NULL, end_at INTEGER NOT NULL,
            message_ids TEXT NOT NULL, embedding BLOB NOT NULL, version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rag_members(chunk_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
            PRIMARY KEY(chunk_id,message_id));
        CREATE INDEX IF NOT EXISTS rag_member_message ON rag_members(message_id);
        CREATE TABLE IF NOT EXISTS rag_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,version TEXT NOT NULL);
    ''')
    for table in ('messages','media_results'):
        for operation in ('INSERT','UPDATE','DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS rag_change_{table}_{operation}
                AFTER {operation} ON {table} BEGIN UPDATE rag_revision SET value=value+1 WHERE id=1; END''')
    db.commit()


def folder(store, private):
    ident=hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()
    path=Path(private)/'rag'/ident
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    return path


# Keep native model code in an isolated child. HTTP/FAISS never imports torch.
from wechat_embedding_client import LocalEncoder as _WorkerEncoder


class LocalEncoder(_WorkerEncoder):
    version=VERSION


def get_encoder(private):
    try:return LocalEncoder(private)
    except Exception:
        raise RuntimeError('本地向量模型尚未就绪，请检查模型下载或依赖；不会自动改用关键词回答。') from None


def revision(store):
    return store.db.execute('SELECT value FROM rag_revision WHERE id=1').fetchone()[0]


def status(store):
    row=store.db.execute('SELECT revision,version FROM rag_state WHERE id=1').fetchone()
    count=store.db.execute('SELECT count(*) FROM rag_chunks').fetchone()[0]
    with _jobs_lock:job=dict(_jobs.get(str(store.path.resolve()),{}))
    return {'ready':bool(row and row['revision']==revision(store) and row['version']==VERSION),
            'chunks':count,'model':MODEL,'running':job.get('running',False),
            'processed':job.get('processed',0),'total':job.get('total',0),'error':job.get('error')}


def pieces(rows,encoder):
    batch=[];last=None
    def make(items):
        text='\n'.join(item['piece'] for item in items)
        return {'fingerprint':hashlib.sha256((encoder.version+'\0'+text+'\0'+json.dumps([item['id'] for item in items])).encode()).hexdigest(),
                'text':text,'start_at':items[0]['created_at'],'end_at':items[-1]['created_at'],
                'message_ids':list(dict.fromkeys(item['id'] for item in items))}
    for row in rows:
        text=row['text'] or ''
        if row['description']:
            text+='\n[媒体识别描述，范围：'+row['scope']+'] '+row['description']
        if not text.strip():continue
        day=dt.datetime.fromtimestamp(row['created_at'],ZoneInfo('Asia/Shanghai')).date()
        for part in encoder.split(text):
            item={**row,'piece':f'[{"M"+str(row["id"])}] {row["sender"] or "未知发送者"}：{part}'}
            discontinuous=last and (day!=last[0] or row['created_at']-last[1]>600)
            candidate='\n'.join([entry['piece'] for entry in batch]+[item['piece']])
            if batch and (discontinuous or len(batch)>=30 or len(encoder.tokens(candidate))>400):
                yield make(batch)
                overlap=batch[-2:] if not discontinuous else []
                if len(encoder.tokens('\n'.join(entry['piece'] for entry in overlap+[item])))>400:overlap=[]
                batch=overlap
            batch.append(item);last=(day,row['created_at'])
    if batch:yield make(batch)


def ensure(store,private,on_progress=None):
    """Cache unchanged vectors; publish one consistent source revision at a time."""
    import numpy as np
    path=folder(store,private)
    if on_progress:on_progress('正在检查当前会话的本地向量索引…')
    with (path/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if status(store)['ready']:return status(store)
        encoder=get_encoder(private)
        computed={}
        for attempt in range(3):
            store.db.execute('BEGIN')
            source_revision=revision(store)
            rows=[dict(row) for row in store.db.execute('''SELECT m.*,r.description,r.scope FROM messages m
                LEFT JOIN media_results r ON r.message_id=m.id WHERE m.kind!=10000 ORDER BY m.created_at,m.id''')]
            cache={row['fingerprint']:bytes(row['embedding']) for row in store.db.execute('SELECT fingerprint,embedding FROM rag_chunks WHERE version=?',(encoder.version,))}
            cache.update(computed)
            store.db.commit()
            # Identical repeated portions of one long message share a vector.
            # Their complete original message is still kept in messages.
            chunks=list({chunk['fingerprint']:chunk for chunk in pieces(rows,encoder)}.values())
            missing=[chunk for chunk in chunks if chunk['fingerprint'] not in cache]
            key=str(store.path.resolve())
            with _jobs_lock:_jobs.setdefault(key,{}).update(total=len(missing),processed=0,error=None)
            for start in range(0,len(missing),16):
                group=missing[start:start+16]
                vectors=encoder.encode([chunk['text'] for chunk in group])
                if len(vectors)!=len(group) or not np.isfinite(vectors).all():raise RuntimeError('向量模型输出无效，索引未发布。')
                for chunk,vector in zip(group,vectors):
                    cache[chunk['fingerprint']]=np.asarray(vector,dtype='float32').tobytes()
                    computed[chunk['fingerprint']]=cache[chunk['fingerprint']]
                done=min(start+16,len(missing))
                with _jobs_lock:_jobs[key].update(processed=done)
                if on_progress:on_progress(f'正在建立本地向量索引：{done}/{len(missing)} 个新片段…')
            store.db.execute('BEGIN IMMEDIATE')
            if revision(store)!=source_revision:
                store.db.rollback();continue
            try:
                keep=[]
                for chunk in chunks:
                    store.db.execute('''INSERT INTO rag_chunks(fingerprint,text,start_at,end_at,message_ids,embedding,version)
                        VALUES(?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET embedding=excluded.embedding,version=excluded.version''',
                        (chunk['fingerprint'],chunk['text'],chunk['start_at'],chunk['end_at'],json.dumps(chunk['message_ids']),cache[chunk['fingerprint']],encoder.version))
                    ident=store.db.execute('SELECT id FROM rag_chunks WHERE fingerprint=?',(chunk['fingerprint'],)).fetchone()[0]
                    keep.append(ident)
                    store.db.executemany('INSERT OR IGNORE INTO rag_members VALUES(?,?)',[(ident,message) for message in chunk['message_ids']])
                store.db.execute('CREATE TEMP TABLE IF NOT EXISTS rag_keep(id INTEGER PRIMARY KEY)')
                store.db.execute('DELETE FROM rag_keep')
                store.db.executemany('INSERT INTO rag_keep VALUES(?)',[(ident,) for ident in keep])
                store.db.execute('DELETE FROM rag_members WHERE chunk_id NOT IN (SELECT id FROM rag_keep)')
                store.db.execute('DELETE FROM rag_chunks WHERE id NOT IN (SELECT id FROM rag_keep)')
                store.db.execute('INSERT OR REPLACE INTO rag_state VALUES(1,?,?)',(source_revision,encoder.version))
                store.db.commit()
            except Exception:store.db.rollback();raise
            # Export a rebuildable snapshot; SQLite remains authoritative.
            import faiss
            faiss.omp_set_num_threads(1)
            stored=list(store.db.execute('SELECT id,embedding FROM rag_chunks ORDER BY id'))
            if stored:
                matrix=np.stack([np.frombuffer(row['embedding'],dtype='float32') for row in stored])
                index=faiss.IndexIDMap2(faiss.IndexFlatIP(matrix.shape[1]));index.add_with_ids(matrix,np.array([row['id'] for row in stored],dtype='int64'))
                fd,temporary=tempfile.mkstemp(dir=path,prefix='vectors-');os.close(fd)
                try:faiss.write_index(index,temporary);os.replace(temporary,path/'vectors.faiss')
                finally:Path(temporary).unlink(missing_ok=True)
            else:(path/'vectors.faiss').unlink(missing_ok=True)
            return status(store)
        raise RuntimeError('聊天正在持续更新，向量索引尚未追上，请稍后重试。')


def request_index(path,private):
    key=str(Path(path).resolve())
    with _jobs_lock:
        if _jobs.get(key,{}).get('running'):return
        _jobs[key]={'running':True,'processed':0,'total':0,'error':None}
    def run():
        try:
            from wechat_rag import Store
            store=Store(path)
            try:ensure(store,private)
            finally:store.close()
        except Exception:
            with _jobs_lock:_jobs[key]['error']='本地向量索引未完成，可点击重建重试；不会自动切换检索模式。'
        finally:
            with _jobs_lock:_jobs[key]['running']=False
    threading.Thread(target=run,daemon=True,name='chatseek-index').start()


def retrieve(store,question,private,*,mode='vector',period=None,on_progress=None,limit=8,min_score=.38):
    if mode not in ('vector','hybrid'):raise ValueError('请选择纯向量或混合检索。')
    for _ in range(3):
        ensure(store,private,on_progress)
        store.db.execute('BEGIN')
        if not status(store)['ready']:
            store.db.rollback();continue
        try:
            return _retrieve_snapshot(store,question,private,mode=mode,period=period,limit=limit,min_score=min_score)
        finally:store.db.commit()
    raise RuntimeError('聊天正在更新，请稍后重新检索。')


def _retrieve_snapshot(store,question,private,*,mode,period,limit,min_score):
    # Vectors and original rows are read from the same WAL snapshot.
    import numpy as np
    import faiss
    faiss.omp_set_num_threads(1)
    # Filter BEFORE ranking; an out-of-range match cannot hide an in-range one.
    sql='SELECT * FROM rag_chunks'
    params=()
    if period:sql+=' WHERE start_at>=? AND end_at<?';params=(period['start'],period['end'])
    chunks=[dict(row) for row in store.db.execute(sql+' ORDER BY id',params)]
    if not chunks:return [],{'mode':mode,'eligible_chunks':0,'vector_candidates':0,'keyword_candidates':0,'selected_chunks':0}
    encoder=get_encoder(private)
    matrix=np.stack([np.frombuffer(chunk['embedding'],dtype='float32') for chunk in chunks])
    index=faiss.IndexFlatIP(matrix.shape[1]);index.add(matrix)
    query=encoder.encode([question],query=True)
    scores,indices=index.search(query,min(30,len(chunks)))
    vector=[(int(pos),float(score)) for pos,score in zip(indices[0],scores[0]) if pos>=0 and score>=min_score]
    ranks={pos:1/(60+rank) for rank,(pos,_) in enumerate(vector,1)}
    lexical=[]
    if mode=='hybrid':
        # Independent lexical recall: it never gates semantic matches.
        hits=store.keyword_hits(question,limit=30,time_range=period)
        rank_by_message={row['id']:rank for rank,row in enumerate(hits,1)}
        lexical=sorted([(pos,min(rank_by_message[ident] for ident in json.loads(chunk['message_ids']) if ident in rank_by_message))
                        for pos,chunk in enumerate(chunks) if any(ident in rank_by_message for ident in json.loads(chunk['message_ids']))],key=lambda item:item[1])
        for rank,(pos,_) in enumerate(lexical,1):ranks[pos]=ranks.get(pos,0)+1/(60+rank)
    chosen=sorted(ranks,key=lambda pos:ranks[pos],reverse=True)[:limit]
    wanted={ident for pos in chosen for ident in json.loads(chunks[pos]['message_ids'])}
    rows=[]
    if wanted:
        placeholders=','.join('?' for _ in wanted)
        rows=[dict(row) for row in store.db.execute('SELECT * FROM messages WHERE id IN ('+placeholders+') ORDER BY created_at,id',tuple(wanted))]
        selected={row['id']:row for row in rows}
        date_sql=' AND created_at>=? AND created_at<?' if period else ''
        date_args=(period['start'],period['end']) if period else ()
        for row in rows:
            for comparison,order in (('<','DESC'),('>','ASC')):
                for neighbor in store.db.execute(f'''SELECT * FROM messages WHERE kind!=10000{date_sql}
                    AND (created_at,id) {comparison} (?,?) ORDER BY created_at {order},id {order} LIMIT 2''',(*date_args,row['created_at'],row['id'])):
                    if abs(neighbor['created_at']-row['created_at'])<=3600:selected[neighbor['id']]=dict(neighbor)
        rows=sorted(selected.values(),key=lambda row:(row['created_at'],row['id']))
    return rows,{'mode':mode,'eligible_chunks':len(chunks),'vector_candidates':len(vector),'keyword_candidates':len(lexical),
                 'selected_chunks':len(chosen),'best_similarity':round(float(scores[0][0]),4),
                 'chunk_ids':[chunks[pos]['id'] for pos in chosen],'model':MODEL}
