#!/usr/bin/env python3
"""
sync_ibkr.py — IBKR 成交记录同步到交易日记

用法：
    python sync_ibkr.py                 # 真实账户 IB Gateway 4001，拉取近 30 天
    python sync_ibkr.py --port 4002     # Paper Trading
    python sync_ibkr.py --days 7        # 只拉近 7 天
    python sync_ibkr.py --dry-run       # 只预览，不写入

前提：IB Gateway 正在运行，且已在
      Configure → API → Settings 中勾选 Enable ActiveX and Socket Clients
      Socket port 设为 4001（真实）或 4002（Paper）
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Windows 控制台 UTF-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ─── 路径 ──────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_FILE      = os.path.join(BASE_DIR, 'trades.json')
PROCESSED_FILE = os.path.join(BASE_DIR, 'ibkr_processed.json')

# 手动填写的字段——同步时保留用户已填内容，不覆盖
USER_FIELDS = [
    'reasonForEntry', 'entryCharts',
    'reasonForExit',  'exitCharts',
    'exitTactic', 'stopLoss', 'profitTarget',
    'postTradeAnalysis', 'analysisCharts',
    'tradeGrade', 'tradeLetter',
]


# ══════════════════════════════════════════════════════════════════
# 数据 IO
# ══════════════════════════════════════════════════════════════════
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {'trades': data, 'tactics': []}
        return data
    return {'trades': [], 'tactics': []}


def save_data(data: dict):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'  ✓ 已写入 {DATA_FILE}  (共 {len(data["trades"])} 条记录)')


def load_processed() -> set:
    """返回已处理过的 execId 集合，防止重复导入"""
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, 'r') as f:
            return set(json.load(f))
    return set()


def save_processed(ids: set):
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(sorted(ids), f, indent=2)


# ══════════════════════════════════════════════════════════════════
# Fill → 交易日记行格式
# ══════════════════════════════════════════════════════════════════
def fill_to_row(fill) -> dict:
    """把一条 IBKR Fill 转成 entries/exits 中的一行"""
    ex = fill.execution
    dt = ex.time
    date_str = dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)[:10]

    price  = float(ex.avgPrice) if ex.avgPrice else float(ex.price)
    shares = abs(int(ex.shares))

    return {
        'date':        date_str,
        'orderPrice':  0,                       # IBKR API 不直接提供委托价，留空
        'filledPrice': round(price, 4),
        'slippage':    0,
        'shares':      shares,
        'totalCost':   round(price * shares, 2),
        'dayHigh':     0,                       # 手动填写
        'dayLow':      0,                       # 手动填写
        'grade':       None,
    }


def build_trade(symbol: str, direction: str,
                entry_fills: list, exit_fills: list,
                contract=None) -> dict:
    """
    把入场 / 出场 fills 组装成完整的交易日记 trade 对象。
    空白字段（分析、评分等）由用户手动填写。
    """
    entry_rows = [fill_to_row(f) for f in entry_fills]
    exit_rows  = [fill_to_row(f) for f in exit_fills]

    tot_en_sh = sum(r['shares'] for r in entry_rows)
    tot_ex_sh = sum(r['shares'] for r in exit_rows)

    avg_en = (sum(r['filledPrice'] * r['shares'] for r in entry_rows) / tot_en_sh
              if tot_en_sh else 0)
    avg_ex = (sum(r['filledPrice'] * r['shares'] for r in exit_rows) / tot_ex_sh
              if tot_ex_sh else 0)

    # 做空：入-出 为盈；做多：出-入 为盈
    mult        = -1 if direction == 'short' else 1
    closed_sh   = min(tot_en_sh, tot_ex_sh)
    gain_loss   = round(mult * (avg_ex - avg_en) * closed_sh, 2)   if (avg_en and avg_ex) else 0
    gain_loss_p = round(mult * (avg_ex - avg_en) / avg_en * 100, 4) if avg_en            else 0

    status = 'closed' if tot_ex_sh >= tot_en_sh > 0 else 'open'

    entry_dates = sorted(r['date'] for r in entry_rows if r['date'])
    exit_dates  = sorted(r['date'] for r in exit_rows  if r['date'])

    # 交易所 / 市场
    market = ''
    if contract:
        market = getattr(contract, 'primaryExch', '') or getattr(contract, 'exchange', '') or ''

    # 用入场第一笔的 execId 作唯一 ID
    all_exec_ids = ([f.execution.execId for f in entry_fills] +
                   [f.execution.execId for f in exit_fills])
    trade_id = all_exec_ids[0].replace('.', '_') if all_exec_ids else \
               str(int(datetime.now().timestamp() * 1000))

    return {
        # ── 自动计算字段 ──────────────────────────
        'id':              trade_id,
        'symbol':          symbol,
        'market':          market,
        'sector':          '',
        'direction':       direction,
        'status':          status,
        'entries':         entry_rows,
        'exits':           exit_rows,
        'gainLoss':        gain_loss,
        'gainLossPct':     gain_loss_p,
        'avgEntryPrice':   round(avg_en, 4),
        'avgExitPrice':    round(avg_ex, 4),
        'totalShares':     max(tot_en_sh, tot_ex_sh),
        'firstEntryDate':  entry_dates[0] if entry_dates else None,
        'lastExitDate':    exit_dates[-1]  if exit_dates  else None,
        'updatedAt':       datetime.now().isoformat(),
        '_execIds':        all_exec_ids,    # 内部追踪字段
        # ── 手动填写字段（留空）────────────────────
        'reasonForEntry':    '',
        'entryCharts':       [None, None],
        'reasonForExit':     '',
        'exitCharts':        [None, None],
        'exitTactic':        '',
        'stopLoss':          None,
        'profitTarget':      None,
        'postTradeAnalysis': '',
        'analysisCharts':    [None],
        'tradeGrade':        None,
        'tradeLetter':       None,
    }


# ══════════════════════════════════════════════════════════════════
# 入场 / 出场匹配（基于持仓量变化）
# ══════════════════════════════════════════════════════════════════
def match_trades_for_symbol(fills: list) -> list:
    """
    对单一标的的所有 fills 做持仓追踪，返回：
        [(direction, entry_fills, exit_fills), ...]

    做空逻辑：
        SLD（卖出开仓）→ 持仓变负  → 视为入场
        BOT（买入平仓）→ 持仓归零  → 视为出场

    做多逻辑：
        BOT → 持仓变正 → 入场
        SLD → 持仓归零 → 出场
    """
    fills_sorted = sorted(fills, key=lambda f: f.execution.time)

    trades    = []
    position  = 0       # 正数 = 多头，负数 = 空头
    direction = None
    cur_entry = []
    cur_exit  = []

    for fill in fills_sorted:
        ex    = fill.execution
        qty   = abs(int(ex.shares))
        delta = qty if ex.side == 'BOT' else -qty
        old   = position
        position += delta

        if old == 0:
            # 开新仓
            direction = 'long' if delta > 0 else 'short'
            cur_entry = [fill]
            cur_exit  = []

        elif (old > 0 and delta > 0) or (old < 0 and delta < 0):
            # 加仓（同向扩大）
            cur_entry.append(fill)

        elif position == 0:
            # 平仓完毕
            cur_exit.append(fill)
            trades.append((direction, list(cur_entry), list(cur_exit)))
            cur_entry = []
            cur_exit  = []
            direction = None

        elif (old > 0) == (position > 0):
            # 部分平仓（方向未改变，往 0 靠近）
            cur_exit.append(fill)

        else:
            # 仓位穿越 0：先平仓，再反向开仓
            cur_exit.append(fill)
            trades.append((direction, list(cur_entry), list(cur_exit)))
            direction = 'long' if position > 0 else 'short'
            cur_entry = [fill]
            cur_exit  = []

    # 持仓尚未平仓（open trade）
    if cur_entry:
        trades.append((direction, list(cur_entry), list(cur_exit)))

    return trades


# ══════════════════════════════════════════════════════════════════
# 与现有 trades.json 合并
# ══════════════════════════════════════════════════════════════════
def merge_into_existing(data: dict, new_trades: list) -> tuple[int, int]:
    """
    把新交易合并进现有 data['trades']：
      - 已有记录（通过 _execIds 重叠判断）→ 更新价格/盈亏，保留用户手填字段
      - 全新记录 → 插到最前面
    返回 (added, updated)
    """
    added, updated = 0, 0

    for trade in new_trades:
        new_ids = set(trade.get('_execIds', []))

        # 找有没有重叠 execId 的现有记录
        match = next(
            (t for t in data['trades']
             if new_ids & set(t.get('_execIds', []))),
            None
        )

        if match:
            # 更新：保留用户手填字段
            idx = data['trades'].index(match)
            for f in USER_FIELDS:
                trade[f] = match.get(f, trade[f])
            trade['id'] = match['id']   # 保持原 ID
            data['trades'][idx] = trade
            updated += 1
        else:
            data['trades'].insert(0, trade)
            added += 1

    return added, updated


# ══════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════
def sync(tws_port: int, days_back: int, client_id: int, dry_run: bool):
    # 1. 连接 IBKR
    try:
        from ib_insync import IB, ExecutionFilter
    except ImportError:
        print('❌ 请先运行：pip install ib_insync')
        sys.exit(1)

    print(f'\n🔌 正在连接 IBKR（端口 {tws_port}）...')
    ib = IB()
    try:
        ib.connect('127.0.0.1', tws_port, clientId=client_id, readonly=True)
    except Exception as e:
        print(f'❌ 连接失败：{e}')
        print()
        print('请确认：')
        print('  1. IB Gateway 正在运行')
        print('  2. IB Gateway → Configure → API → Settings')
        print('     已勾选 "Enable ActiveX and Socket Clients"')
        print('  3. Socket port 设置为 4001（真实账户）或 4002（Paper）')
        sys.exit(1)

    print('  ✓ 已连接')

    # 2. 拉取成交记录
    since     = datetime.now() - timedelta(days=days_back)
    since_str = since.strftime('%Y%m%d %H:%M:%S')   # IBKR 格式：YYYYMMDD HH:MM:SS
    print(f'\n📥 拉取近 {days_back} 天的成交记录（自 {since.strftime("%Y-%m-%d")}）...')

    try:
        ef    = ExecutionFilter(time=since_str)
        fills = ib.reqExecutions(ef)

        # 如果 reqExecutions 返回空，再试 fills()（当前会话）
        if not fills:
            fills = ib.fills()

    except Exception as e:
        print(f'❌ 拉取失败：{e}')
        ib.disconnect()
        sys.exit(1)

    ib.disconnect()
    print(f'  ✓ 已断开连接')
    print(f'  → 共找到 {len(fills)} 条成交记录')

    # 3. 只保留股票（排除期权、期货等）
    stock_fills = [f for f in fills if getattr(f.contract, 'secType', '') == 'STK']
    print(f'  → 股票成交 {len(stock_fills)} 条')

    if not stock_fills:
        print('\n⚠️  没有股票成交记录，退出。')
        return

    # 4. 过滤已处理的 execId
    processed = load_processed()
    symbols   = defaultdict(list)
    for f in stock_fills:
        symbols[f.contract.symbol].append(f)

    print(f'  → 涉及标的：{", ".join(sorted(symbols.keys()))}')

    # 5. 匹配交易对
    new_trades = []
    for symbol, sym_fills in symbols.items():
        matched = match_trades_for_symbol(sym_fills)
        for direction, entries, exits in matched:
            all_ids = {f.execution.execId for f in entries + exits}

            # 如果所有 execId 都已处理过，跳过
            if all_ids and all_ids.issubset(processed):
                continue

            contract = (entries[0].contract if entries
                        else exits[0].contract if exits else None)
            trade = build_trade(symbol, direction, entries, exits, contract)
            new_trades.append(trade)
            processed.update(all_ids)

    if not new_trades:
        print('\nℹ️  没有新交易需要同步（所有成交均已处理过）。')
        return

    print(f'\n📋 识别到 {len(new_trades)} 笔交易：')
    for t in new_trades:
        status_str = '已平仓' if t['status'] == 'closed' else '持仓中'
        pl_str     = (f"  盈亏 {t['gainLoss']:+.2f}"
                      if t['status'] == 'closed' else '  (尚未平仓)')
        print(f"  {t['direction'].upper():5s}  {t['symbol']:<8s} "
              f"{t.get('firstEntryDate','?')} → {t.get('lastExitDate','?') or '—'}"
              f"  [{status_str}]{pl_str}")

    if dry_run:
        print('\n⚠️  --dry-run 模式，不写入文件。')
        return

    # 6. 合并写入
    data             = load_data()
    added, updated   = merge_into_existing(data, new_trades)
    save_data(data)
    save_processed(processed)

    print(f'\n✅ 完成！新增 {added} 条，更新 {updated} 条。')
    print(f'   打开交易日记网页即可查看（若已打开，刷新页面）。')


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='IBKR 成交记录 → 交易日记 同步工具',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        '--port', type=int, default=4001,
        help='IB Gateway 端口\n  4001 = 真实账户（默认）\n  4002 = Paper Trading',
    )
    parser.add_argument(
        '--days', type=int, default=30,
        help='拉取最近几天的记录（默认 30）',
    )
    parser.add_argument(
        '--client-id', type=int, default=99,
        help='IBKR API 客户端 ID（默认 99，避免与其他程序冲突）',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='只预览，不写入 trades.json',
    )
    args = parser.parse_args()

    sync(
        tws_port=args.port,
        days_back=args.days,
        client_id=args.client_id,
        dry_run=args.dry_run,
    )
