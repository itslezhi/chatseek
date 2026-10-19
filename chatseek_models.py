"""Locate public embedding weights without importing the model runtime."""
from pathlib import Path
import os

from chatseek_runtime import ROOT

MODEL = 'BAAI/bge-small-zh-v1.5'
REVISION = '7999e1d3359715c523056ef9478215996d62a620'
FILES = ('config.json', 'config_sentence_transformers.json', 'modules.json',
         'sentence_bert_config.json', '1_Pooling/config.json', 'model.safetensors',
         'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt')


def complete(path):
    path = Path(path)
    return all((path/name).is_file() and (path/name).stat().st_size > 0 for name in FILES)


def locate(private):
    explicit = os.environ.get('CHATSEEK_EMBEDDING_MODEL_DIR')
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return {'path': path, 'source': 'configured', 'available': complete(path)}
    candidates = [
        (ROOT/'models/bge-small-zh-v1.5', 'bundled'),
        (Path(private)/'models/models--BAAI--bge-small-zh-v1.5/snapshots'/REVISION, 'cache'),
    ]
    for path, source in candidates:
        if complete(path):
            return {'path': path.resolve(), 'source': source, 'available': True}
    return {'path': None, 'source': 'download', 'available': False}
