#!/usr/bin/env python3
"""Frozen runtime dispatcher and supervised local desktop service."""
import os
from pathlib import Path
import signal
import sys


def main():
    os.umask(0o077)
    if '--chatseek-self-test' in sys.argv:
        import wechat_rag  # registers bundled/source vendor module location
        import json
        import tempfile
        import sqlite3
        import faiss
        import numpy as np
        import imageio_ffmpeg
        from Crypto.Cipher import AES
        from sqlcipher_probe import DB
        with tempfile.TemporaryDirectory() as folder:
            with DB(Path(folder)/'toy.db','11'*32,fixture_write=True) as db:
                db.query('CREATE TABLE toy(value INTEGER)')
                db.query('INSERT INTO toy VALUES(42)')
            with DB(Path(folder)/'toy.db','11'*32) as db:
                assert int(db.query('SELECT value FROM toy')[0]['value'])==42
            connection=sqlite3.connect(':memory:')
            connection.execute('CREATE VIRTUAL TABLE toy USING fts5(text)')
            connection.close()
        matrix=np.array([[1,0],[0,1]],dtype='float32')
        index=faiss.IndexFlatIP(2);index.add(matrix)
        assert index.search(matrix[:1],1)[1][0][0]==0
        assert Path(imageio_ffmpeg.get_ffmpeg_exe()).exists()
        print(json.dumps({'sqlcipher':True,'fts5':True,'faiss':True,'ffmpeg':True,'crypto':True,'frozen':bool(getattr(sys,'frozen',False))}))
        return 0
    if '--chatseek-worker' in sys.argv:
        sys.argv.remove('--chatseek-worker')
        from wechat_embedding_worker import main as worker
        return worker()
    if '--chatseek-watch' in sys.argv:
        sys.argv[1]='watch'
        from wechat_rag import main as cli
        return cli()
    private=Path(os.environ.get('CHATSEEK_DATA_DIR',str(Path.home()/'Library/Application Support/ChatSeek'))).expanduser().resolve()
    os.environ['CHATSEEK_DATA_DIR']=str(private)
    private.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(private,0o700)
    # Retain this descriptor for the life of the service.
    import fcntl
    with (private/'desktop.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if '--chatseek-clear-data' in sys.argv:
                print('Quit ChatSeek before clearing local data.')
                return 1
            return 0
        if '--chatseek-clear-data' in sys.argv:
            if '--confirm' not in sys.argv:
                print('Quit ChatSeek, then add --confirm to clear local chat indexes, media and Q&A. WeChat originals and activation are retained.')
                return 2
            import shutil
            for name in ('contacts','rag','media'):
                path=private/name
                if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
            for name in ('chat.sqlite3','chat.sqlite3-wal','chat.sqlite3-shm','target.json','target.new','media-keys.json'):
                (private/name).unlink(missing_ok=True)
            return 0
        from http.server import ThreadingHTTPServer
        from wechat_web import Handler
        import threading
        import webbrowser
        import wechat_rag as rag
        from chatseek_beta_client import private_json
        server=None
        for port in range(8765,8785):
            try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler);break
            except OSError:continue
        if server is None:
            raise RuntimeError('No local port is available')
        url=f'http://127.0.0.1:{server.server_address[1]}'
        private_json(private/'desktop.json',{'pid':os.getpid(),'url':url})
        def stop(*_):
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        if '--no-browser' not in sys.argv:webbrowser.open(url)
        try:server.serve_forever(poll_interval=.2)
        finally:
            server.server_close()
            from chatseek_setup import stop_bootstrap
            stop_bootstrap()
            rag.stop_watch()
            from wechat_embedding_client import shutdown_worker
            shutdown_worker()
            (private/'desktop.json').unlink(missing_ok=True)
        return 0


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
