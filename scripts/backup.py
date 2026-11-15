#!/usr/bin/env python3
"""Consistent online SQLite backup, including committed WAL transactions."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadharbor.config import load_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination', help='New backup filename; never overwrites an existing backup.')
args = parser.parse_args()
load_env()
source = Path(os.getenv('DATABASE_PATH', 'data/leadharbor.sqlite3')).resolve()
target = Path(args.destination).resolve()
if not source.is_file() or target.exists() or source == target:
    parser.error('Source must exist and destination must be a new file.')
target.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
try:
    with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise RuntimeError('Backup integrity verification failed.')
except BaseException:
    target.unlink(missing_ok=True)
    raise
print('Backup created and verified. Store it encrypted with restricted access.')
