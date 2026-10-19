#!/usr/bin/env python3
"""Single-instance beta gateway. Run behind HTTPS; admin actions are local CLI only."""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Denied(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO config VALUES('seats',20),('tokens',100000000);
                CREATE TABLE IF NOT EXISTS invites(
                    id INTEGER PRIMARY KEY,code_hash TEXT UNIQUE NOT NULL,label TEXT NOT NULL,
                    days INTEGER NOT NULL,call_limit INTEGER NOT NULL,daily_limit INTEGER NOT NULL,
                    token_limit INTEGER NOT NULL,device_hash TEXT,token_hash TEXT UNIQUE,
                    activated_at REAL,expires_at REAL,revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS calls(
                    id INTEGER PRIMARY KEY,invite_id INTEGER NOT NULL,created_at REAL NOT NULL,
                    day TEXT NOT NULL,reserved INTEGER NOT NULL,charged INTEGER NOT NULL,
                    actual_tokens INTEGER,status TEXT NOT NULL DEFAULT 'pending');
                CREATE INDEX IF NOT EXISTS calls_invite ON calls(invite_id,day);
            ''')
        os.chmod(self.path,0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path,timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def issue(self, label, days=3, calls=200, daily=100, tokens=5000000):
        if not all(isinstance(x,int) and x>0 for x in (days,calls,daily,tokens)):
            raise ValueError('limits must be positive integers')
        code = 'CS-'+secrets.token_urlsafe(24)
        with self.connect() as db:
            db.execute('INSERT INTO invites(code_hash,label,days,call_limit,daily_limit,token_limit) VALUES(?,?,?,?,?,?)',
                       (digest(code),label,days,calls,daily,tokens))
        return code

    def activate(self, code, device):
        if not isinstance(code,str) or not isinstance(device,str) or not 20<=len(device)<=128:
            raise Denied(401,'invalid_activation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM invites WHERE code_hash=?',(digest(code),)).fetchone()
            if row is None:
                raise Denied(401,'invalid_invite')
            now = time.time()
            if row['revoked'] or (row['expires_at'] is not None and row['expires_at']<=now):
                raise Denied(403,'expired_or_revoked')
            if row['device_hash'] and row['device_hash']!=digest(device):
                raise Denied(403,'device_bound')
            if not row['activated_at']:
                seats = db.execute("SELECT value FROM config WHERE key='seats'").fetchone()[0]
                used = db.execute('SELECT count(*) FROM invites WHERE activated_at IS NOT NULL').fetchone()[0]
                if used>=seats:
                    raise Denied(409,'seats_full')
            token = secrets.token_urlsafe(48)
            activated = row['activated_at'] or now
            db.execute('UPDATE invites SET device_hash=?,token_hash=?,activated_at=?,expires_at=? WHERE id=?',
                       (digest(device),digest(token),activated,activated+row['days']*86400,row['id']))
        return {'token':token,'quota':self.quota(token)}

    @staticmethod
    def authorized(db, token):
        row = db.execute('SELECT * FROM invites WHERE token_hash=?',(digest(token),)).fetchone()
        if row is None:
            raise Denied(401,'invalid_token')
        if row['revoked'] or row['expires_at']<=time.time():
            raise Denied(403,'expired_or_revoked')
        return row

    @staticmethod
    def day():
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()

    def quota(self, token):
        with self.connect() as db:
            row = self.authorized(db,token)
            count,total = db.execute('SELECT count(*),coalesce(sum(charged),0) FROM calls WHERE invite_id=?',(row['id'],)).fetchone()
            daily = db.execute('SELECT count(*) FROM calls WHERE invite_id=? AND day=?',(row['id'],self.day())).fetchone()[0]
            return {'id':row['id'],'calls_remaining':max(0,row['call_limit']-count),
                    'daily_remaining':max(0,row['daily_limit']-daily),
                    'tokens_remaining':max(0,row['token_limit']-total),
                    'expires_at':dt.datetime.fromtimestamp(row['expires_at'],dt.timezone.utc).isoformat(),
                    'unit':'model_request','timezone':'Asia/Shanghai'}

    def reserve(self, token, amount):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.authorized(db,token)
            count,total = db.execute('SELECT count(*),coalesce(sum(charged),0) FROM calls WHERE invite_id=?',(row['id'],)).fetchone()
            daily = db.execute('SELECT count(*) FROM calls WHERE invite_id=? AND day=?',(row['id'],self.day())).fetchone()[0]
            global_total = db.execute('SELECT coalesce(sum(charged),0) FROM calls').fetchone()[0]
            global_limit = db.execute("SELECT value FROM config WHERE key='tokens'").fetchone()[0]
            if count>=row['call_limit'] or daily>=row['daily_limit'] or total+amount>row['token_limit'] or global_total+amount>global_limit:
                raise Denied(429,'quota_exhausted')
            # One request per invite and at most four globally. Expired pending
            # reservations still consume budget, but cease holding concurrency.
            threshold = time.time()-360
            pending = db.execute("SELECT invite_id FROM calls WHERE status='pending' AND created_at>?",(threshold,)).fetchall()
            if any(r[0]==row['id'] for r in pending) or len(pending)>=4:
                raise Denied(409,'request_in_progress')
            cur = db.execute('INSERT INTO calls(invite_id,created_at,day,reserved,charged) VALUES(?,?,?,?,?)',
                             (row['id'],time.time(),self.day(),amount,amount))
            return cur.lastrowid

    def settle(self, call_id, actual=None):
        with self.connect() as db:
            row = db.execute('SELECT reserved FROM calls WHERE id=?',(call_id,)).fetchone()
            # Missing/invalid usage or network failures keep the reservation.
            valid = isinstance(actual,int) and not isinstance(actual,bool) and actual>0
            db.execute('UPDATE calls SET charged=?,actual_tokens=?,status=? WHERE id=?',
                       (actual if valid else row[0],actual if valid else None,'complete' if valid else 'uncertain',call_id))


def validated_body(body):
    if not isinstance(body,dict) or body.get('model') not in ('deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'):
        raise Denied(400,'unsupported_model')
    messages = body.get('messages')
    if not isinstance(messages,list) or not 1<=len(messages)<=32:
        raise Denied(400,'invalid_messages')
    images = 0
    text_bytes = 0
    for message in messages:
        if not isinstance(message,dict) or message.get('role') not in ('system','user','assistant'):
            raise Denied(400,'invalid_role')
        content = message.get('content')
        if isinstance(content,str):
            text_bytes += len(content.encode())
        elif isinstance(content,list):
            for item in content:
                if not isinstance(item,dict):
                    raise Denied(400,'invalid_content')
                if item.get('type')=='text' and isinstance(item.get('text'),str):
                    text_bytes += len(item['text'].encode())
                elif item.get('type')=='image_url' and isinstance(item.get('image_url'),dict) and isinstance(item['image_url'].get('url'),str) and item['image_url']['url'].startswith('data:image/jpeg;base64,'):
                    images += 1
                else:
                    raise Denied(400,'invalid_content')
        else:
            raise Denied(400,'invalid_content')
    max_output = body.get('max_tokens',4096)
    if type(max_output) is not int or not 1<=max_output<=8192 or type(body.get('stream',False)) is not bool:
        raise Denied(400,'invalid_options')
    if text_bytes>192000 or images>4:
        raise Denied(413,'materials_too_large')
    # Fixed options prevent clients from enabling expensive reasoning/tools/n.
    result = {'model':body['model'],'messages':messages,'max_tokens':max_output,
              'stream':False,'thinking':{'type':'disabled'}}
    if body.get('response_format')=={'type':'json_object'}:
        result['response_format']={'type':'json_object'}
    # Text uses a conservative byte bound plus protocol overhead. Image usage
    # varies by provider; reserve the full 1M context for requests with images.
    reserved = (1000000 if images else text_bytes+8192)+max_output
    return result,reserved


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(330)

    def log_message(self,*args):
        pass

    def send(self,status,body,kind='application/json'):
        value = body if isinstance(body,bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(value)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(value)

    def token(self):
        auth = self.headers.get('Authorization','')
        if not auth.startswith('Bearer ') or len(auth)>256:
            raise Denied(401,'missing_token')
        return auth[7:]

    def do_GET(self):
        try:
            if self.path=='/health':
                return self.send(200,{'ok':True})
            if self.path=='/v1/quota':
                return self.send(200,self.server.ledger.quota(self.token()))
            raise Denied(404,'not_found')
        except Denied as exc:
            self.send(exc.status,{'error':exc.message})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length','0'))
            limit = 4096 if self.path=='/v1/activate' else 8*1024*1024
            if not 0<length<=limit:
                raise Denied(413,'request_too_large')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise Denied(400,'invalid_json')
            if self.path=='/v1/activate':
                return self.send(200,self.server.ledger.activate(body.get('code'),body.get('device')))
            if self.path!='/v1/chat/completions':
                raise Denied(404,'not_found')
            upstream,reservation = validated_body(body)
            call_id = self.server.ledger.reserve(self.token(),reservation)
            actual = None
            try:
                req = urllib.request.Request('https://api.deepseek.com/chat/completions',
                    data=json.dumps(upstream).encode(),headers={'Authorization':'Bearer '+self.server.api_key,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=300) as response:
                    raw = response.read(16*1024*1024)
                    result = json.loads(raw)
                actual = result.get('usage',{}).get('total_tokens')
                choice = result['choices'][0]
                if not isinstance(choice['message'].get('content'),str):
                    raise ValueError()
            except (OSError,ValueError,KeyError,TypeError,IndexError):
                raise Denied(502,'upstream_failed') from None
            finally:
                self.server.ledger.settle(call_id,actual)
            if body.get('stream'):
                # Beta gateway buffers the provider result so usage is settled
                # before delivery, then emits the existing client's SSE protocol.
                event = {'choices':[{'delta':{'content':choice['message']['content']},'finish_reason':None}]}
                end = {'choices':[{'delta':{},'finish_reason':choice.get('finish_reason','stop')}]}
                value = ('data: '+json.dumps(event)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()
                return self.send(200,value,'text/event-stream')
            self.send(200,raw)
        except Denied as exc:
            self.send(exc.status,{'error':exc.message})
        except (ValueError,TypeError,UnicodeError):
            self.send(400,{'error':'invalid_request'})
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            self.send(503,{'error':'gateway_unavailable'})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path('gateway-private/usage.sqlite3'))
    subs = parser.add_subparsers(dest='command',required=True)
    serve = subs.add_parser('serve')
    serve.add_argument('--port',type=int,default=8787)
    issue = subs.add_parser('issue')
    issue.add_argument('--label',required=True)
    for flag,default in (('days',3),('calls',200),('daily',100),('tokens',5000000)):
        issue.add_argument('--'+flag,type=int,default=default)
    subs.add_parser('list')
    revoke = subs.add_parser('revoke');revoke.add_argument('id',type=int)
    limits = subs.add_parser('limits')
    limits.add_argument('--seats',type=int,required=True)
    limits.add_argument('--tokens',type=int,required=True)
    args = parser.parse_args()
    ledger = Ledger(args.db)
    if args.command=='issue':
        print(ledger.issue(args.label,args.days,args.calls,args.daily,args.tokens))
    elif args.command=='list':
        with ledger.connect() as db:
            print(json.dumps([dict(row) for row in db.execute('''SELECT i.id,i.label,i.activated_at,i.expires_at,i.revoked,
                i.call_limit,i.daily_limit,i.token_limit,count(c.id) AS calls_used,coalesce(sum(c.charged),0) AS tokens_charged,
                coalesce(sum(c.actual_tokens),0) AS reported_tokens FROM invites i LEFT JOIN calls c ON c.invite_id=i.id GROUP BY i.id''')],ensure_ascii=False,indent=2))
    elif args.command=='revoke':
        with ledger.connect() as db:
            db.execute('UPDATE invites SET revoked=1 WHERE id=?',(args.id,))
    elif args.command=='limits':
        if args.seats<1 or args.tokens<1:
            parser.error('limits must be positive')
        with ledger.connect() as db:
            db.execute("UPDATE config SET value=? WHERE key='seats'",(args.seats,))
            db.execute("UPDATE config SET value=? WHERE key='tokens'",(args.tokens,))
    else:
        key = os.environ.get('DEEPSEEK_API_KEY')
        if not key:
            parser.error('set DEEPSEEK_API_KEY on the server')
        server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        server.ledger,server.api_key = ledger,key
        server.serve_forever()


if __name__=='__main__':
    main()
