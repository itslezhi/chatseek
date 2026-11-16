"""Build on 64-bit Windows; only allowlisted source/assets enter the executable."""
from pathlib import Path
import shutil
import subprocess
import sys
import struct

ROOT=Path(__file__).resolve().parents[1]

def main():
    if sys.platform!='win32' or struct.calcsize('P')!=8:
        raise SystemExit('Build natively with 64-bit Windows Python 3.12.')
    subprocess.run([sys.executable,'-m','PyInstaller','--clean','--noconfirm',
                    str(ROOT/'packaging/windows/ChatSeek.spec')],cwd=ROOT,check=True)
    subprocess.run([str(ROOT/'dist/ChatSeek/ChatSeek.exe'),'--chatseek-self-test'],cwd=ROOT,check=True)
    shutil.make_archive(str(ROOT/'dist/ChatSeek-Windows-x64'),'zip',ROOT/'dist','ChatSeek')

if __name__=='__main__':main()
