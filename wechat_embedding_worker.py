"""Single-process, single-threaded local model runtime.

Only this child imports PyTorch/sentence-transformers. FAISS remains in the web
process so the two packages never load their OpenMP runtimes in one process.
The JSON-lines protocol carries text only over local pipes, never in argv/logs.
"""
from __future__ import annotations
import base64
import contextlib
import json
import os
from pathlib import Path
import sys


def main():
    for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name]='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    from wechat_vectors import MODEL,QUERY_PREFIX
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    options={'cache_folder':sys.argv[1],'device':'cpu','trust_remote_code':False}
    with contextlib.redirect_stdout(sys.stderr):
        try:model=SentenceTransformer(MODEL,local_files_only=True,**options)
        except (OSError,ValueError):model=SentenceTransformer(MODEL,**options)
    model.max_seq_length=512
    print(json.dumps({'ready':True,'pid':os.getpid()}),flush=True)
    for line in sys.stdin.buffer:
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request['action']=='tokens':
                    result={'tokens':model.tokenizer.encode(request['text'],add_special_tokens=False)}
                elif request['action']=='encode':
                    texts=[QUERY_PREFIX+text if request.get('query') else text for text in request['texts']]
                    matrix=model.encode(texts,batch_size=16,normalize_embeddings=True,
                                        convert_to_numpy=True,show_progress_bar=False).astype('float32')
                    result={'shape':list(matrix.shape),'data':base64.b64encode(matrix.tobytes()).decode('ascii')}
                else:raise ValueError()
            print(json.dumps(result),flush=True)
        except Exception:
            # Never echo request text, paths or native exception strings.
            print(json.dumps({'error':'local_embedding_failed'}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'error':'local_model_initialization_failed'}),flush=True)
        raise SystemExit(1)
