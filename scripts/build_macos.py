#!/usr/bin/env python3
"""Build an arm64 Mac beta with an explicit resource allowlist. No private data."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def run(args,env=None):subprocess.run(list(map(str,args)),cwd=ROOT,check=True,env=env)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity',help='Developer ID Application certificate name')
    parser.add_argument('--notary-profile',help='Existing notarytool keychain profile')
    parser.add_argument('--skip-runtime',action='store_true',help='Reuse the already built runtime')
    parser.add_argument('--gateway-url',help='Public HTTPS beta gateway preset; never a secret key')
    args=parser.parse_args()
    if args.gateway_url:
        from urllib.parse import urlsplit
        parsed=urlsplit(args.gateway_url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            parser.error('--gateway-url must be an HTTPS origin without credentials')
    if platform.system()!='Darwin' or platform.machine()!='arm64':parser.error('build natively on Apple Silicon macOS')
    dist=ROOT/'dist';work=ROOT/'build'
    dist.mkdir(exist_ok=True)
    if args.notary_profile and not args.identity:parser.error('--notary-profile requires --identity')
    if args.identity and args.skip_runtime:parser.error('signed builds must rebuild the nested runtime')
    env=os.environ.copy()
    if args.identity:env['CHATSEEK_CODESIGN_IDENTITY']=args.identity
    if not args.skip_runtime:run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',ROOT/'packaging/macos/ChatSeek.spec'],env=env)
    app=dist/'ChatSeek.app'
    if app.exists():shutil.rmtree(app)
    mac=app/'Contents/MacOS';resources=app/'Contents/Resources'
    mac.mkdir(parents=True);resources.mkdir()
    shutil.copytree(dist/'ChatSeekRuntime',resources/'runtime',symlinks=True)
    shutil.copytree(ROOT/'web',resources/'runtime/_internal/web',dirs_exist_ok=True)
    defaults=resources/'runtime/_internal/beta-defaults.json'
    defaults.write_text(json.dumps({'gateway_url':args.gateway_url or ''}))
    shutil.copy2(ROOT/'packaging/macos/内测使用说明.txt',resources/'内测使用说明.txt')
    iconset=work/'ChatSeek.iconset'
    iconset.mkdir(parents=True,exist_ok=True)
    for size in (16,32,128,256,512):
        for scale in (1,2):
            filename=f'icon_{size}x{size}'+('@2x' if scale==2 else '')+'.png'
            run(['/usr/bin/sips','-z',str(size*scale),str(size*scale),ROOT/'web/assets/chatseek-pet.png','--out',iconset/filename])
    run(['/usr/bin/iconutil','-c','icns',iconset,'-o',resources/'ChatSeek.icns'])
    run(['/usr/bin/swiftc','-O','-target','arm64-apple-macos14.0',ROOT/'packaging/macos/Launcher.swift','-o',mac/'ChatSeek'])
    info={'CFBundleName':'ChatSeek','CFBundleDisplayName':'ChatSeek','CFBundleExecutable':'ChatSeek',
          'CFBundleIdentifier':'local.chatseek.beta','CFBundlePackageType':'APPL','CFBundleVersion':'1',
          'CFBundleIconFile':'ChatSeek.icns',
          'CFBundleShortVersionString':'0.1.0','LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,
          'NSHumanReadableCopyright':'ChatSeek beta','NSDocumentsFolderUsageDescription':'ChatSeek 在你确认后读取本机微信聊天数据库。'}
    with (app/'Contents/Info.plist').open('wb') as handle:plistlib.dump(info,handle)
    # PyInstaller signs nested runtime binaries; the outer app can be ad hoc
    # signed for local QA or signed with a supplied Developer ID for distribution.
    identity=args.identity or '-'
    sign=['/usr/bin/codesign','--force','--deep','--sign',identity]
    if args.identity:sign+=['--options','runtime','--timestamp']
    run(sign+[app])
    run(['/usr/bin/codesign','--verify','--deep','--strict',app])
    archive=dist/'ChatSeek-macOS-arm64-beta.zip'
    archive.unlink(missing_ok=True)
    run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',app,archive])
    if args.notary_profile:
        run(['/usr/bin/xcrun','notarytool','submit',archive,'--keychain-profile',args.notary_profile,'--wait'])
        run(['/usr/bin/xcrun','stapler','staple',app])
        archive.unlink()
        run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',app,archive])
    print(archive)
    from build_beta_gateway import build
    gateway=build(dist/'ChatSeek-beta-gateway.zip')
    print(gateway)

if __name__=='__main__':main()
