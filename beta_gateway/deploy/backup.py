#!/usr/bin/env python3
"""Consistent local SQLite backup, including committed WAL transactions."""
import argparse
import datetime as dt
import os
from pathlib import Path
import sqlite3


def backup(source, directory, keep=14):
    source, directory = Path(source).resolve(), Path(directory)
    if keep < 1:
        raise ValueError('keep must be positive')
    if not source.is_file():
        raise FileNotFoundError(source)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory / ('usage-' + stamp + '.sqlite3')
    temporary = destination.with_suffix('.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(temporary) as copy:
                original.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup integrity check failed')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    for old in sorted(directory.glob('usage-*.sqlite3'), reverse=True)[keep:]:
        old.unlink()
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('/var/lib/chatseek/usage.sqlite3'))
    parser.add_argument('--directory', type=Path, default=Path('/var/lib/chatseek-backups'))
    parser.add_argument('--keep', type=int, default=14)
    args = parser.parse_args()
    print(backup(args.db, args.directory, args.keep))
