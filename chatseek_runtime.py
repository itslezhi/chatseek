"""Paths and child entry points shared by source and the macOS bundle."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent


def data_dir():
    value = os.environ.get('CHATSEEK_DATA_DIR')
    return Path(value).expanduser().resolve() if value else ROOT / '.private'


def child_command(role, *args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--chatseek-' + role, *map(str, args)]
    script = 'wechat_embedding_worker.py' if role == 'worker' else 'wechat_rag.py'
    return [sys.executable, str(ROOT / script), *([] if role == 'worker' else ['watch']), *map(str, args)]


def config_path():
    # A distributed app never reads a developer .env from its resources.
    return data_dir() / '.env' if os.environ.get('CHATSEEK_DATA_DIR') else ROOT / '.env'
