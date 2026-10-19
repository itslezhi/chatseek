#!/usr/bin/env python3
"""Exercise a built app using temporary data and optional cached public weights."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,default=ROOT/'dist/ChatSeek.app')
    parser.add_argument('--model-cache',type=Path,help='Public model cache only, never a chat database')
    args=parser.parse_args()
    binary=args.app.resolve()/'Contents/Resources/runtime/ChatSeekRuntime'
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(args.app)],check=True)
    core=json.loads(subprocess.check_output([str(binary),'--chatseek-self-test'],text=True))
    with tempfile.TemporaryDirectory(prefix='chatseek-package-check-') as folder:
        private=Path(folder)
        env=dict(os.environ,CHATSEEK_DATA_DIR=folder)
        env.pop('DEEPSEEK_API_KEY',None)
        # Exercise the exact frozen multiprocessing helper entry that otherwise
        # falls through into the desktop launcher. No browser should be opened.
        env['BROWSER']='/usr/bin/true'
        read_fd,write_fd=os.pipe()
        helper=subprocess.Popen([str(binary),'-B','-S','-I','-c',
            f'from multiprocessing.resource_tracker import main;main({read_fd})'],
            env=env,pass_fds=(read_fd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.close(read_fd);os.close(write_fd)
        try:
            assert helper.wait(timeout=15)==0
        finally:
            if helper.poll() is None:helper.terminate();helper.wait(timeout=10)
        assert not (private/'desktop.json').exists()
        core['helper_dispatch']=True
        with (private/'log').open('w') as log:
            process=subprocess.Popen([str(binary),'--chatseek-desktop','--no-browser'],env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+30
                while not (private/'desktop.json').exists():
                    if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('desktop startup failed')
                    time.sleep(.1)
                base=json.loads((private/'desktop.json').read_text())['url']
                with urllib.request.urlopen(base+'/api/state',timeout=5) as response:
                    assert json.load(response)=={'setup_required':True}
                with urllib.request.urlopen(base+'/api/setup',timeout=5) as response:
                    value=json.load(response)
                    assert not value['keys_ready'] and not value['contact_ready'] and not value['api_configured']
                core['first_run']=True
                busy=subprocess.run([str(binary),'--chatseek-clear-data','--confirm'],env=env,capture_output=True)
                assert busy.returncode!=0
                core['cleanup_requires_exit']=True
            finally:
                process.terminate();process.wait(timeout=30)
        if process.returncode!=0:
            raise RuntimeError('desktop shutdown failed: '+(private/'log').read_text()[-4000:])
        assert not (private/'desktop.json').exists()
        core['shutdown']=True
        # Clear only disposable, synthetic files. The app must be stopped first.
        for name in ('contacts','media','rag'):
            (private/name).mkdir();(private/name/'synthetic').write_text('test')
        (private/'chat.sqlite3').write_text('test');(private/'target.json').write_text('{}')
        (private/'beta.json').write_text('{"device":"synthetic"}')
        subprocess.run([str(binary),'--chatseek-clear-data','--confirm'],env=env,check=True)
        assert (private/'beta.json').exists() and not (private/'target.json').exists()
        assert all(not (private/name).exists() for name in ('contacts','media','rag','chat.sqlite3'))
        core['cleanup']=True
    if args.model_cache:
        env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='1')
        env.pop('DEEPSEEK_API_KEY',None)
        with tempfile.TemporaryFile() as log, tempfile.TemporaryDirectory(prefix='chatseek-model-check-') as model_private:
            env['CHATSEEK_DATA_DIR']=model_private
            env['BROWSER']='/usr/bin/true'
            process=subprocess.Popen([str(binary),'--chatseek-worker',str(args.model_cache.resolve())],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,env=env,text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ)
                    if not selector.select(90):raise RuntimeError('model startup timeout')
                    assert json.loads(process.stdout.readline())['ready']
                    process.stdin.write(json.dumps({'action':'encode','texts':['合成验证：周五下午三点开会。'],'query':False})+'\n')
                    process.stdin.flush()
                    if not selector.select(30):raise RuntimeError('embedding timeout')
                    result=json.loads(process.stdout.readline())
                    assert result['shape']==[1,512]
                    core['embedding_shape']=result['shape']
            finally:
                process.terminate();process.wait(timeout=15)
            assert not (Path(model_private)/'desktop.json').exists()
    archive=ROOT/'dist/ChatSeek-macOS-arm64-beta.zip'
    if archive.exists():
        with zipfile.ZipFile(archive) as package:
            bad=[name for name in package.namelist() if '/.private/' in name or name.endswith(('/.env','/keys.json','/target.json','/beta.json','/chat.sqlite3'))]
            assert not bad
        core['private_file_matches']=0
    print(json.dumps(core,ensure_ascii=False))


if __name__=='__main__':main()
