# Explicit data allowlist: private files and developer configuration are excluded.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata
root=Path(SPECPATH).parents[1]
datas=[(str(root/'web'),'web'),(str(root/'vendor/wechat_local/bootstrap_keys.py'),'vendor/wechat_local'),
       (str(root/'vendor/wechat_local/capture_keys.py'),'vendor/wechat_local')]
binaries=[]
hidden=['wechat_web','wechat_rag','wechat_embedding_worker','chatseek_setup','sqlcipher3._sqlite3','imageio_ffmpeg','tiktoken_ext.openai_public']
for package in ('sentence_transformers','transformers','faiss','sqlcipher3','imageio_ffmpeg','tiktoken'):
    d,b,h=collect_all(package);datas+=d;binaries+=b;hidden+=h
for package in ('sentence-transformers','torch','transformers','tokenizers','huggingface-hub','safetensors','tiktoken','regex','requests','packaging','PyYAML','numpy','scipy','scikit-learn','Pillow','tqdm'):
    datas+=copy_metadata(package)
a=Analysis([str(root/'chatseek_desktop.py')],pathex=[str(root),str(root/'vendor/wechat_local')],
           binaries=binaries,datas=datas,hiddenimports=hidden,
           excludes=['tkinter','matplotlib','IPython','pytest','tensorboard'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ChatSeekRuntime',console=True,target_arch='arm64',
        codesign_identity=os.environ.get('CHATSEEK_CODESIGN_IDENTITY'))
coll=COLLECT(exe,a.binaries,a.datas,name='ChatSeekRuntime')
