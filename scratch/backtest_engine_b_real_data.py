"""
===============================================================================
     ENGINE B: REAL-DATA MCX CRUDE OIL 1-YEAR BACKTEST (DATA-DRIVEN)
===============================================================================
Drives the OPTIMIZED Engine B Quantitative Strategy with REAL market data
instead of the synthetic Monte-Carlo draws used in backtest_crude_engine_b_6m.py.

Data source:
  - Official MCX CRUDEOIL daily SPOT (INR) archive via the `mcx-data` PyPI
    package (scrapes MCX India with Chrome TLS impersonation).
  - Window: 2025-08-01 -> 2026-08-11 (263 trading days, >= 1 year).
  - Cached locally at data_cache/mcx_crude_spot_1y.csv.

Data granularity note:
  - MCX spot archive is CLOSE-ONLY (no intraday OHLC / volume).
  - We therefore set high = low = close, so:
      * VWAP  -> volume-free cumulative typical-price mean (uses volume=1)
      * ATR   -> close-to-close true range (|close - prev_close|)
      * Supertrend/EMA -> computed on the close series with identical algorithms
    All Engine B scoring gates remain percentage/score-based and scale-free.

Models kept EXACTLY identical to the live repo (nothing changed):
  - calculate_optimized_engine_b_score()    (backtest_crude_engine_b_6m.py)
  - calculate_vwap / ema / atr / supertrend  (scanner/crude_scanner.py)
  - calculate_trade_friction()               (reporting/friction_calculator.py)
  - config/settings.py gates (score >= 80, TP +25%, SL -12%, TSL +8%/+15%->+1%/+10%)

Modeling choices (documented, no repo files modified):
  - ATM option premium proxy  = 0.015 x spot (exact level the live Engine B
    option mapper prices: option_mapper.py estimated_premium = spot * 0.015).
  - Premium move mapped via delta = 0.52 on the day's high/low/close move.
  - EOD square-off model: signal on day D close -> entry at signal-day close,
    hold through the next trading session, exit at next session's close
    (mirrors MCX 23:00 square-off). The next session's high/low is proxied
    from the entry and exit closes (conservative: ignores intraday wicks).
    TP/SL/TSL checked against the proxy high/low; stop checked first.
  - EIA blackout: all Wednesdays skipped (20:00 IST release window overlaps
    the 17:00-23:00 session).
  - Regime gate: real ADX(14) >= 25 (MIN_ADX_TREND_STRENGTH) computed on the
    close series. Macro-news score set to 0.0 (no historical news feed).
===============================================================================
"""

import os
import sys
import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config.settings import (
    INITIAL_WALLET_CAPITAL,
    MICRO_CAPITAL_BUDGET_CAP,
    QUALIFICATION_SCORE_THRESHOLD,
    MIN_MICRO_PREMIUM_INR,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    TSL_STEP1_TRIGGER_PCT,
    TSL_STEP1_LOCK_PCT,
    TSL_STEP2_TRIGGER_PCT,
    TSL_STEP2_LOCK_PCT,
    MIN_ADX_TREND_STRENGTH,
    MCX_CRUDE_MINI_LOT_SIZE,
    MCX_CRUDE_LOT_SIZE,
)
from reporting.friction_calculator import calculate_trade_friction
from scanner.crude_scanner import calculate_vwap, calculate_ema, calculate_atr, calculate_supertrend
from backtest_crude_engine_b_6m import calculate_optimized_engine_b_score

DATA_CACHE = os.path.join(BASE_DIR, "data_cache", "mcx_crude_spot_1y.csv")
REPORT_PREFIX = "crude_engine_b_realdata"

PREMIUM_RATIO = 0.015        # live Engine B ATM premium = 1.5% of spot
ATM_DELTA = 0.52             # live Engine B estimated delta
MIN_PREMIUM_FLOOR = 1.0      # clamp premium proxy values


def load_mcx_spot_series() -> pd.DataFrame:
    """Loads the cached official MCX CRUDEOIL spot series (INR, daily)."""
    if not os.path.exists(DATA_CACHE):
        sys.exit(f"[Data Error] Cache not found: {DATA_CACHE}. Run the fetch step first.")
    df = pd.read_csv(DATA_CACHE)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df = df.rename(columns={"Spot Price (Rs.)": "close"})
    df["close"] = df["close"].astype(float)
    # Close-only archive: proxy OHLC from the close series.
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["open"] = df["close"]
    df["volume"] = 1.0
    return df[["Date", "open", "high", "low", "close", "volume"]]


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


def compute_adx(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ADX on close-only series (high=low=close)."""
    prev_close = close.shift(1)
    up_move = close - prev_close
    down_move = prev_close - close
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = np.abs(close - prev_close).fillna(0.0).values
    alpha = 1.0 / period
    atr = pd.Series(tr, index=close.index).ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=close.index).ewm(alpha=alpha, adjust=False).mean() / atr.replace(0.0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=close.index).ewm(alpha=alpha, adjust=False).mean() / atr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx.fillna(0.0)


def simulate_day_trade(direction: str, entry_spot: float, exit_close: float) -> dict:
    """
    EOD square-off trade simulation over the next trading session.

    Entry fills at the signal-day close (entry_spot). The next session's
    high/low is proxied conservatively from (entry_spot, exit_close).

    Returns exit_premium + exit_reason.
    Stop is evaluated first (conservative worst-case ordering).
    """
    p0 = round(entry_spot * PREMIUM_RATIO, 2)
    day_high = max(entry_spot, exit_close)
    day_low = min(entry_spot, exit_close)

    if direction == "BULLISH":  # CE
        premium_high = p0 + ATM_DELTA * (day_high - entry_spot)
        premium_low = p0 + ATM_DELTA * (day_low - entry_spot)
        premium_close = p0 + ATM_DELTA * (exit_close - entry_spot)
    else:  # PE
        premium_high = p0 + ATM_DELTA * (entry_spot - day_low)
        premium_low = p0 + ATM_DELTA * (entry_spot - day_high)
        premium_close = p0 + ATM_DELTA * (entry_spot - exit_close)

    premium_high = max(premium_high, MIN_PREMIUM_FLOOR)
    premium_low = max(premium_low, MIN_PREMIUM_FLOOR)
    premium_close = max(premium_close, MIN_PREMIUM_FLOOR)

    if premium_low <= p0 * (1.0 - STOP_LOSS_PCT):
        return {"exit_premium": round(p0 * (1.0 - STOP_LOSS_PCT), 2), "exit_reason": "STOP_LOSS_HIT_-12%"}
    if premium_high >= p0 * (1.0 + TAKE_PROFIT_PCT):
        return {"exit_premium": round(p0 * (1.0 + TAKE_PROFIT_PCT), 2), "exit_reason": "TARGET_HIT_+25%"}
    peak_gain = premium_high / p0 - 1.0
    if peak_gain >= TSL_STEP2_TRIGGER_PCT:
        return {"exit_premium": round(p0 * (1.0 + TSL_STEP2_LOCK_PCT), 2), "exit_reason": "TSL_STEP2_LOCK_HIT"}
    if peak_gain >= TSL_STEP1_TRIGGER_PCT:
        return {"exit_premium": round(p0 * (1.0 + TSL_STEP1_LOCK_PCT), 2), "exit_reason": "TSL_STEP1_LOCK_HIT"}
    return {"exit_premium": round(premium_close, 2), "exit_reason": "TIME_STAGNATION_EXIT_EOD"}


def analyze(df: pd.DataFrame, init_cap: float, final_cap: float) -> dict:
    if df is None or len(df) == 0:
        return {}
    win = df[df["net_pnl"] > 0]
    loss = df[df["net_pnl"] <= 0]
    w_cnt = len(win)
    l_cnt = len(loss)
    w_rate = (w_cnt / len(df) * 100.0)
    g_prof = win["net_pnl"].sum() if w_cnt > 0 else 0.0
    g_loss = abs(loss["net_pnl"].sum()) if l_cnt > 0 else 0.0
    pf = (g_prof / g_loss) if g_loss > 0 else 99.0
    net_amt = final_cap - init_cap
    ret_pct = (net_amt / init_cap) * 100.0
    tot_fric = df["friction"].sum()
    eq_arr = df["wallet_balance"].values if "wallet_balance" in df.columns else np.array([init_cap, final_cap])
    peak = np.maximum.accumulate(eq_arr)
    dd = (peak - eq_arr) / peak
    max_dd = float(np.max(dd) * 100.0) if len(dd) > 0 else 0.0
    return {
        "total_trades": len(df), "winning": w_cnt, "losing": l_cnt,
        "win_rate": round(w_rate, 2), "gross_profit": round(g_prof, 2),
        "gross_loss": round(g_loss, 2), "total_friction": round(tot_fric, 2),
        "net_pnl": round(net_amt, 2), "ret_pct": round(ret_pct, 2),
        "profit_factor": round(pf, 2), "max_dd_pct": round(max_dd, 2),
    }


def run_engine_b_real_data_backtest():
    print("=" * 95)
    print("      ENGINE B: MCX CRUDE OIL REAL-DATA 1-YEAR BACKTEST (DATA-DRIVEN)      ")
    print(f"      Strategy Matrix: 100-Point Composite Model (Threshold >= {QUALIFICATION_SCORE_THRESHOLD:.0f} Pts)")
    print(f"      Optimized TSL: +{TSL_STEP1_TRIGGER_PCT*100:.0f}% -> +{TSL_STEP1_LOCK_PCT*100:.0f}% | +{TSL_STEP2_TRIGGER_PCT*100:.0f}% -> +{TSL_STEP2_LOCK_PCT*100:.0f}%")
    print("=" * 95)

    df = load_mcx_spot_series()
    n = len(df)
    print(f"\n[Data] Official MCX CRUDEOIL daily spot (INR): {n} rows")
    print(f"[Data] Window: {df['Date'].iloc[0].date()} -> {df['Date'].iloc[-1].date()}")
    print(f"[Data] Spot range: Rs {df['close'].min():,.2f} - Rs {df['close'].max():,.2f} | "
          f"Mean: Rs {df['close'].mean():,.2f}")

    # Compute indicators (identical algorithms to the live engine)
    df["vwap"] = calculate_vwap(df)
    df["ema_20"] = calculate_ema(df["close"], period=20)
    df["atr_14"] = calculate_atr(df, period=14)
    st_val, st_dir = calculate_supertrend(df, period=7, multiplier=3.0)
    df["supertrend_val"] = st_val
    df["supertrend_dir"] = st_dir
    df["rsi_14"] = compute_rsi(df["close"], period=14)
    df["adx_14"] = compute_adx(df["close"], period=14)
    atr_20 = df["atr_14"].rolling(20, min_periods=5).mean()
    df["vol_spike"] = (df["atr_14"] / atr_20.replace(0, np.nan)).clip(lower=1.0)

    # Wallet / counters
    wallet_micro = INITIAL_WALLET_CAPITAL
    wallet_std = 25000.0
    equity_micro = [wallet_micro]
    equity_std = [wallet_std]
    trade_log_micro = []
    trade_log_std = []
    signal_log = []

    rejected_eia = 0
    rejected_regime = 0
    rejected_alignment = 0
    rejected_score = 0
    rejected_no_next_day = 0
    rejected_micro_budget = 0
    rejected_micro_premium = 0

    for i in range(n - 1):
        row = df.iloc[i]
        day = row["Date"].date()

        # 1. EIA blackout: skip Wednesdays (20:00 IST release overlaps session)
        if day.weekday() == 2:
            rejected_eia += 1
            continue

        # 2. Market regime gate: real ADX(14) >= 25
        if pd.isna(row["adx_14"]) or row["adx_14"] < MIN_ADX_TREND_STRENGTH:
            rejected_regime += 1
            continue

        # 3. Engine B 3-way technical alignment
        close = float(row["close"])
        vwap = float(row["vwap"])
        ema20 = float(row["ema_20"])
        st_dir_latest = int(row["supertrend_dir"])
        is_bullish = (close > vwap) and (close > ema20) and st_dir_latest == 1
        is_bearish = (close < vwap) and (close < ema20) and st_dir_latest == -1
        if not is_bullish and not is_bearish:
            rejected_alignment += 1
            continue

        direction = "BULLISH" if is_bullish else "BEARISH"
        option_type = "CE" if is_bullish else "PE"

        # 4. Composite score (identical Engine B matrix)
        score, reason = calculate_optimized_engine_b_score(
            trend_aligned=True,
            rsi_val=float(row["rsi_14"]),
            vol_spike=float(row["vol_spike"]),
            macro_news_score=0.0
        )
        if score < QUALIFICATION_SCORE_THRESHOLD:
            rejected_score += 1
            continue

        # 5. Execute EOD square-off trade on the NEXT trading session
        #    Entry fills at the signal-day close; exit at next session close.
        next_row = df.iloc[i + 1]
        entry_spot = close
        day_close = float(next_row["close"])
        trade_day = next_row["Date"].date()

        p0 = round(entry_spot * PREMIUM_RATIO, 2)
        sim = simulate_day_trade(direction, entry_spot, day_close)
        exit_premium = sim["exit_premium"]
        exit_reason = sim["exit_reason"]

        # --- Mode 1: Micro-Capital (CRUDEOILM, 10 barrels, Rs 500 cap) ---
        lot_micro = MCX_CRUDE_MINI_LOT_SIZE
        if p0 < MIN_MICRO_PREMIUM_INR:
            rejected_micro_premium += 1
        elif p0 * lot_micro > MICRO_CAPITAL_BUDGET_CAP:
            rejected_micro_budget += 1
        else:
            f_micro = calculate_trade_friction(lot_micro, p0, exit_premium)
            wallet_micro += f_micro["net_pnl"]
            equity_micro.append(wallet_micro)
            trade_log_micro.append({
                "trade_no": len(trade_log_micro) + 1, "signal_date": day.isoformat(),
                "trade_date": trade_day.isoformat(), "symbol": "CRUDEOILM",
                "direction": direction, "option_type": option_type, "lot_size": lot_micro,
                "entry_spot": round(entry_spot, 2), "entry_premium": p0,
                "exit_premium": exit_premium, "gross_pnl": f_micro["gross_pnl"],
                "friction": f_micro["total_friction"], "net_pnl": f_micro["net_pnl"],
                "exit_reason": exit_reason, "composite_score": score,
                "rsi": round(float(row["rsi_14"]), 1), "vol_spike": round(float(row["vol_spike"]), 2),
                "adx": round(float(row["adx_14"]), 1), "wallet_balance": round(wallet_micro, 2),
            })

        # --- Mode 2: Standard Account (CRUDEOIL, 100 barrels, Rs 25,000 wallet) ---
        lot_std = MCX_CRUDE_LOT_SIZE
        f_std = calculate_trade_friction(lot_std, p0, exit_premium)
        wallet_std += f_std["net_pnl"]
        equity_std.append(wallet_std)
        trade_log_std.append({
            "trade_no": len(trade_log_std) + 1, "signal_date": day.isoformat(),
            "trade_date": trade_day.isoformat(), "symbol": "CRUDEOIL",
            "direction": direction, "option_type": option_type, "lot_size": lot_std,
            "entry_spot": round(entry_spot, 2), "entry_premium": p0,
            "exit_premium": exit_premium, "gross_pnl": f_std["gross_pnl"],
            "friction": f_std["total_friction"], "net_pnl": f_std["net_pnl"],
            "exit_reason": exit_reason, "composite_score": score,
            "rsi": round(float(row["rsi_14"]), 1), "vol_spike": round(float(row["vol_spike"]), 2),
            "adx": round(float(row["adx_14"]), 1), "wallet_balance": round(wallet_std, 2),
        })
        signal_log.append({
            "signal_date": day.isoformat(), "trade_date": trade_day.isoformat(),
            "direction": direction, "option_type": option_type, "spot": round(close, 2),
            "entry_spot": round(entry_spot, 2), "vwap": round(vwap, 2), "ema_20": round(ema20, 2),
            "supertrend_dir": st_dir_latest, "atr_14": round(float(row["atr_14"]), 2),
            "rsi": round(float(row["rsi_14"]), 1), "vol_spike": round(float(row["vol_spike"]), 2),
            "adx": round(float(row["adx_14"]), 1), "score": score,
            "entry_premium": p0, "exit_premium": exit_premium, "exit_reason": exit_reason,
            "net_pnl_std": round(f_std["net_pnl"], 2),
        })

    df_micro = pd.DataFrame(trade_log_micro)
    df_std = pd.DataFrame(trade_log_std)
    df_sig = pd.DataFrame(signal_log)

    res_micro = analyze(df_micro, INITIAL_WALLET_CAPITAL, wallet_micro)
    res_std = analyze(df_std, 25000.0, wallet_std)

    total_signal_days = len(signal_log)
    print("\n" + "=" * 95)
    print("  ENGINE B (MCX CRUDE OIL) REAL-DATA 1-YEAR BACKTEST - SIDE-BY-SIDE      ")
    print("=" * 95)
    print(f"  Backtest Window        : {df['Date'].iloc[0].date()} - {df['Date'].iloc[-1].date()} "
          f"({n} Trading Days, Real MCX Spot INR)")
    print(f"  Filter Rejections      : {rejected_eia} EIA(Wed) | {rejected_regime} ADX<{MIN_ADX_TREND_STRENGTH:.0f} | "
          f"{rejected_alignment} No-Alignment | {rejected_score} Score<{QUALIFICATION_SCORE_THRESHOLD:.0f} | "
          f"{rejected_no_next_day} No-Next-Day")
    print(f"  Micro Rejections       : {rejected_micro_premium} Premium<Rs{MIN_MICRO_PREMIUM_INR:.0f} | "
          f"{rejected_micro_budget} Budget>Rs{MICRO_CAPITAL_BUDGET_CAP:.0f} (Real ATM premiums Rs{p0:.0f}x lot{MCX_CRUDE_MINI_LOT_SIZE})")
    print("-" * 95)
    print(f"  METRIC                 | MODE 1: MICRO (Rs {MICRO_CAPITAL_BUDGET_CAP:.0f} CAP) | MODE 2: STANDARD (100 BBL)")
    print("-" * 95)
    print(f"  Initial Wallet         | Rs {INITIAL_WALLET_CAPITAL:,.2f}             | Rs 25,000.00")
    print(f"  Final Wallet Balance   | Rs {wallet_micro:,.2f}             | Rs {wallet_std:,.2f}")
    print(f"  Net Realized PnL       | Rs {res_micro.get('net_pnl', 0):,.2f} ({res_micro.get('ret_pct', 0):+.2f}%)  | Rs {res_std.get('net_pnl', 0):,.2f} ({res_std.get('ret_pct', 0):+.2f}%)")
    print(f"  Total Trades           | {res_micro.get('total_trades', 0)}                          | {res_std.get('total_trades', 0)}")
    print(f"  Win Rate               | {res_micro.get('win_rate', 0):.2f}% ({res_micro.get('winning', 0)}W/{res_micro.get('losing', 0)}L)      | {res_std.get('win_rate', 0):.2f}% ({res_std.get('winning', 0)}W/{res_std.get('losing', 0)}L)")
    print(f"  Gross Profit           | Rs {res_micro.get('gross_profit', 0):,.2f}          | Rs {res_std.get('gross_profit', 0):,.2f}")
    print(f"  Gross Loss             | Rs {res_micro.get('gross_loss', 0):,.2f}          | Rs {res_std.get('gross_loss', 0):,.2f}")
    print(f"  Total Friction Fees    | Rs {res_micro.get('total_friction', 0):,.2f}          | Rs {res_std.get('total_friction', 0):,.2f}")
    print(f"  PROFIT FACTOR          | {res_micro.get('profit_factor', 0):.2f}                          | {res_std.get('profit_factor', 0):.2f}")
    print(f"  MAX DRAWDOWN           | {res_micro.get('max_dd_pct', 0):.2f}%                         | {res_std.get('max_dd_pct', 0):.2f}%")
    print(f"  Signals Generated      | {total_signal_days} (of {n} days)")
    print("=" * 95)

    if len(df_std) > 0:
        print("\n  TOP EXIT REASONS (Standard Mode):")
        for reason_str, cnt in df_std["exit_reason"].value_counts().items():
            win_cnt = int(df_std[df_std["exit_reason"] == reason_str]["net_pnl"].gt(0).sum())
            print(f"    {reason_str:<24} {cnt:>3} trades | {win_cnt} winners")
        print("\n  DIRECTION SPLIT (Standard Mode):")
        for d, g in df_std.groupby("direction"):
            print(f"    {d:<10} {len(g):>3} trades | WinRate {100*g['net_pnl'].gt(0).mean():.1f}% | NetPnl Rs {g['net_pnl'].sum():,.2f}")

    os.makedirs(os.path.join(BASE_DIR, "reports"), exist_ok=True)
    micro_path = os.path.join(BASE_DIR, "reports", f"{REPORT_PREFIX}_micro_1y_backtest.csv")
    std_path = os.path.join(BASE_DIR, "reports", f"{REPORT_PREFIX}_std_1y_backtest.csv")
    sig_path = os.path.join(BASE_DIR, "reports", f"{REPORT_PREFIX}_signals_1y.csv")
    df_micro.to_csv(micro_path, index=False)
    df_std.to_csv(std_path, index=False)
    df_sig.to_csv(sig_path, index=False)
    print(f"\n[Report Export] Micro-Capital log   -> '{micro_path}'")
    print(f"[Report Export] Standard log        -> '{std_path}'")
    print(f"[Report Export] Signals/indicator   -> '{sig_path}'")

    return {"micro": res_micro, "std": res_std, "signals": total_signal_days,
            "micro_path": micro_path, "std_path": std_path, "sig_path": sig_path}


if __name__ == "__main__":
    run_engine_b_real_data_backtest()
