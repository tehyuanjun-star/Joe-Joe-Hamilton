#!/usr/bin/env python3
"""
migrate_positions.py — 一次性迁移：把本地 positions.json 导入 Supabase positions 表

用法：
    python migrate_positions.py           # 迁移，完成后提示是否可以删除本地文件
    python migrate_positions.py --dry-run # 预览，不写入 Supabase

环境变量（同 upload_signals.py，可放在 .env.local）：
    SUPABASE_URL      = https://xxxx.supabase.co
    SUPABASE_ANON_KEY = eyJ...
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# Fix Windows terminal encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR       = Path(__file__).parent
POSITIONS_FILE = BASE_DIR / 'positions.json'


# ── 加载 .env.local ────────────────────────────────────────────────
def load_env():
    env_path = BASE_DIR / '.env.local'
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_env()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '').strip()

dry_run = '--dry-run' in sys.argv


# ── 检查环境变量 ───────────────────────────────────────────────────
if not SUPABASE_URL or not SUPABASE_KEY:
    print('❌ 缺少环境变量 SUPABASE_URL 或 SUPABASE_ANON_KEY')
    print('   在 .env.local 中添加这两个变量，参考 .env.example')
    sys.exit(1)


# ── 读取本地 positions.json ────────────────────────────────────────
if not POSITIONS_FILE.exists():
    print('❌ 找不到 positions.json，没有需要迁移的数据。')
    sys.exit(0)

with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
    local_positions = json.load(f)

if not local_positions:
    print('⚠ positions.json 为空，无需迁移。')
    sys.exit(0)

print(f'📋 本地持仓记录 {len(local_positions)} 条：')
for p in local_positions:
    print(f'   {p.get("symbol"):<8} 入场 ${p.get("entry_price")}  日期 {p.get("entry_date")}')

if dry_run:
    print('\n⚠  --dry-run 模式，不写入 Supabase。')
    sys.exit(0)

print()

# ── 上传到 Supabase ────────────────────────────────────────────────
# 本地 positions.json 没有 trade id，用 symbol 作为 id（迁移专用）
# 格式：migrate_<symbol>（避免与交易日记的 id 冲突）
rows = []
for p in local_positions:
    sym = str(p.get('symbol', '')).upper().strip()
    if not sym:
        continue
    rows.append({
        'id':          f'migrate_{sym}',
        'symbol':      sym,
        'entry_price': float(p['entry_price']) if p.get('entry_price') else None,
        'entry_date':  p.get('entry_date') or None,
        'status':      '持仓中',
        'updated_at':  datetime.now(timezone.utc).isoformat(),
    })

payload = json.dumps(rows).encode('utf-8')
url = f'{SUPABASE_URL}/rest/v1/positions?on_conflict=id'
headers = {
    'Content-Type':  'application/json',
    'apikey':        SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Prefer':        'resolution=merge-duplicates,return=representation',
}

req = urllib.request.Request(url, data=payload, headers=headers, method='POST')

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    print(f'✅ 成功写入 {len(result)} 条持仓记录到 Supabase：')
    for r in result:
        print(f'   {r.get("symbol"):<8} 入场 ${r.get("entry_price")}  状态 {r.get("status")}')
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8')
    print(f'❌ 写入失败：HTTP {e.code}')
    print(f'   {body}')
    sys.exit(1)
except urllib.error.URLError as e:
    print(f'❌ 网络错误：{e.reason}')
    sys.exit(1)

print()
print('迁移完成！')
print()
print('后续说明：')
print('  · 本地 positions.json 仍然保留，可作为回退备用。')
print('  · 扫描器现在会优先读取 Supabase，只有 Supabase 不可用时才读本地文件。')
print('  · 之后新增/平仓 → 在交易日记网页保存交易，Supabase 会自动同步。')
print('  · 如确认不再需要本地文件，可手动删除 positions.json。')
