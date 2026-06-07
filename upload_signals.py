"""
upload_signals.py
Upload signals/YYYY-MM-DD.json to Supabase signals table.

Usage:
    python upload_signals.py                          # upload latest
    python upload_signals.py signals/2026-06-07.json # specific file

Env vars (set in .env.local or system environment):
    SUPABASE_URL      = https://xxxx.supabase.co
    SUPABASE_ANON_KEY = sb_publishable_...
"""

import json
import os
import sys
import glob

# Fix Windows terminal encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from datetime import datetime, timezone
from pathlib import Path

# -- Load env vars from .env.local if python-dotenv available --
def load_env():
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent / '.env.local'
        if env_path.exists():
            load_dotenv(env_path)
            print(f'  Loaded env from {env_path.name}')
    except ImportError:
        # Manually parse .env.local
        env_path = Path(__file__).parent / '.env.local'
        if env_path.exists():
            with open(env_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip())
            print(f'  Loaded env from {env_path.name}')

load_env()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '').strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    print('ERROR: missing SUPABASE_URL or SUPABASE_ANON_KEY')
    print('  Create .env.local with these two variables (see .env.example)')
    sys.exit(1)

# -- Find report file --
BASE_DIR = Path(__file__).parent

if len(sys.argv) >= 2:
    report_file = sys.argv[1]
else:
    files = sorted(glob.glob(str(BASE_DIR / 'signals' / '*.json')), reverse=True)
    if not files:
        print('ERROR: no signals/*.json found. Run scanner.py first.')
        sys.exit(1)
    report_file = files[0]

print(f'Uploading: {report_file}')

with open(report_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

scan_date    = data.get('scan_date') or datetime.now().strftime('%Y-%m-%d')
generated_at = data.get('generated_at')
summary      = data.get('summary', {})

payload = json.dumps({
    'scan_date':    scan_date,
    'generated_at': generated_at,
    'candidates':   summary.get('candidates', 0),
    'watch_now':    summary.get('watch_now', 0),
    'watching':     summary.get('watching', 0),
    'close_alerts': summary.get('close_alerts', 0),
    'data':         data,
    'created_at':   datetime.now(timezone.utc).isoformat(),
}).encode('utf-8')

# -- Call Supabase REST API --
import urllib.request
import urllib.error

url = f'{SUPABASE_URL}/rest/v1/signals'
headers = {
    'Content-Type':  'application/json',
    'apikey':        SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Prefer':        'resolution=merge-duplicates,return=representation',
}

req = urllib.request.Request(url, data=payload, headers=headers, method='POST')

try:
    with urllib.request.urlopen(req) as resp:
        body   = resp.read().decode('utf-8')
        result = json.loads(body)
        r0     = result[0] if result else {}
        print(f'OK: scan_date={r0.get("scan_date")}  '
              f'watch_now={r0.get("watch_now")}  watching={r0.get("watching")}')
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8')
    print(f'FAILED: HTTP {e.code}')
    print(f'  {body}')
    sys.exit(1)
except urllib.error.URLError as e:
    print(f'NETWORK ERROR: {e.reason}')
    sys.exit(1)
