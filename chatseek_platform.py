"""Small OS boundary: file locks, data locations and process identity."""
import errno
import os
from pathlib import Path
import sys
import time

LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8


def flock(handle, operation):
    if sys.platform != 'win32':
        import fcntl
        return fcntl.flock(handle, operation)
    import msvcrt
    fd = handle if isinstance(handle, int) else handle.fileno()
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK if operation & LOCK_UN else msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if operation & LOCK_UN or exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if operation & LOCK_NB:
                    raise BlockingIOError(errno.EAGAIN, 'File is locked') from None
                time.sleep(.05)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def desktop_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'ChatSeek'
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/ChatSeek'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'ChatSeek'


def child_options():
    if sys.platform == 'win32':
        import subprocess
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def terminate_watcher(pid, expected):
    """Check the entire command, not a substring of another process's command."""
    import psutil
    try:
        process = psutil.Process(pid)
        actual = process.cmdline()
        if len(actual) < len(expected):
            return False
        if Path(actual[0]).resolve() != Path(expected[0]).resolve() or actual[1:len(expected)] != expected[1:]:
            return False
        process.terminate()
        try: process.wait(timeout=5)
        except psutil.TimeoutExpired: return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
