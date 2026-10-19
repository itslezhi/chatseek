"""A monitored local model subprocess; native crashes never kill HTTP serving."""
from __future__ import annotations
import atexit
import base64
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from chatseek_runtime import child_command

_lock=threading.RLock()
_process=None
_buffer=b''
_stopping=threading.Event()


def shutdown_worker():
    """Interrupt a cold model load/read before waiting for the request lock."""
    _stopping.set()
    close_worker()


def close_worker():
    global _process,_buffer
    with _lock:
        process=_process;_process=None;_buffer=b''
        if process is None:return
        for stream in (process.stdin,process.stdout):
            if stream:
                try:stream.close()
                except OSError:pass
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)


atexit.register(close_worker)


def _read(process,timeout):
    global _buffer
    deadline=time.monotonic()+timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout,selectors.EVENT_READ)
        while b'\n' not in _buffer:
            if _stopping.is_set():raise EOFError()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            if not selector.select(min(remaining,1)):continue
            chunk=os.read(process.stdout.fileno(),65536)
            if not chunk:raise EOFError()
            _buffer+=chunk
            if len(_buffer)>64*1024*1024:raise ValueError()
    line,_buffer=_buffer.split(b'\n',1)
    value=json.loads(line)
    if not isinstance(value,dict) or 'error' in value:raise ValueError()
    return value


def _start(private):
    global _process,_buffer
    if _process is not None and _process.poll() is None:return _process
    close_worker()
    env=os.environ.copy()
    for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        env[name]='1'
    env['TOKENIZERS_PARALLELISM']='false'
    env.pop('DEEPSEEK_API_KEY',None)
    env.pop('LLM_API_KEY',None)
    # Only cache location is in argv. No questions, chats or API credentials.
    _process=subprocess.Popen(child_command('worker',str(Path(private).resolve()/'models')),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,env=env,bufsize=0)
    _buffer=b''
    if _read(_process,300).get('ready') is not True:raise ValueError()
    return _process


def request(private,payload):
    with _lock:
        try:
            if _stopping.is_set():raise EOFError()
            process=_start(private)
            # bufsize=0 pipes can perform short writes: loop until all bytes land.
            data=memoryview((json.dumps(payload,ensure_ascii=False)+'\n').encode())
            while data:
                sent=process.stdin.write(data)
                if not sent:raise BrokenPipeError()
                data=data[sent:]
            return _read(process,300)
        except (OSError,EOFError,TimeoutError,ValueError,subprocess.SubprocessError) as exc:
            print(json.dumps({'embedding_worker_error':type(exc).__name__,
                              'exit_code':_process.poll() if _process is not None else None}),flush=True)
            close_worker()
            raise RuntimeError('本地向量计算进程中断或未就绪，网页服务仍在运行；请稍后重试。') from None


class LocalEncoder:
    def __init__(self,private):self.private=Path(private)
    def tokens(self,text):return request(self.private,{'action':'tokens','text':text})['tokens']
    def split(self,text,budget=360):
        while text:
            if len(self.tokens(text))<=budget:
                yield text;break
            low,high=1,len(text)
            while low<high:
                middle=(low+high+1)//2
                if len(self.tokens(text[:middle]))<=budget:low=middle
                else:high=middle-1
            yield text[:low];text=text[low:]
    def encode(self,texts,*,query=False):
        import numpy as np
        result=request(self.private,{'action':'encode','texts':texts,'query':query})
        try:
            shape=tuple(result['shape'])
            if len(shape)!=2 or shape[0]!=len(texts) or shape[1]!=512:raise ValueError()
            matrix=np.frombuffer(base64.b64decode(result['data'],validate=True),dtype='float32').reshape(shape).copy()
            if not np.isfinite(matrix).all():raise ValueError()
            return matrix
        except (KeyError,TypeError,ValueError):
            raise RuntimeError('本地向量计算返回无效结果，请重试。') from None
