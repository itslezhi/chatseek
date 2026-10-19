#!/usr/bin/env python3
"""Loopback-only web interface for the local WeChat RAG application."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import select
import socket
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import wechat_rag as rag
from wechat_dialog import DialogMemory, model_history

ASSETS = rag.ROOT / 'web'


def access_settings():
    try:
        mode = json.loads((rag.PRIVATE/'web-access.json').read_text()).get('mode')
    except (OSError, ValueError, AttributeError):
        mode = 'ask'
    return {'mode':mode if mode in ('ask','full') else 'ask', 'system_permission':'manual'}


def set_access_mode(mode):
    if mode not in ('ask','full'):
        raise ValueError('请选择询问请求或完全访问。')
    with rag.runtime_lock():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=rag.PRIVATE,prefix='access-',delete=False) as handle:
                temporary = rag.Path(handle.name)
                os.chmod(temporary,0o600)
                json.dump({'mode':mode},handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary,rag.PRIVATE/'web-access.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return access_settings()


def needs_access_confirmation(action, data):
    return action in ('ask','select','sync','media-preview','media-recognize','media-import','rag-index') or (action=='watch' and data.get('enabled') is True)


def open_access_settings():
    if sys.platform!='darwin':
        raise RuntimeError('当前系统不支持打开 macOS 权限设置。')
    # Open the pane only. The user must grant system permissions themselves.
    subprocess.run(['/usr/bin/open','x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'],check=True,timeout=10)
    return {'opened':True}


def context_id(target):
    return hashlib.sha256((str(rag.Path(target['db_root']).resolve())+'\0'+target['chat_id']).encode()).hexdigest()


def watch_running():
    try:
        with rag.watcher_lock():
            return False
    except RuntimeError:
        return True


def snapshot(store, target):
    from wechat_vectors import status,request_index
    vector=status(store)
    if not vector['ready'] and not vector['running'] and not vector['error']:
        request_index(store.path,rag.PRIVATE)
    return {**store.stats(), 'vector_index':vector, 'context': context_id(target), 'watch_running': watch_running(),
            'dialog_id': DialogMemory(store).session()}


def query(action, data, *, on_event=None):
    if action == 'state' and not (rag.PRIVATE/'target.json').exists():
        return {'setup_required':True}
    # Capture the selected index under the selection lock; keep cloud latency
    # outside this lock. A later selection cannot change this request's Store.
    with rag.runtime_lock():
        target, reader = rag.load_runtime()
        if action != 'state' and data.get('context') != context_id(target):
            raise ValueError('会话已改变，请刷新后重试。')
        store = rag.Store(rag.store_path(target))
    try:
        if action == 'state':
            return snapshot(store, target)
        if action == 'rag-index':
            from wechat_vectors import request_index
            request_index(store.path,rag.PRIVATE)
            return {'started':True}
        if action in ('dialog-history', 'dialog-new', 'dialog-manage', 'ask'):
            memory = DialogMemory(store)
            session = memory.check_session(data.get('session'))
            if action == 'dialog-new':
                session = memory.new(session)
            if action == 'dialog-manage':
                memory.manage(session,data.get('turn_id'),data.get('action'))
            previous_id,revision = memory.latest_id(session),memory.revision()
            turns = memory.recent(session)
            if action != 'ask':
                return {'context':context_id(target), 'dialog_id':session, 'turns':turns,
                        'archived_turns':memory.recent(session,status='archived'),
                        'deleted_turns':memory.recent(session,status='deleted')}
        if action == 'history':
            limit = int(data.get('limit', 30))
            if not 1 <= limit <= 500:
                raise ValueError('显示条数须为 1–500。')
            rows = store.db.execute('SELECT * FROM messages ORDER BY created_at DESC,id DESC LIMIT ?', (limit,)).fetchall()
            return {'sources': rag.evidence(store, [dict(r) for r in reversed(rows)]), 'context': context_id(target)}
        if action in ('media-preview','media-recognize','media-asset','media-import'):
            from wechat_media import MediaService
            service = MediaService(reader, target, store, rag.PRIVATE)
            if action == 'media-preview': result = service.prepare(data.get('id'))
            elif action == 'media-recognize': result = service.recognize(data.get('id'), rag.env_config())
            elif action == 'media-import': result = service.import_file(data.get('id'),data.get('file'))
            else: return service.asset(data.get('id'), int(data.get('index', -1)))
            return {**result, 'context': context_id(target)}
        question = data.get('question', '')
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError('请输入 1–4000 字的问题或关键词。')
        if action == 'search':
            return {'sources': rag.evidence(store, store.retrieve(question)), 'context': context_id(target)}
        if action == 'ask':
            stream_options = {'on_event':on_event} if on_event else {}
            if 'retrieval_mode' in data:stream_options['retrieval_mode']=data['retrieval_mode']
            result = rag.ask(store, question, reader=reader, target=target, history=model_history(turns), **stream_options)
            if on_event:
                # A disconnected client must not leave a partial answer in memory.
                on_event({'type':'status','message':'正在保存完整回答…'})
            turn_id=memory.append(session,previous_id,question,result,expected_revision=revision)
            return {**result, 'turn_id':turn_id, 'context': context_id(target), 'dialog_id':session}
        raise ValueError('未知操作。')
    finally:
        store.close()


class StreamDisconnected(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'WeChatMemory/1.0'

    def log_message(self, *_args):
        pass  # Request paths and user input never enter HTTP logs.

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return False
        origin = self.headers.get('Origin')
        return (not origin or origin in {'http://'+h for h in hosts}) and self.headers.get('Sec-Fetch-Site') != 'cross-site'

    def stream_answer(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-transform')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        try:
            self.end_headers()
        except OSError:
            return
        self.close_connection = True

        def emit(event):
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    raise StreamDisconnected()
                self.wfile.write((json.dumps(event, ensure_ascii=False)+'\n').encode())
                self.wfile.flush()
            except OSError:
                raise StreamDisconnected() from None

        try:
            emit({'type':'status','message':'已连接，正在准备聊天记录…'})
            result = query('ask', data, on_event=emit)
            # Only mark completion after citation checks and history persistence.
            emit({'type':'done','result':result})
        except StreamDisconnected:
            return
        except (ValueError, TypeError, UnicodeError):
            error = '输入无效或会话已切换，请刷新后重试。'
        except RuntimeError as exc:
            error = str(exc)
        except Exception:
            error = '本机读取或模型连接失败，请稍后重试。'
        else:
            return
        try:
            emit({'type':'error','error':error})
        except StreamDisconnected:
            pass

    def do_GET(self):
        if not self.trusted():
            return self.send(403, {'error': '仅允许本机同源访问。'})
        path = urlsplit(self.path).path
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/stream.js': ('stream.js', 'text/javascript; charset=utf-8'),
                  '/setup.js': ('setup.js', 'text/javascript; charset=utf-8'),
                  '/pet.js': ('pet.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/assets/chatseek-pet.png': ('assets/chatseek-pet.png', 'image/png'),
                  '/assets/chatseek-pet-body.png': ('assets/chatseek-pet-body.png', 'image/png'),
                  '/assets/chatseek-mark.svg': ('assets/chatseek-mark.svg', 'image/svg+xml')}
        if path in assets:
            filename, mime = assets[path]
            return self.send(200, (ASSETS/filename).read_bytes(), mime)
        if path == '/api/access':
            return self.send(200, access_settings())
        if path == '/api/setup':
            from chatseek_setup import status
            return self.send(200,status(rag.PRIVATE))
        if path == '/api/beta-quota':
            from chatseek_beta_client import quota
            try:
                return self.send(200,quota(rag.PRIVATE))
            except RuntimeError as exc:
                return self.send(400,{'error':str(exc)})
        if path == '/api/state':
            try:
                return self.send(200, query('state', {}))
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
                # Diagnostic codes and call sites only: never log credentials,
                # chat text, filesystem paths, or raw exception messages.
                print(json.dumps({'state_error':type(exc).__name__,
                      'errno':getattr(exc,'errno',None), 'sqlite_error':getattr(exc,'sqlite_errorname',None),
                      'calls':[frame.name+':'+str(frame.lineno) for frame in traceback.extract_tb(exc.__traceback__)]}),flush=True)
                return self.send(503, {'error': '无法读取本地配置或索引，请确认微信初始化已完成。'})
        match = re.fullmatch(r'/api/media/([a-f0-9]{64})/(M[1-9]\d*)/([0-3])', path)
        if match:
            try:
                context, message_id, index = match.groups()
                return self.send(200, query('media-asset', {'context':context,'id':message_id,'index':int(index)}), 'image/jpeg')
            except (RuntimeError, ValueError, OSError, sqlite3.Error):
                return self.send(404, {'error':'此媒体不可用或已切换会话。'})
        self.send(404, {'error': '页面不存在。'})

    def do_POST(self):
        if not self.trusted() or self.headers.get('X-WeChat-RAG') != '1':
            return self.send(403, {'error': '仅允许本机网页发起操作。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            limit = 30*1024*1024 if urlsplit(self.path).path == '/api/media-import' else 32768
            if not 0 < length <= limit:
                raise ValueError('请求大小无效。')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求格式错误。')
            action = urlsplit(self.path).path.removeprefix('/api/')
            if needs_access_confirmation(action,data) and access_settings()['mode']=='ask' and data.get('access_approved') is not True:
                return self.send(409, {'error':'请先允许本次访问，再继续操作。','access_confirmation_required':True})
            if action == 'ask' and data.get('stream') is True:
                return self.stream_answer(data)
            if action.startswith('setup-'):
                from chatseek_setup import choose_account,start_bootstrap,save_api,verify_api,check_environment,check_wechat,prepare_model
                if action=='setup-account':
                    result = choose_account(rag.PRIVATE,data.get('account'))
                elif action=='setup-bootstrap':
                    result = start_bootstrap(rag.PRIVATE,data.get('acknowledged'))
                elif action in ('setup-api','setup-verify-api'):
                    rag.PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True)
                    options={name:data[name] for name in ('base_url','model','vision_model','context_tokens','max_output_tokens','json_mode') if name in data}
                    result = (verify_api if action=='setup-verify-api' else save_api)(rag.PRIVATE,data.get('key'),**options)
                elif action=='setup-check-environment':
                    result = check_environment(rag.PRIVATE)
                elif action=='setup-check-wechat':
                    result = check_wechat(rag.PRIVATE)
                elif action=='setup-activate':
                    from chatseek_beta_client import activate
                    result = activate(rag.PRIVATE,data.get('url'),data.get('code'))
                elif action=='setup-model':
                    result = prepare_model(rag.PRIVATE)
                else:
                    return self.send(404,{'error':'接口不存在。'})
            elif action == 'access':
                result = set_access_mode(data.get('mode'))
            elif action == 'access-settings':
                result = open_access_settings()
            elif action == 'select':
                name = data.get('name')
                contact_id = data.get('id')
                if not isinstance(name, str) or len(name) > 256 or (contact_id is not None and not isinstance(contact_id, str)):
                    raise ValueError('请输入有效的联系人或群聊名字。')
                result = rag.select_contact(name, contact_id)
                if result.get('selected'):
                    result = {**result, **query('state', {})}
            elif action == 'sync':
                with rag.runtime_lock():
                    target, reader = rag.load_runtime()
                    if data.get('context') != context_id(target):
                        raise ValueError('会话已改变，请刷新后重试。')
                    store = rag.Store(rag.store_path(target))
                    try:
                        result = {**rag.sync(store, reader, target), **snapshot(store, target)}
                    finally:
                        store.close()
            elif action == 'watch':
                with rag.runtime_lock():
                    enabled = data.get('enabled')
                    if not isinstance(enabled, bool):
                        raise ValueError('同步状态参数无效。')
                    if enabled and not watch_running():
                        rag.start_watch(5)
                    elif not enabled:
                        rag.stop_watch()
                result = query('state', {})
            elif action in ('history', 'search', 'ask', 'dialog-history', 'dialog-new', 'dialog-manage', 'media-preview', 'media-recognize', 'media-import','rag-index'):
                result = query(action, data)
            else:
                return self.send(404, {'error': '接口不存在。'})
            self.send(200, result)
        except (ValueError, TypeError, UnicodeError):
            self.send(400, {'error': '输入无效或会话已切换，请检查后重试。'})
        except RuntimeError as exc:
            # RuntimeError messages in the application are controlled; native
            # SQLite and OS error strings can expose file paths and stay private.
            self.send(400, {'error': str(exc)})
        except (OSError, sqlite3.Error):
            self.send(503, {'error': '本机读取或模型连接失败，请稍后重试。'})
        except Exception:
            self.send(500, {'error': '操作未完成，请重试或检查本机配置。'})


def serve(port=8765):
    os.umask(0o077)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'微信记忆网页版：http://127.0.0.1:{server.server_address[1]}', flush=True)
    print('按 Ctrl+C 关闭网页服务。聊天后台同步由网页内开关管理。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    serve(parser.parse_args().port)
