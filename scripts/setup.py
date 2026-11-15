#!/usr/bin/env python3
"""Create private local configuration without overwriting an existing installation."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadharbor.security import password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Synthetic, loopback-only workspace; no external requests.')
    args = parser.parse_args()
    path = ROOT / '.env'
    if path.exists():
        parser.error('.env already exists. Edit it directly; existing credentials are never overwritten.')
    if args.demo:
        password = 'leadharbor-demo-only'
    else:
        password = getpass.getpass('Choose a workspace password (at least 12 characters): ')
        if len(password) < 12 or len(password) > 512:
            parser.error('Password must contain 12–512 characters.')
        if password != getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match.')
    content = (ROOT / '.env.example').read_text()
    replacements = {'ADMIN_PASSWORD_HASH': password_hash(password), 'ADMIN_TOKEN': secrets.token_urlsafe(32),
                    'INGEST_TOKEN': secrets.token_urlsafe(32), 'SESSION_SECRET': secrets.token_urlsafe(32),
                    'N8N_ENCRYPTION_KEY': secrets.token_hex(32), 'EVENT_WEBHOOK_TOKEN': secrets.token_urlsafe(32),
                    'DEMO_MODE': str(args.demo).lower()}
    if args.demo:
        replacements['DATABASE_PATH'] = 'data/demo.sqlite3'
    lines = []
    for line in content.splitlines():
        key = line.split('=', 1)[0]
        lines.append(key + '=' + replacements[key] if key in replacements else line)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('\n'.join(lines) + '\n')
    print('Created .env with private permissions. Authentication secrets were not printed.')
    print('Run: python3 scripts/seed_demo.py && python3 -m leadharbor' if args.demo else 'Add your LLM settings to .env, then run: python3 -m leadharbor')
    if args.demo:
        print('Demo password: leadharbor-demo-only (synthetic, local-only workspace).')


if __name__ == '__main__':
    main()
