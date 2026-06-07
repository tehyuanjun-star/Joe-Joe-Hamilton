"""
upload_signals.py
-----------------
把 scanner.py 生成的 signals/YYYY-MM-DD.json 上传到 Supabase signals 表。

用法：
    python upload_signals.py                          # 上传最新报告
    python upload_signals.py signals/2026-06-07.json # 指定文件

环境变量（在 .env.local 或系统环境中设置）：
    SUPABASE_URL      = https://xxxx.supabase.co
    SUPABASE_ANON_KEY = sb_publishable_...
"""

import json
import os
import sys
import glob
from datetime import datetime, timezone
from pathlib import Path

# ── 读取环境变量 ─────────────────────────────────────────
def load_env():
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent / '.env.local'
        if env_path.exists():
            load_dotenv(env_path)
            print(f'  ✓ 从 {env_path.name} 加载环境变量')
    except ImportError:
        pass

load_env()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '').strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    print('❌ 错误：缺少 SUPABASE_URL 或 SUPABASE_ANON_KEY 环境变量')
    print('   请在 .env.local 文件中配置（参考 .env.example）')
    sys.exit(1)

# ── 找到要上传的文件 ─────────────────────────────────────
BASE_DIR = Path(__file__).parent

if len(sys.argv) >= 2:
    report_file = sys.argv[1]
else:
    files = sorted(glob.glob(str(BASE_DIR / 'signals' / '*.json')), reverse=True)
    if not files:
        print('❌ 找不到 signals/*.json 文件，请先运行 scanner.py')
        sys.exit(1)
    report_file = files[0]

print(f'📂 上传文件: {report_file}')

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

# ── 调用 Supabase REST API ──────────────────────────────
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
        print(f'✅ 上传成功！scan_date={r0.get("scan_date")}  '
              f'watch_now={r0.get("watch_now")}  watching={r0.get("watching")}')
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8')
    print(f'❌ 上传失败：HTTP {e.code}')
    print(f'   {body}')
    sys.exit(1)
except urllib.error.URLError as e:
    print(f'❌ 网络错误：{e.reason}')
    sys.exit(1)
