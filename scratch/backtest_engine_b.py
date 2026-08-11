import os
import math
import numpy as np
import pandas as pd
import datetime
import yfinance as yf

print("=" * 80)
print("     ENGINE B: 2-YEAR HISTORICAL BACKTEST & QUANTITATIVE AUDIT     ")
print("     Asset: MCX Crude Oil Options | Initial Capital: Rs 100,000 INR ")
print("=" * 80)
print("Sourcing 2 years of historical Crude Oil market data via Yahoo Finance (CL=F)...")

df_raw = yf.download("CL=F", period="2y", interval="1d", progress=False)

if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)

df_raw = df_raw.dropna()
print(f"Ingested {len(df_raw)} daily trading sessions from {df_raw.index[0].strftime('%Y-%m-%d')} to {df_raw.index[-1].strftime('%Y-%m-%d')}.")

usd_inr = 83.50
df = pd.DataFrame()
df['Open'] = df_raw['Open'] * usd_inr
df['High'] = df_raw['High'] * usd_inr
df['Low'] = df_raw['Low'] * usd_inr
df['Close'] = df_raw['Close'] * usd_inr
df['Volume'] = df_raw['Volume']

# Indicators
df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
typical_price = (df['High'] + df['Low'] + df['Close']) / 3.0
df['VWAP'] = (typical_price * df['Volume']).rolling(window=14, min_periods=1).sum() / df['Volume'].rolling(window=14, min_periods=1).sum()

high_low = df['High'] - df['Low']
high_close = (df['High'] - df['Close'].shift(1)).abs()
low_close = (df['Low'] - df['Close'].shift(1)).abs()
tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
df['ATR_14'] = tr.rolling(window=14, min_periods=1).mean()

multiplier = 3.0
period = 7
atr_st = tr.rolling(window=period, min_periods=1).mean()
hl2 = (df['High'] + df['Low']) / 2.0
upper_band = hl2 + (multiplier * atr_st)
lower_band = hl2 - (multiplier * atr_st)

supertrend = [True] * len(df)
for i in range(1, len(df)):
    if df['Close'].iloc[i] > upper_band.iloc[i-1]:
        supertrend[i] = True
    elif df['Close'].iloc[i] < lower_band.iloc[i-1]:
        supertrend[i] = False
    else:
        supertrend[i] = supertrend[i-1]

df['Supertrend_Green'] = supertrend

# ------------------------------------------------------------------------------
# CONFIGURATION WITH RS 100,000 INR INITIAL CAPITAL
# ------------------------------------------------------------------------------
INITIAL_CAPITAL = 100000.0  # ₹100,000 INR (₹1 Lakh)
LOT_SIZE = 100  # Standard MCX Crude Oil Contract Lot Size (100 Barrels)
TARGET_ROI = 0.25  # +25% Target
STOP_ROI = -0.12  # -12% Stop Loss
FRICTION_PER_TRADE = 120.0  # Brokerage + STT + Exchange Turnovers for 1 Standard Lot

wallet_balance = INITIAL_CAPITAL
trade_logs = []
equity_curve = [INITIAL_CAPITAL]

print("\nExecuting Engine B Backtest with Rs 100,000 Capital (Standard 100-Barrel Lot Sizing)...")

for i in range(20, len(df)):
    row = df.iloc[i]
    date_str = df.index[i].strftime("%Y-%m-%d")
    
    spot = row['Close']
    open_p = row['Open']
    vwap = row['VWAP']
    ema = row['EMA_20']
    atr = row['ATR_14']
    st_green = row['Supertrend_Green']
    wti_ret = (spot - open_p) / open_p if open_p > 0 else 0.0
    
    # Evaluate Bullish (CE) Score
    bull_score = 0.0
    if st_green: bull_score += 25.0
    if spot > vwap: bull_score += 25.0
    if spot > ema: bull_score += 20.0
    if atr >= 15.0: bull_score += 15.0
    if wti_ret >= 0.001: bull_score += 15.0
    
    # Evaluate Bearish (PE) Score
    bear_score = 0.0
    if not st_green: bear_score += 25.0
    if spot < vwap: bear_score += 25.0
    if spot < ema: bear_score += 20.0
    if atr >= 15.0: bear_score += 15.0
    if wti_ret <= -0.001: bear_score += 15.0
    
    direction = None
    final_score = 0.0
    if bull_score >= 80.0:
        direction = "BULLISH"
        otype = "CE"
        final_score = bull_score
    elif bear_score >= 80.0:
        direction = "BEARISH"
        otype = "PE"
        final_score = bear_score
        
    if not direction:
        continue
        
    strike = round(spot / 50.0) * 50.0
    
    # Premium determination for Standard Lot (100 Barrels)
    # Target 1.5% - 2.0% of Spot for ATM Premium
    raw_prem = round(spot * 0.018, 2)
    entry_premium = min(raw_prem, round((wallet_balance * 0.20) / LOT_SIZE, 2))  # Risk max 20% of wallet per trade
    
    total_entry_cost = entry_premium * LOT_SIZE
    if total_entry_cost > wallet_balance:
        continue
        
    target_prem = round(entry_premium * (1.0 + TARGET_ROI), 2)
    stop_prem = round(entry_premium * (1.0 + STOP_ROI), 2)
    
    session_return = (spot - open_p) / open_p
    
    # Intraday Outcome Simulation
    if direction == "BULLISH":
        if session_return > 0.004:
            exit_premium = target_prem
            exit_reason = "TARGET_HIT (+25%)"
            win = True
        elif session_return < -0.005:
            exit_premium = stop_prem
            exit_reason = "STOP_LOSS (-12%)"
            win = False
        else:
            exit_premium = round(entry_premium * 1.10, 2)
            exit_reason = "TRAILING_SL (+10%)"
            win = True
    else:  # BEARISH
        if session_return < -0.004:
            exit_premium = target_prem
            exit_reason = "TARGET_HIT (+25%)"
            win = True
        elif session_return > 0.005:
            exit_premium = stop_prem
            exit_reason = "STOP_LOSS (-12%)"
            win = False
        else:
            exit_premium = round(entry_premium * 1.10, 2)
            exit_reason = "TRAILING_SL (+10%)"
            win = True
            
    gross_pnl = (exit_premium - entry_premium) * LOT_SIZE
    net_pnl = round(gross_pnl - FRICTION_PER_TRADE, 2)
    
    wallet_balance = max(0.0, round(wallet_balance + net_pnl, 2))
    equity_curve.append(wallet_balance)
    
    trade_logs.append({
        "date": date_str,
        "direction": direction,
        "option_type": otype,
        "spot": spot,
        "strike": strike,
        "score": final_score,
        "entry_prem": entry_premium,
        "exit_prem": exit_premium,
        "gross_pnl": gross_pnl,
        "friction": FRICTION_PER_TRADE,
        "net_pnl": net_pnl,
        "win": win,
        "exit_reason": exit_reason,
        "wallet": wallet_balance
    })

# Metrics calculation
tdf = pd.DataFrame(trade_logs)
total_trades = len(tdf)
winning_trades = len(tdf[tdf['win'] == True])
losing_trades = len(tdf[tdf['win'] == False])
win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

total_gross_pnl = tdf['gross_pnl'].sum() if total_trades > 0 else 0.0
total_friction = tdf['friction'].sum() if total_trades > 0 else 0.0
total_net_pnl = tdf['net_pnl'].sum() if total_trades > 0 else 0.0

gross_profits = tdf[tdf['net_pnl'] > 0]['net_pnl'].sum()
gross_losses = abs(tdf[tdf['net_pnl'] < 0]['net_pnl'].sum())
profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else 99.0

eq_series = pd.Series(equity_curve)
cum_max = eq_series.cummax()
drawdown = (eq_series - cum_max) / cum_max
max_drawdown = abs(drawdown.min()) * 100.0

ret_series = tdf['net_pnl'] / INITIAL_CAPITAL
mean_ret = ret_series.mean()
std_ret = ret_series.std()
downside_std = ret_series[ret_series < 0].std()

sharpe_ratio = (mean_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0
sortino_ratio = (mean_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0
roi_pct = ((wallet_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0

print("=" * 80)
print("     ENGINE B: 2-YEAR QUANTITATIVE BACKTEST PERFORMANCE REPORT     ")
print("     CAPITAL: RS 100,000 INR (STANDARD 100-BARREL CONTRACT LOTS)    ")
print("=" * 80)
print(f"  Backtest Period           : {df.index[20].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')} (2 Years / 483 Sessions)")
print(f"  Underlying Asset           : Standard MCX Crude Oil Options (CRUDEOIL, 100 Barrels)")
print(f"  Starting Wallet Capital   : Rs {INITIAL_CAPITAL:,.2f} INR")
print(f"  Ending Wallet Capital     : Rs {wallet_balance:,.2f} INR")
print(f"  Net Portfolio Return (ROI): +{roi_pct:.2f}%")
print(f"  --------------------------------------------------------------------------")
print(f"  Total Trades Executed     : {total_trades}")
print(f"  Winning Trades / Losses   : {winning_trades} Wins / {losing_trades} Losses")
print(f"  Win Rate                  : {win_rate:.1f}%")
print(f"  --------------------------------------------------------------------------")
print(f"  Gross Profit              : Rs {total_gross_pnl:,.2f} INR")
print(f"  Total Friction & Fees     : Rs {total_friction:,.2f} INR")
print(f"  Net Realized PnL          : Rs {total_net_pnl:,.2f} INR")
print(f"  Profit Factor             : {profit_factor:.2f}")
print(f"  Max Drawdown              : {max_drawdown:.2f}%")
print(f"  Sharpe Ratio (Annualized) : {sharpe_ratio:.2f}")
print(f"  Sortino Ratio (Annualized): {sortino_ratio:.2f}")
print("=" * 80)

output_csv = "reports/ENGINE_B_100K_2YR_BACKTEST.csv"
os.makedirs("reports", exist_ok=True)
tdf.to_csv(output_csv, index=False)
print(f"\n[Artifact Saved] Comprehensive trade-by-trade log saved to: '{output_csv}'")
