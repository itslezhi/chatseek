#!/usr/bin/env python3
"""Allowlisted source snapshot and local-only secret check before publishing."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXTRA_FILES=('LICENSE','SECURITY.md','CONTRIBUTING.md','requirements-build.txt','web/setup.js',
    'scripts/build_macos.py','scripts/build_beta_gateway.py','scripts/check_macos_bundle.py','scripts/prepare_public_source.py',
    'tests/test_beta_gateway.py','tests/test_gateway_deploy.py','tests/test_desktop_setup.py','tests/test_llm_config.py')
EXTRA_DIRS=('.github','packaging','beta_gateway')
SECRET_PATTERN=re.compile(rb'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def files():
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    chosen={name for name in tracked if name}
    chosen.update(EXTRA_FILES)
    chosen.update(p.name for p in ROOT.glob('chatseek_*.py'))
    for name in EXTRA_DIRS:
        chosen.update(str(p.relative_to(ROOT)) for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    forbidden={'.private','.venv','dist','build','output','reports','gateway-private','__pycache__'}
    for name in sorted(chosen):
        path=ROOT/name
        if path.is_symlink() or any(part in forbidden for part in Path(name).parts) or (name!='.env.example' and Path(name).name.startswith('.env')) or path.suffix in ('.db','.sqlite3','.pyc','.pem','.key','.p12','.pfx','.log'):
            raise RuntimeError('Non-public path in source selection: '+name)
        if not path.is_file():raise RuntimeError('Missing source: '+name)
    return sorted(chosen)


def actual_private_values():
    values=[]
    for path in (ROOT/'.env',ROOT/'.private/.env'):
        if path.exists():
            for line in path.read_text().splitlines():
                key,sep,value=line.partition('=')
                if sep and key.strip().endswith('API_KEY') and len(value.strip())>8:values.append(value.strip().strip('\"\'').encode())
    target=ROOT/'.private/target.json'
    if target.exists():
        item=json.loads(target.read_text())
        values.extend(item[key].encode() for key in ('db_root','chat_id') if isinstance(item.get(key),str) and len(item[key])>8)
    keys=ROOT/'.private/wechat-init/keys.json'
    def walk(item):
        if isinstance(item,dict):
            for value in item.values():walk(value)
        elif isinstance(item,list):
            for value in item:walk(value)
        elif isinstance(item,str) and re.fullmatch('[a-fA-F0-9]{64}',item):values.append(item.encode())
    if keys.exists():walk(json.loads(keys.read_text()))
    return values


def snapshot():
    entries=[];problems=[];private=actual_private_values()
    for name in files():
        data=(ROOT/name).read_bytes()
        if SECRET_PATTERN.search(data) or any(value in data for value in private):problems.append(name)
        try:content=data.decode();encoding='utf-8'
        except UnicodeError:content=base64.b64encode(data).decode();encoding='base64'
        sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        mode='100755' if (ROOT/name).stat().st_mode&0o111 else '100644'
        entries.append({'path':name,'mode':mode,'type':'blob','sha':sha,'encoding':encoding,'content':content})
    if problems:raise RuntimeError('Sensitive material detected; publication stopped for paths: '+', '.join(problems))
    return entries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true',help='Machine-readable source contents; never includes personal data')
    parser.add_argument('--zip',type=Path)
    args=parser.parse_args();entries=snapshot()
    if args.zip:
        args.zip.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(args.zip,'w',zipfile.ZIP_DEFLATED) as archive:
            for item in entries:archive.write(ROOT/item['path'],'ChatSeek/'+item['path'])
    if args.json:print(json.dumps(entries,ensure_ascii=False))
    else:print(json.dumps({'public_files':len(entries),'secret_matches':0,'archive':str(args.zip) if args.zip else None},ensure_ascii=False))


if __name__=='__main__':main()
