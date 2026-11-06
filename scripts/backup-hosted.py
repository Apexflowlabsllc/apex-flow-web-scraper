"""Encrypted off-host snapshot and restore verification, run under Brian's Windows account.

Windows DPAPI protects the entire bundle, including the service environment.
Recovery requires this Windows account/profile on this computer. No credentials
are written as plaintext locally or printed. Database snapshots use SQLite backup.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import zipfile

REMOTE = r'''
import io,sqlite3,sys,tempfile,zipfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='snapshot-',dir='/var/lib/apex-webscraper') as temp:
    snapshot=Path(temp)/'billing.db'
    with sqlite3.connect('file:/var/lib/apex-webscraper/billing.db?mode=ro',uri=True) as source:
        with sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            dest.execute('PRAGMA journal_mode=DELETE')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(snapshot,'billing.db')
        bundle.write('/etc/apex-webscraper.env','service.env')
        bundle.write('/etc/systemd/system/apex-webscraper.service','service.unit')
    sys.stdout.buffer.write(output.getvalue())
'''


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('This backup is bound to Windows DPAPI')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypto = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypto.CryptUnprotectData if decrypt else crypto.CryptProtectData
    description = None if decrypt else 'Apex scraper recovery snapshot'
    if not fn(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def verify(encrypted):
    plain = protect(encrypted, decrypt=True)
    with zipfile.ZipFile(io.BytesIO(plain)) as bundle:
        if set(bundle.namelist()) != {'billing.db', 'service.env', 'service.unit'}:
            raise ValueError('Unexpected backup members')
        raw = bundle.read('billing.db')
        database = sqlite3.connect(':memory:')
        try:
            database.deserialize(raw)
            if database.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Restored database failed integrity check')
            counts = {table: database.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                      for table in ('accounts', 'keys', 'orders', 'usage')}
        finally:
            database.close()
        env = dict(line.split('=', 1) for line in bundle.read('service.env').decode().splitlines() if '=' in line)
        required = ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'DELIVERY_SECRET', 'SCRAPER_GATEWAY_SECRET')
        if not all(env.get(name) for name in required):
            raise ValueError('Recovery environment is incomplete')
    return {'integrity': 'ok', 'restore': 'in-memory SQLite restore verified',
            'counts': counts, 'database_sha256': hashlib.sha256(raw).hexdigest(),
            'encrypted_sha256': hashlib.sha256(encrypted).hexdigest(), 'environment_complete': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', default=r'E:\apex\backups\webscraper')
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.verify.read_bytes())))
        return
    destination = Path(args.destination).resolve()
    if destination.drive.upper() != 'E:':
        raise ValueError('Project backups must stay on E:')
    destination.mkdir(parents=True, exist_ok=True)
    command = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i',
               r'C:\Users\Apex-Boss\.ssh\rainbo_fresh_20260915', '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=15', 'root@159.223.109.53', 'python3 -']
    result = subprocess.run(command, input=REMOTE.encode(), capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Remote snapshot failed with exit code '+str(result.returncode))
    encrypted = protect(result.stdout)
    receipt = verify(encrypted)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = destination / ('scraper-'+stamp+'.dpapi')
    with path.open('xb') as output:
        output.write(encrypted)
    receipt.update(file=str(path), bytes=path.stat().st_size, verified_at=stamp)
    (destination/'latest-verification.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
