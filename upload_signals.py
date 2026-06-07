"""
upload_signals.py
-----------------
把 scanner.py 生成的 scan_report_YYYY-MM-DD.json 上传到 Supabase signals 表。

用法：
    python upload_signals.py                       # 上传最新报告
    python upload_signals.py scan_report_2026-06-07.json  # 指定文件

环境变量（在 .env.local 或系统环境中设置）：
    SUPABASE_URL      = https://xxxx.supabase.co
    SUPABASE_ANON_KEY = eyJ...（anon public key）
"""

import json
import os
import sys
import glob
from datetime import datetime
from pathlib import Path

# ── 读取环境变量 ──────────────────────────────────────────
def load_env():
    """尝试从 .env.local 加载环境变量（如果 python-dotenv 可用）"""
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent / '.env.local'
        if env_path.exists():
            load_dotenv(env_path)
            print(f'  ✓ 从 {env_path.name} 加载环境变量')
    except ImportError:
        pass  # 没有 python-dotenv 时从系统环境变量读取

load_env()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '').strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    print('❌ 错误：缺少 SUPABASE_URL 或 SUPABASE_ANON_KEY 环境变量')
    print('   请在 .env.local 文件中配置（参考 .env.example）')
    sys.exit(1)

# ── 找到要上传的文件 ──────────────────────────────────────
if len(sys.argv) >= 2:
    report_file = sys.argv[1]
else:
    files = sorted(glob.glob('scan_report_*.json'), reverse=True)
    if not files:
        print('❌ 找不到 scan_report_*.json 文件，请先运行 scanner.py')
        sys.exit(1)
    report_file = files[0]

print(f'📂 上传文件: {report_file}')

with open(report_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

scan_date = data.get('scan_date') or datetime.now().strftime('%Y-%m-%d')

# ── 调用 Supabase REST API（不依赖额外库）────────────────
import urllib.request
import urllib.error

payload = json.dumps({
    'scan_date': scan_date,
    'data': data,
    'created_at': datetime.utcnow().isoformat() + 'Z'
}).encode('utf-8')

url = f'{SUPABASE_URL}/rest/v1/signals'
headers = {
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    # upsert：如果同一 scan_date 已存在则更新
    'Prefer': 'resolution=merge-duplicates,return=representation',
}

req = urllib.request.Request(url, data=payload, headers=headers, method='POST')

try:
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode('utf-8')
        print(f'✅ 上传成功！HTTP {resp.status}')
        try:
            result = json.loads(body)
            if result:
                print(f'   scan_date = {result[0].get("scan_date")}，id = {result[0].get("id")}')
        except Exception:
            pass
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8')
    print(f'❌ 上传失败：HTTP {e.code}')
    print(f'   {body}')
    sys.exit(1)
except urllib.error.URLError as e:
    print(f'❌ 网络错误：{e.reason}')
    sys.exit(1)
