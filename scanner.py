#!/usr/bin/env python3
"""
scanner.py — 双条件做空信号扫描器 + 持仓平仓监控（美股短线）

筛选逻辑（放宽版）：
  条件一：日线 Stochastic(14,3,3) 超买（%K > 80 且 %D > 80）
  条件二：日线 EFI 13 熊市背离（近 30 根内价格创高但 EFI 高点下降）

  两个都满足 → 🔴 立即关注
  只满足一个 → 🟡 观察中（注明满足哪个）
  两个都不满足 → 不显示

参考信息（不作为筛选条件，显示在卡片里供人工判断）：
  - 日线 MACD 能量柱状态
  - 4小时 MACD 状态
  - 股价距 200MA 百分比

流程：
  Step 0  用 Finviz 筛选候选池（秒级，无需 IB Gateway）
  Step 1  基本面二次确认（直接读 Finviz DataFrame，无额外请求）
  Step 2  技术分析（日线 + 4h 参考，需要 IB Gateway）
  Step 3  持仓平仓监控（读 positions.json，需要 IB Gateway）
  输出    signals/YYYY-MM-DD.json

用法：
    python scanner.py                      # 完整流程，端口 4001
    python scanner.py --port 4002          # Paper Trading
    python scanner.py --symbols AAPL MSFT  # 跳过 Finviz，直接分析指定股票
    python scanner.py --finviz-only        # 只跑 Finviz 筛选，不连 IB Gateway
    python scanner.py --dry-run            # 不写文件，只打印
"""
import argparse
import json
import os
import sys
from datetime import datetime, date, timedelta

import numpy as np

# ── Windows 控制台 UTF-8 ──────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SIGNALS_DIR    = os.path.join(BASE_DIR, 'signals')
POSITIONS_FILE = os.path.join(BASE_DIR, 'positions.json')

# ── 从 .env.local 加载环境变量（与 upload_signals.py 相同逻辑）───────
def _load_env():
    from pathlib import Path
    env_path = Path(__file__).parent / '.env.local'
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

_load_env()


# ══════════════════════════════════════════════════════════════════
# Step 0：Finviz 候选池
# ══════════════════════════════════════════════════════════════════

def get_finviz_candidates() -> tuple[list[str], dict]:
    """
    用 finvizfinance 筛选候选股票。
    返回 (symbols, finviz_data_by_symbol)
      finviz_data_by_symbol: {symbol: {price, volume, market_cap, ...}}
    """
    try:
        from finvizfinance.screener.overview import Overview
    except ImportError:
        print('❌ 请先运行：pip install finvizfinance')
        sys.exit(1)

    print('🔍 Finviz 筛选候选池...')
    foverview = Overview()
    filters_dict = {
        'Country':                         'USA',
        'Price':                           'Over $30',
        'Average Volume':                  'Over 300K',
        'Market Cap.':                     '+Small (over $300mln)',
        '200-Day Simple Moving Average':   'Price 10% above SMA200',
    }
    try:
        foverview.set_filter(filters_dict=filters_dict)
        df = foverview.screener_view()
    except Exception as e:
        print(f'❌ Finviz 筛选失败：{e}')
        print('   提示：检查网络连接，或 finvizfinance 参数名称是否与 Finviz 网站一致')
        return [], {}

    if df is None or len(df) == 0:
        print('⚠ Finviz 返回空结果')
        return [], {}

    print(f'  → 候选池 {len(df)} 只股票')

    # 把 DataFrame 转成 {symbol: dict}，供基本面二次确认用
    finviz_data = {}
    for _, row in df.iterrows():
        sym = str(row.get('Ticker', '')).strip().upper()
        if not sym:
            continue
        try:
            price  = float(str(row.get('Price', 0)).replace(',', ''))
        except (ValueError, TypeError):
            price = 0.0
        try:
            vol = float(str(row.get('Volume', 0)).replace(',', ''))
        except (ValueError, TypeError):
            vol = 0.0
        finviz_data[sym] = {'price': price, 'volume': vol}

    symbols = list(finviz_data.keys())
    return symbols, finviz_data


# ══════════════════════════════════════════════════════════════════
# Step 1：基本面二次确认（用 Finviz 数据）
# ══════════════════════════════════════════════════════════════════

def fundamental_filter(symbol: str, finviz_row: dict) -> tuple[bool, dict]:
    """
    Finviz 筛选条件已经覆盖大部分，这里只做最终确认。
    返回 (pass, detail_dict)
    """
    price = finviz_row.get('price', 0)
    vol   = finviz_row.get('volume', 0)

    # Finviz 筛选已保证：market_cap > 300M, price > 30, vol > 300K, price > 1.1×SMA200
    # 二次确认同样条件（数值可能因时间差有轻微偏差）
    checks = {
        'price_gt_30':    price > 30,
        'volume_gt_300k': vol   >= 300_000,
    }
    return all(checks.values()), checks


# ══════════════════════════════════════════════════════════════════
# 技术指标
# ══════════════════════════════════════════════════════════════════

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(series), np.nan, dtype=float)
    if len(series) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.nanmean(series[:period])
    for i in range(period, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(series), np.nan, dtype=float)
    for i in range(period - 1, len(series)):
        result[i] = np.mean(series[i - period + 1: i + 1])
    return result


def calc_stochastic(highs, lows, closes, k_period=14, k_smooth=3, d_smooth=3):
    n = len(closes)
    raw_k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        lo = np.min(lows[i - k_period + 1: i + 1])
        hi = np.max(highs[i - k_period + 1: i + 1])
        raw_k[i] = 100.0 * (closes[i] - lo) / (hi - lo) if hi != lo else 50.0
    pct_k = _sma(raw_k, k_smooth)
    pct_d = _sma(pct_k, d_smooth)
    return pct_k, pct_d


def calc_efi13(closes, volumes) -> np.ndarray:
    """EFI 13：Force Index 原始值取 13 日 EMA"""
    fi = np.zeros(len(closes))
    for i in range(1, len(closes)):
        fi[i] = (closes[i] - closes[i - 1]) * volumes[i]
    return _ema(fi, 13)


def calc_macd(closes, fast=12, slow=26, signal=9):
    macd_line   = _ema(closes, fast) - _ema(closes, slow)
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bars_to_arrays(bars):
    closes  = np.array([b.close  for b in bars], dtype=float)
    highs   = np.array([b.high   for b in bars], dtype=float)
    lows    = np.array([b.low    for b in bars], dtype=float)
    volumes = np.array([b.volume for b in bars], dtype=float)
    return closes, highs, lows, volumes


def find_peaks(arr: np.ndarray, min_dist: int = 4) -> list:
    peaks = []
    for i in range(1, len(arr) - 1):
        if np.isnan(arr[i]):
            continue
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            if not peaks or (i - peaks[-1]) >= min_dist:
                peaks.append(i)
            elif arr[i] > arr[peaks[-1]]:
                peaks[-1] = i
    return peaks


def find_troughs(arr: np.ndarray, min_dist: int = 4) -> list:
    peaks = []
    for i in range(1, len(arr) - 1):
        if np.isnan(arr[i]):
            continue
        if arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            if not peaks or (i - peaks[-1]) >= min_dist:
                peaks.append(i)
            elif arr[i] < arr[peaks[-1]]:
                peaks[-1] = i
    return peaks


# ══════════════════════════════════════════════════════════════════
# Step 2：三重滤网各屏判断
# ══════════════════════════════════════════════════════════════════

def check_daily_signals(closes_d, highs_d, lows_d, volumes_d) -> dict:
    """
    日线双条件判断（放宽版）：
    条件一：Stochastic(14,3,3) 超买（%K > 80 且 %D > 80）
    条件二：EFI 13 熊市背离（近 30 根内价格创高但 EFI 高点下降）

    MACD 状态作为参考信息输出，不参与筛选。
    """
    stoch_k, stoch_d = calc_stochastic(highs_d, lows_d, closes_d)
    efi13            = calc_efi13(closes_d, volumes_d)
    _, _, hist_d     = calc_macd(closes_d)

    # ── 条件一：Stochastic 超买 ──────────────────────────────────
    k = float(stoch_k[-1]) if not np.isnan(stoch_k[-1]) else 0.0
    d = float(stoch_d[-1]) if not np.isnan(stoch_d[-1]) else 0.0
    stoch_pass = k > 80 and d > 80

    # ── 条件二：EFI 13 熊市背离（回望 30 根）─────────────────────
    c30 = closes_d[-30:]
    e30 = efi13[-30:]
    price_peaks = find_peaks(c30, min_dist=4)
    efi_div, div_strength = False, 'none'
    if len(price_peaks) >= 2:
        p1, p2 = price_peaks[-2], price_peaks[-1]
        if c30[p2] > c30[p1] and e30[p2] < e30[p1]:
            efi_div = True
            fi_drop    = (e30[p1] - e30[p2]) / abs(e30[p1]) * 100 if e30[p1] != 0 else 0
            price_rise = (c30[p2] - c30[p1]) / c30[p1] * 100
            if fi_drop > 30 or price_rise > 3:
                div_strength = 'strong'
            elif fi_drop > 15 or price_rise > 1:
                div_strength = 'moderate'
            else:
                div_strength = 'weak'

    # ── 参考：MACD 能量柱状态（不作为筛选条件）──────────────────
    h3 = [x for x in hist_d[-3:] if not np.isnan(x)]
    if len(h3) == 3:
        macd_green_fading = h3[-1] > 0 and h3[-1] < h3[-2] < h3[-3]
        if h3[-1] > 0 and h3[-2] > 0 and h3[-3] > 0:
            macd_hist_state = 'deep_green' if h3[-1] >= h3[-2] else 'light_green'
        elif h3[-1] <= 0:
            macd_hist_state = 'negative'
        else:
            macd_hist_state = 'mixed'
    else:
        macd_green_fading = False
        macd_hist_state   = 'unknown'

    # ── 综合：两个条件分别判断 ────────────────────────────────────
    conditions_met = []
    if stoch_pass:
        conditions_met.append('stoch_overbought')
    if efi_div:
        conditions_met.append('efi_divergence')

    return {
        'stoch_k':             round(k, 2),
        'stoch_d':             round(d, 2),
        'stoch_overbought':    stoch_pass,
        'efi13_divergence':    efi_div,
        'divergence_strength': div_strength,
        # 参考信息（不作为筛选条件）
        'macd_hist_state':     macd_hist_state,
        'macd_hist_shrinking': macd_green_fading,
        # 满足条件列表
        'conditions_met':      conditions_met,
        'both_pass':           stoch_pass and efi_div,
        'any_pass':            stoch_pass or efi_div,
    }


def check_4h_reference(closes_4h, volumes_4h) -> dict:
    """
    4小时参考信息（不作为筛选条件，供人工判断）：
    - MACD 状态（死叉 / histogram 负值 / 正值递减）
    - EFI 方向（向上 / 向下）
    """
    efi13        = calc_efi13(closes_4h, volumes_4h)
    ml, sl, hist = calc_macd(closes_4h)

    vm = [x for x in ml[-6:] if not np.isnan(x)]
    vs = [x for x in sl[-6:] if not np.isnan(x)]
    vh = [x for x in hist[-3:] if not np.isnan(x)]

    hist_neg   = len(vm) >= 1 and len(vs) >= 1 and vm[-1] < vs[-1]
    dead_cross = False
    if len(vm) >= 2 and len(vs) >= 2:
        for i in range(1, min(len(vm), len(vs))):
            if vm[i - 1] >= vs[i - 1] and vm[i] < vs[i]:
                dead_cross = True
                break

    # MACD 状态描述
    if dead_cross:
        macd_state = 'dead_cross'
    elif hist_neg:
        macd_state = 'negative'
    elif len(vh) >= 2 and vh[-1] < vh[-2] and vh[-1] > 0:
        macd_state = 'light_green'
    elif len(vh) >= 1 and vh[-1] > 0:
        macd_state = 'deep_green'
    else:
        macd_state = 'unknown'

    ve       = [x for x in efi13 if not np.isnan(x)]
    efi_down = len(ve) >= 2 and ve[-1] < ve[-2]

    return {
        'macd_state':         macd_state,
        'macd_dead_cross':    dead_cross,
        'macd_hist_negative': hist_neg,
        'efi_direction_down': efi_down,
    }


def check_screen3(closes_1h, volumes_1h) -> dict:
    """
    第三屏（1小时）—— 两个条件同时满足：
    1. MACD 死叉
    2. EFI 跌破零轴（prev >= 0, curr < 0）
    """
    efi13    = calc_efi13(closes_1h, volumes_1h)
    ml, sl, _ = calc_macd(closes_1h)

    vm = [x for x in ml[-6:] if not np.isnan(x)]
    vs = [x for x in sl[-6:] if not np.isnan(x)]
    dead_cross = False
    if len(vm) >= 2 and len(vs) >= 2:
        for i in range(1, min(len(vm), len(vs))):
            if vm[i - 1] >= vs[i - 1] and vm[i] < vs[i]:
                dead_cross = True
                break

    ve = [x for x in efi13 if not np.isnan(x)]
    efi_cross_zero = len(ve) >= 2 and ve[-2] >= 0 and ve[-1] < 0

    return {
        'macd_dead_cross':  dead_cross,
        'efi_cross_zero':   efi_cross_zero,
        'pass':             dead_cross and efi_cross_zero,
    }


# ══════════════════════════════════════════════════════════════════
# Step 3：持仓平仓监控
# ══════════════════════════════════════════════════════════════════

def check_close_signals(closes_d, highs_d, lows_d, volumes_d,
                        entry_price: float, entry_date_str: str) -> dict:
    """
    对已持仓空单检查平仓信号，任何一个触发即发出警告：
    1. 日线动力系统 K 线变绿（EMA13 上升 + MACD histogram 上升）
    2. 日线 EFI 底背离（价格创新低但 EFI 未创新低）
    3. 日线 MACD 能量柱由负转正（histogram[-2] < 0, histogram[-1] > 0）
    4. 持仓超过 5 个交易日但价格未低于入场价
    返回 {warning: bool, reasons: [str], detail: {...}}
    """
    efi13    = calc_efi13(closes_d, volumes_d)
    ema13_cl = _ema(closes_d, 13)
    _, _, hist_d = calc_macd(closes_d)

    reasons = []

    # ① 动力系统变绿：EMA13 连续 2 根上升 + MACD hist 上升
    ema13_rising = (not np.isnan(ema13_cl[-1]) and not np.isnan(ema13_cl[-2])
                   and ema13_cl[-1] > ema13_cl[-2])
    h2 = [x for x in hist_d[-2:] if not np.isnan(x)]
    hist_rising = len(h2) == 2 and h2[-1] > h2[-2]
    if ema13_rising and hist_rising:
        reasons.append('动力系统变绿（EMA13↑ + Histogram↑）')

    # ② EFI 底背离（近 30 根）
    c30 = closes_d[-30:]
    e30 = efi13[-30:]
    troughs_c = find_troughs(c30, min_dist=4)
    if len(troughs_c) >= 2:
        t1, t2 = troughs_c[-2], troughs_c[-1]
        if c30[t2] < c30[t1] and e30[t2] > e30[t1]:
            reasons.append('EFI 底背离（价格新低，EFI 未创新低）')

    # ③ MACD histogram 由负转正
    h_last2 = [x for x in hist_d[-2:] if not np.isnan(x)]
    if len(h_last2) == 2 and h_last2[-2] < 0 and h_last2[-1] > 0:
        reasons.append('MACD 能量柱由负转正')

    # ④ 持仓超过 5 个交易日且价格未低于入场价
    try:
        entry_dt = datetime.strptime(entry_date_str, '%Y-%m-%d').date()
        days_held = (date.today() - entry_dt).days
        trading_days_approx = int(days_held * 5 / 7)
        if trading_days_approx >= 5 and float(closes_d[-1]) >= entry_price:
            reasons.append(f'持仓约 {trading_days_approx} 个交易日，价格未低于入场价 ${entry_price}')
    except Exception:
        pass

    return {
        'warning':  len(reasons) > 0,
        'reasons':  reasons,
        'detail': {
            'ema13_rising':  ema13_rising,
            'hist_rising':   hist_rising,
            'current_price': round(float(closes_d[-1]), 2),
        },
    }


# ══════════════════════════════════════════════════════════════════
# IBKR 工具
# ══════════════════════════════════════════════════════════════════

def get_historical(ib, contract, duration, bar_size) -> list | None:
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1,
        )
        ib.sleep(0.5)
        return bars if bars else None
    except Exception as e:
        print(f'    ⚠ [{bar_size}] 数据拉取失败：{e}')
        return None


def connect_ibkr(tws_port: int, client_id: int):
    """连接 IB Gateway，返回 ib 对象"""
    try:
        from ib_insync import IB
    except ImportError:
        print('❌ 请先运行：pip install ib_insync numpy')
        sys.exit(1)

    ib = IB()
    # 压制 Error 10358（Fundamentals not subscribed）和 162（scanner cancelled）
    _QUIET = {10358, 162, 2104, 2106, 2158}
    ib.errorEvent += lambda reqId, code, msg, c: (
        None if code in _QUIET
        else print(f'  ⚠ IB Error {code}: {msg}')
    )

    print(f'🔌 连接 IB Gateway（端口 {tws_port}）...')
    try:
        ib.connect('127.0.0.1', tws_port, clientId=client_id, readonly=True)
    except Exception as e:
        print(f'❌ 连接失败：{e}')
        print('   请确认 IB Gateway 已运行，API 已启用，端口为 4001（真实账户）')
        sys.exit(1)
    print('  ✓ 已连接\n')
    return ib


# ══════════════════════════════════════════════════════════════════
# 单只股票完整技术分析
# ══════════════════════════════════════════════════════════════════

def analyze_symbol(ib, symbol: str, finviz_row: dict | None = None) -> dict | None:
    """
    返回结果 dict（只有第一屏通过才返回），或 None。
    finviz_row: 来自 Finviz 的基本面数据（可为 None，此时跳过基本面确认）
    """
    from ib_insync import Stock

    contract = Stock(symbol, 'SMART', 'USD')
    try:
        ib.qualifyContracts(contract)
    except Exception as e:
        print(f'  {symbol} ✗ 合约验证失败：{e}')
        return None

    # ── 日线（1年）——至少需要 220 根计算 200MA ────────────────────
    daily_bars = get_historical(ib, contract, '1 Y', '1 day')
    if not daily_bars or len(daily_bars) < 220:
        n = len(daily_bars) if daily_bars else 0
        print(f'  {symbol} ✗ 日线不足（{n} 根）')
        return None

    closes_d, highs_d, lows_d, volumes_d = bars_to_arrays(daily_bars)

    # ── 基本面确认（用 Finviz 数据） ──────────────────────────────
    price       = float(closes_d[-1])
    vol_today   = float(volumes_d[-1])
    ma200       = float(np.mean(closes_d[-200:]))
    pct_vs_ma200 = (price - ma200) / ma200 * 100

    if finviz_row:
        fv_pass, fv_checks = fundamental_filter(symbol, finviz_row)
        if not fv_pass:
            failed = [k for k, v in fv_checks.items() if not v]
            print(f'  {symbol} — 基本面二次确认未通过 {failed}')
            return None
    else:
        fv_checks = {}

    fundamentals = {
        'price':              round(price, 2),
        'volume_today':       int(vol_today),
        'ma200':              round(ma200, 2),
        'price_vs_ma200_pct': round(pct_vs_ma200, 2),
        'finviz_checks':      fv_checks,
    }

    # ── 日线双条件判断 ───────────────────────────────────────────
    daily = check_daily_signals(closes_d, highs_d, lows_d, volumes_d)

    # 两个条件都不满足 → 跳过
    if not daily['any_pass']:
        print(f'  {symbol} — 双条件均未通过'
              f'（Stoch={daily["stoch_overbought"]} '
              f'Div={daily["efi13_divergence"]}）')
        return None

    # ── 4小时参考数据 ────────────────────────────────────────────
    bars_4h = get_historical(ib, contract, '60 D', '4 hours')
    ref_4h  = {'macd_state': 'unknown', 'macd_dead_cross': False,
               'macd_hist_negative': False, 'efi_direction_down': False}
    if bars_4h and len(bars_4h) >= 30:
        c4, _, _, v4 = bars_to_arrays(bars_4h)
        ref_4h = check_4h_reference(c4, v4)
    else:
        print(f'  {symbol} ⚠ 4h 数据不足', end='  ')

    # ── 综合评级 ─────────────────────────────────────────────────
    conds = daily['conditions_met']
    if daily['both_pass']:
        status, icon = '立即关注', '🔴'
        unmet = []
    else:
        status, icon = '观察中', '🟡'
        unmet = [c for c in ['stoch_overbought', 'efi_divergence'] if c not in conds]

    # 观察中时注明满足了哪个条件
    cond_label = {
        'stoch_overbought': 'Stoch超买',
        'efi_divergence':   'EFI背离',
    }
    met_str  = ' + '.join(cond_label[c] for c in conds)
    unmet_str= ' + '.join(cond_label[c] for c in unmet)

    print(f'  {symbol} {icon} {status}  '
          f'满足=[{met_str}]  '
          f'%K={daily["stoch_k"]} %D={daily["stoch_d"]}  '
          f'Div={daily["divergence_strength"]}  '
          f'MACD日线={daily["macd_hist_state"]}  '
          f'距200MA {pct_vs_ma200:+.1f}%')

    return {
        'symbol':          symbol,
        'status':          status,
        'conditions_met':  conds,
        'conditions_unmet': unmet,
        'scan_timestamp':  datetime.now().isoformat(timespec='seconds'),
        'fundamentals':    fundamentals,
        'daily':           daily,      # 完整日线数据（含 MACD 参考）
        'ref_4h':          ref_4h,     # 4h MACD 参考
        # 向后兼容旧字段名（前端 UI 读取用）
        'screen1_daily':   daily,
        'screen2_4h':      ref_4h,
        'screen3_1h':      {'pass': False},
        'entry_range':     None,
        'close_warning':   None,
    }


# ══════════════════════════════════════════════════════════════════
# 持仓平仓监控
# ══════════════════════════════════════════════════════════════════

def _load_positions_supabase() -> list | None:
    """从 Supabase positions 表读取持仓中的记录。失败返回 None。"""
    import urllib.request
    url = os.environ.get('SUPABASE_URL', '').strip()
    key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if not url or not key:
        return None
    try:
        api_url = (f'{url}/rest/v1/positions'
                   f'?status=eq.持仓中'
                   f'&select=symbol,entry_price,entry_date'
                   f'&order=created_at.asc')
        req = urllib.request.Request(api_url, headers={
            'apikey':        key,
            'Authorization': f'Bearer {key}',
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        print(f'  ✓ 从 Supabase 读取 {len(data)} 条持仓记录')
        return data
    except Exception as e:
        print(f'  ⚠ Supabase 持仓读取失败，回退本地文件：{e}')
        return None


def _load_positions_local() -> list:
    """从本地 positions.json 读取持仓记录。"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'⚠ 读取 positions.json 失败：{e}')
        return []


def load_positions() -> list:
    """优先从 Supabase 读取持仓，Supabase 不可用时回退到本地 positions.json。"""
    result = _load_positions_supabase()
    if result is not None:
        return result
    print('  → 使用本地 positions.json')
    return _load_positions_local()


def monitor_positions(ib) -> list:
    """
    对 positions.json 中每只持仓股票做日线平仓信号检查。
    返回 [{symbol, entry_price, entry_date, close_signal}, ...]
    """
    from ib_insync import Stock

    positions = load_positions()
    if not positions:
        print('  （positions.json 为空或不存在，跳过持仓监控）')
        return []

    print(f'\n📋 持仓监控（{len(positions)} 只）...\n')
    results = []

    for pos in positions:
        sym          = str(pos.get('symbol', '')).upper()
        entry_price  = float(pos.get('entry_price', 0))
        entry_date   = str(pos.get('entry_date', ''))

        contract = Stock(sym, 'SMART', 'USD')
        try:
            ib.qualifyContracts(contract)
        except Exception as e:
            print(f'  {sym} ✗ 合约验证失败：{e}')
            continue

        daily_bars = get_historical(ib, contract, '1 Y', '1 day')
        if not daily_bars or len(daily_bars) < 30:
            print(f'  {sym} ✗ 日线数据不足')
            continue

        closes_d, highs_d, lows_d, volumes_d = bars_to_arrays(daily_bars)
        close_sig = check_close_signals(
            closes_d, highs_d, lows_d, volumes_d, entry_price, entry_date
        )

        current_price = float(closes_d[-1])
        pnl_pct = (entry_price - current_price) / entry_price * 100   # 做空：入-出

        icon = '🚨' if close_sig['warning'] else '✅'
        print(f'  {sym} {icon}  入场 ${entry_price}  当前 ${current_price:.2f}'
              f'  盈亏 {pnl_pct:+.1f}%'
              + (f'  → {"; ".join(close_sig["reasons"])}' if close_sig['warning'] else ''))

        results.append({
            'symbol':        sym,
            'entry_price':   entry_price,
            'entry_date':    entry_date,
            'current_price': current_price,
            'pnl_pct':       round(pnl_pct, 2),
            'close_signal':  close_sig,
        })

    return results


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def run(tws_port: int, override_symbols: list, dry_run: bool,
        client_id: int, finviz_only: bool):

    os.makedirs(SIGNALS_DIR, exist_ok=True)

    # ── Step 0：Finviz 候选池 ──────────────────────────────────────
    if override_symbols:
        symbols    = [s.upper() for s in override_symbols]
        finviz_map = {}
        print(f'📋 手动指定 {len(symbols)} 只股票，跳过 Finviz\n')
    else:
        symbols, finviz_map = get_finviz_candidates()
        if not symbols:
            print('⚠ 候选池为空，退出。')
            return

    if finviz_only:
        print(f'\n候选股票（{len(symbols)} 只）：')
        for i, s in enumerate(symbols, 1):
            print(f'  {i:3d}. {s}')
        return

    # ── 连接 IB Gateway ───────────────────────────────────────────
    ib = connect_ibkr(tws_port, client_id)

    # ── Step 2：双条件信号扫描 ────────────────────────────────────
    print(f'📊 双条件信号分析（{len(symbols)} 只候选）...\n')
    signals = []
    for i, sym in enumerate(symbols, 1):
        print(f'[{i:3d}/{len(symbols)}] ', end='', flush=True)
        try:
            result = analyze_symbol(ib, sym, finviz_map.get(sym))
        except Exception as e:
            print(f'  {sym} ✗ 异常：{e}')
            result = None
        if result:
            signals.append(result)
        ib.sleep(0.5)

    # ── Step 3：持仓监控 ──────────────────────────────────────────
    position_alerts = monitor_positions(ib)

    ib.disconnect()
    print('\n  ✓ 已断开连接')

    # ── 汇总统计 ──────────────────────────────────────────────────
    watch_now = sum(1 for r in signals if r['status'] == '立即关注')
    watching  = sum(1 for r in signals if r['status'] == '观察中')
    alerts    = sum(1 for r in position_alerts if r['close_signal']['warning'])

    # 立即关注排前
    signals.sort(key=lambda r: 0 if r['status'] == '立即关注' else 1)

    scan_date = date.today().isoformat()
    report = {
        'generated_at':    datetime.now().isoformat(timespec='seconds'),
        'scan_date':       scan_date,
        'summary': {
            'candidates': len(symbols),
            'watch_now':  watch_now,
            'watching':   watching,
            'close_alerts': alerts,
        },
        'signals':          signals,
        'position_monitor': position_alerts,
    }

    # ── 打印摘要 ───────────────────────────────────────────────────
    sep = '─' * 54
    print(f'\n{sep}')
    print(f'  扫描完成   候选 {len(symbols)} 只 → 信号 {len(signals)} 只')
    print(f'  🔴 立即关注：{watch_now}')
    print(f'  🟡 观察中：  {watching}')
    if alerts:
        print(f'  🚨 持仓平仓警告：{alerts}')

    if watch_now:
        print(f'\n  {"─"*18} 立即关注（双条件全满足）{"─"*18}')
        for r in signals:
            if r['status'] != '立即关注':
                continue
            f  = r['fundamentals']
            d  = r['daily']
            print(f"  {r['symbol']:<6}  ${f['price']}  "
                  f"距200MA {f['price_vs_ma200_pct']:+.1f}%  "
                  f"%K={d['stoch_k']} %D={d['stoch_d']}  "
                  f"背离={d['divergence_strength']}  "
                  f"MACD={d['macd_hist_state']}")

    if watching:
        print(f'\n  {"─"*20} 观察中（满足其中一个条件）{"─"*20}')
        for r in signals:
            if r['status'] != '观察中':
                continue
            d  = r['daily']
            f  = r['fundamentals']
            met   = ' + '.join(r.get('conditions_met', []))
            unmet = ' + '.join(r.get('conditions_unmet', []))
            print(f"  {r['symbol']:<6}  满足=[{met}]  未满足=[{unmet}]  "
                  f"%K={d['stoch_k']} %D={d['stoch_d']}  "
                  f"背离={d['divergence_strength']}  "
                  f"距200MA {f['price_vs_ma200_pct']:+.1f}%")

    if position_alerts:
        print(f'\n  {"─"*18} 持仓监控 {"─"*18}')
        for r in position_alerts:
            sig  = r['close_signal']
            icon = '🚨' if sig['warning'] else '✅'
            pnl  = r['pnl_pct']
            line = (f"  {icon} {r['symbol']:<6}  入场 ${r['entry_price']}"
                    f"  当前 ${r['current_price']}  盈亏 {pnl:+.1f}%")
            if sig['warning']:
                line += f"\n       ⚠ {' | '.join(sig['reasons'])}"
            print(line)

    print(f'{sep}')

    if dry_run:
        print('\n⚠  --dry-run 模式，不写入文件。')
        return

    class _Encoder(json.JSONEncoder):
        """把 numpy bool/int/float 转成 Python 原生类型再序列化"""
        def default(self, obj):
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    out_file = os.path.join(SIGNALS_DIR, f'{scan_date}.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, cls=_Encoder)
    print(f'\n✅ 报告已保存：{out_file}')


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='三重滤网做空信号扫描器',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--port', type=int, default=4001,
        help='IB Gateway 端口（默认 4001）')
    parser.add_argument('--symbols', nargs='+', metavar='SYM',
        help='跳过 Finviz，直接分析这些股票代码')
    parser.add_argument('--finviz-only', action='store_true',
        help='只运行 Finviz 筛选，打印候选池，不连接 IB Gateway')
    parser.add_argument('--client-id', type=int, default=98,
        help='IBKR API 客户端 ID（默认 98）')
    parser.add_argument('--dry-run', action='store_true',
        help='不写入 JSON 文件，只打印结果')
    args = parser.parse_args()

    run(
        tws_port=args.port,
        override_symbols=args.symbols or [],
        dry_run=args.dry_run,
        client_id=args.client_id,
        finviz_only=args.finviz_only,
    )
