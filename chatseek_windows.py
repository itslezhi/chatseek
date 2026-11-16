"""Opt-in, read-only Windows WeChat 4.x connection. No injection or memory writes."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import struct
import platform
import sys
import time

KEY_LITERAL = re.compile(rb"[xX]'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")


def supported():
    return (sys.platform == 'win32' and struct.calcsize('P') == 8
            and platform.machine().lower() in ('amd64','x86_64')
            and sys.getwindowsversion().major >= 10)


def documents_dir():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0]))
    except (ImportError, OSError):
        return Path.home()/'Documents'


def validate_root(value):
    if not isinstance(value, str) or value.startswith(('\\\\', '//')):
        raise ValueError('请选择本机磁盘上的 db_storage 目录。')
    path = Path(value).expanduser()
    if not path.is_absolute():raise ValueError('请填写本机 db_storage 的完整路径。')
    path = path.resolve()
    if path.name != 'db_storage' or not (path/'contact/contact.db').is_file() or not message_paths(path):
        raise ValueError('未找到微信 4.x 的 contact/contact.db 和 message/message_数字.db。')
    return path


def message_paths(root):
    return sorted(p for p in (Path(root)/'message').glob('message_*.db')
                  if p.stem.removeprefix('message_').isdigit())


def accounts():
    bases = [documents_dir()/'xwechat_files', Path.home()/'xwechat_files']
    override = os.environ.get('CHATSEEK_WECHAT_DIR')
    if override:bases.append(Path(override).expanduser())
    try:
        import winreg
        for name in ('Weixin', 'WeChat'):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Tencent\\'+name) as key:
                    value = winreg.QueryValueEx(key, 'FileSavePath')[0]
                    if value and value != 'MyDocument:':bases += [Path(value), Path(value)/'xwechat_files']
            except OSError:pass
    except ImportError:pass
    found = set()
    for base in bases:
        if str(base).startswith(('\\\\', '//')):continue
        for path in [base, base/'db_storage', *base.glob('*/db_storage')]:
            try:found.add(validate_root(str(path)))
            except (ValueError, OSError):pass
    return sorted(found)


def wechat_processes():
    import psutil
    owner = psutil.Process().username().casefold()
    result = []
    for process in psutil.process_iter(['name', 'username']):
        try:
            if (process.info['name'] or '').casefold() in ('weixin.exe', 'wechat.exe') and (process.info['username'] or '').casefold() == owner:
                result.append(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):pass
    return result


def memory_chunks(pid, cancelled, deadline):
    """Read committed readable pages, in bounded chunks, from a current-user process."""
    class Region(C.Structure):
        _fields_ = [('base', C.c_void_p), ('allocation', C.c_void_p), ('allocation_protect', W.DWORD),
                    ('partition', W.WORD), ('size', C.c_size_t), ('state', W.DWORD),
                    ('protect', W.DWORD), ('kind', W.DWORD)]
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD];kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(Region), C.c_size_t]
    kernel.VirtualQueryEx.restype = C.c_size_t
    kernel.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    kernel.ReadProcessMemory.restype = W.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ only
    if not handle:raise RuntimeError('无法读取微信进程。请让微信与 ChatSeek 使用同一 Windows 用户和权限级别。')
    address = 0;scanned = 0
    try:
        while address < 0x7fffffffffff and not cancelled.is_set() and time.monotonic() < deadline:
            region = Region()
            if not kernel.VirtualQueryEx(handle, address, C.byref(region), C.sizeof(region)):break
            base = region.base or 0;end = base + region.size
            if end <= address:break
            if region.state == 0x1000 and not region.protect & 0x100 and region.protect & 0xff in (2, 4, 8, 0x20, 0x40, 0x80):
                offset=base;tail=b''
                while offset < end and not cancelled.is_set() and time.monotonic() < deadline:
                    size=min(1024*1024,end-offset);buffer=C.create_string_buffer(size);count=C.c_size_t()
                    kernel.ReadProcessMemory(handle,offset,buffer,size,C.byref(count))
                    if count.value:
                        chunk=buffer.raw[:count.value];yield tail+chunk;tail=chunk[-100:]
                    else:tail=b''
                    offset+=size;scanned+=size
                    if scanned >= 4*1024**3:return
            address=end
    finally:kernel.CloseHandle(handle)


def capture(root, cancelled, progress, *, pids=None, chunks=None):
    """Only persist keys that actually open every required selected-account database."""
    from sqlcipher_probe import DB
    root=validate_root(str(root))
    required=[root/'contact/contact.db', *message_paths(root)]
    by_salt={}
    for path in required:
        with path.open('rb') as handle:salt=handle.read(16).hex()
        by_salt.setdefault(salt,[]).append(path)
    pids=wechat_processes() if pids is None else pids
    if not pids:raise RuntimeError('请先打开 Windows 微信 4.x 并登录所选账号，再重试。')
    found={};attempted=set();deadline=time.monotonic()+120
    for pid in pids:
        try:
            for chunk in (chunks or memory_chunks)(pid,cancelled,deadline):
                if cancelled.is_set():raise RuntimeError('已取消微信接入。')
                for match in KEY_LITERAL.finditer(chunk):
                    key,salt=(value.decode('ascii').lower() for value in match.groups())
                    if salt not in by_salt or (salt,key) in attempted:continue
                    attempted.add((salt,key))
                    verified=[]
                    try:
                        for path in by_salt[salt]:
                            with DB(path,key) as db:db.query('SELECT count(*) FROM sqlite_master')
                            verified.append(path)
                    except RuntimeError:continue
                    found.update({str(path.relative_to(root)).replace('\\','/'):key for path in verified})
                    progress('正在验证本机数据库：'+str(len(found))+'/'+str(len(required)))
                    if len(found)==len(required):return {'keys':found}
        except OSError:continue
    if cancelled.is_set():raise RuntimeError('已取消微信接入。')
    raise RuntimeError('未能验证全部数据库密钥，未保存不完整凭据。请确认账号、保持微信登录并打开要同步的会话后重试；当前微信版本也可能尚不兼容。')
