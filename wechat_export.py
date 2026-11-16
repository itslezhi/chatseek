"""Complete local transcripts, streamed to a temporary file without model calls."""
import datetime as dt
import html
import json
import re
import tempfile
from zoneinfo import ZoneInfo

NOTE='仅包含这台电脑可读取的微信历史；手机独有、已删除或未迁移的记录无法补齐。图片、语音、视频和文件保留消息标记，不包含媒体文件。'


def build(store, target, format):
    import wechat_rag as rag
    if format not in ('html','json','txt'):raise ValueError('不支持的导出格式。')
    name=target.get('display_name') or '聊天记录'
    safe=re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', name).strip(' .')[:80] or '聊天记录'
    filename='ChatSeek-'+safe+'-'+dt.datetime.now().strftime('%Y%m%d')+'.'+format
    output=tempfile.SpooledTemporaryFile(max_size=8*1024*1024, mode='w+b')
    def write(text):output.write(text.encode('utf-8'))
    try:
        # A single read transaction keeps count and rows consistent while sync continues.
        store.db.execute('BEGIN')
        count=store.db.execute('SELECT count(*) FROM messages').fetchone()[0]
        if format=='html':
            write('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+html.escape(name)+' · 聊天记录</title><body><h1>'+html.escape(name)+'</h1><p>'+html.escape(NOTE)+'</p><p>共 '+str(count)+' 条 · 时间为北京时间</p>')
        elif format=='txt':write(name+'\n'+NOTE+'\n共 '+str(count)+' 条 · 时间为北京时间\n\n')
        else:write('{"conversation":'+json.dumps(name,ensure_ascii=False)+',"note":'+json.dumps(NOTE,ensure_ascii=False)+',"count":'+str(count)+',"messages":[')
        first=True
        for row in store.db.execute('SELECT * FROM messages ORDER BY created_at,id'):
            row=dict(row)
            ref=rag.evidence(store,[row],full_text=True)[0]
            text=row['text'] if row['text'] is not None else '['+rag.message_label(row['kind'])+'；媒体文件未包含]'
            stamp=dt.datetime.fromtimestamp(row['created_at'],ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
            item={'id':ref['id'],'time':stamp,'sender':ref['sender'],'type':rag.message_label(row['kind']),'text':text}
            if format=='json':write(('' if first else ',')+json.dumps(item,ensure_ascii=False))
            elif format=='txt':write(stamp+' '+str(ref['sender'])+' ['+item['type']+']\n'+text+'\n\n')
            else:write('<article><h2>'+html.escape(stamp+' '+str(ref['sender']))+'</h2><small>'+html.escape(item['type'])+'</small><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+html.escape(text)+'</pre></article>')
            first=False
        write(']}\n' if format=='json' else '</body></html>' if format=='html' else '')
        output.seek(0)
        return output,filename
    except BaseException:
        output.close();raise
    finally:store.db.rollback()


def prepare(context, format):
    import wechat_rag as rag
    from wechat_web import context_id
    if format not in ('html','json','txt'):raise ValueError('不支持的导出格式。')
    with rag.runtime_lock():
        target,reader=rag.load_runtime()
        if context != context_id(target):raise ValueError('会话已改变，请刷新后重试。')
        store=rag.Store(rag.store_path(target))
        try:
            # sync() has a bounded batch size. Drain to the captured high-water mark,
            # even for conversations longer than 20,000 messages.
            goal=reader.snapshot(target['chat_id'])['cursor']
            while True:
                before=store.meta('cursor',{})
                rag.sync(store,reader,target)
                after=store.meta('cursor',{})
                if all(after.get(name,0)>=value for name,value in goal.items()):break
                if after==before:raise RuntimeError('历史同步未完成，未导出不完整记录。请检查数据库后重试。')
            return build(store,target,format)
        finally:store.close()
