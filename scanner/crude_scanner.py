import os
import sys
import math
import time
import datetime
import requests
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, Tuple, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    MCX_CRUDE_SYMBOL,
    MCX_CRUDE_INSTRUMENT_KEY,
    MCX_CRUDE_LOT_SIZE,
    MCX_CRUDE_STRIKE_STEP,
    MCX_CRUDE_MIN_ATR,
    DHAN_API_BASE_URL
)

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculates intraday Volume Weighted Average Price (VWAP).
    VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
    Typical Price = (High + Low + Close) / 3
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_volume = typical_price * df["volume"]
    
    if "date" in df.columns:
        cum_tp_vol = tp_volume.groupby(df["date"]).cumsum()
        cum_vol = df["volume"].groupby(df["date"]).cumsum()
    else:
        cum_tp_vol = tp_volume.cumsum()
        cum_vol = df["volume"].cumsum()
        
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap.ffill().fillna(df["close"])

def calculate_ema(series: pd.Series, period: int = 20) -> pd.Series:
    """
    Calculates Exponential Moving Average (EMA) over the specified period.
    """
    return series.ewm(span=period, adjust=False).mean()

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculates Average True Range (ATR) over the specified period.
    True Range = Max(High - Low, |High - Prior Close|, |Low - Prior Close|)
    """
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr

def calculate_supertrend(df: pd.DataFrame, period: int = 7, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """
    Calculates Supertrend(period, multiplier) indicator.
    Returns:
        supertrend_val: pd.Series containing Supertrend price values.
        supertrend_dir: pd.Series containing 1 for GREEN (Bullish) and -1 for RED (Bearish).
    """
    atr = calculate_atr(df, period=period)
    hl2 = (df["high"] + df["low"]) / 2.0
    
    basic_ub = hl2 + (multiplier * atr)
    basic_lb = hl2 - (multiplier * atr)
    
    n = len(df)
    final_ub = np.zeros(n)
    final_lb = np.zeros(n)
    st_val = np.zeros(n)
    st_dir = np.zeros(n, dtype=int)
    
    close = df["close"].values
    b_ub = basic_ub.values
    b_lb = basic_lb.values
    
    for i in range(1, n):
        # Final Upper Band
        if b_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]:
            final_ub[i] = b_ub[i]
        else:
            final_ub[i] = final_ub[i - 1]
            
        # Final Lower Band
        if b_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]:
            final_lb[i] = b_lb[i]
        else:
            final_lb[i] = final_lb[i - 1]
            
        # Supertrend Direction
        if st_dir[i - 1] == 1:
            if close[i] < final_lb[i]:
                st_dir[i] = -1
                st_val[i] = final_ub[i]
            else:
                st_dir[i] = 1
                st_val[i] = final_lb[i]
        else:
            if close[i] > final_ub[i]:
                st_dir[i] = 1
                st_val[i] = final_lb[i]
            else:
                st_dir[i] = -1
                st_val[i] = final_ub[i]

    if n > 0:
        st_dir[0] = 1 if close[0] >= hl2.iloc[0] else -1
        st_val[0] = final_lb[0] if st_dir[0] == 1 else final_ub[0]
        
    return pd.Series(st_val, index=df.index), pd.Series(st_dir, index=df.index)

def fetch_mcx_crude_candles(access_token: str, days: int = 5) -> pd.DataFrame:
    """
    Fetches real-time 5-minute candle data for MCX Crude Oil via Dhan API v2 candle endpoint.
    Falls back to simulated synthetic candle data if market is closed or API response is unavailable.
    """
    instrument_key = MCX_CRUDE_INSTRUMENT_KEY
    today_str = datetime.date.today().isoformat()
    from_date_str = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    
    url = f"{DHAN_API_BASE_URL}/historical-candle/{instrument_key}/5minute/{today_str}/{from_date_str}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    if access_token and not access_token.startswith("MOCK"):
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                candles = data.get("data", {}).get("candles", [])
                if candles:
                    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df.sort_values("timestamp").reset_index(drop=True)
                    df["date"] = df["timestamp"].dt.date
                    return df
        except Exception as e:
            print(f"[Crude Scanner Notice] Dhan Live Candle Fetch Exception: {e}")

    # SIMULATED / SYNTHETIC CANDLE FALLBACK FOR OFFLINE / MOCK TESTING
    print("[Crude Scanner] Generating realistic synthetic 5-min candles for MCX Crude Oil...")
    np.random.seed(42)
    n_candles = 100
    base_price = 6250.0
    
    returns = np.random.normal(0.0003, 0.003, n_candles)
    price_path = base_price * np.exp(np.cumsum(returns))
    
    candles_list = []
    now = datetime.datetime.now() - datetime.timedelta(minutes=n_candles * 5)
    
    for i in range(n_candles):
        c_time = now + datetime.timedelta(minutes=i * 5)
        c_open = price_path[i - 1] if i > 0 else base_price
        c_close = price_path[i]
        spread = abs(np.random.normal(5.0, 2.0))
        c_high = max(c_open, c_close) + spread
        c_low = min(c_open, c_close) - spread
        c_vol = int(np.random.uniform(500, 2500))
        
        candles_list.append({
            "timestamp": c_time,
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "volume": c_vol,
            "date": c_time.date()
        })
        
    return pd.DataFrame(candles_list)

def scan_mcx_crude_oil(access_token: str, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """
    Scans MCX Crude Oil 5-minute candles using the Quantitative Trend Strategy:
    1. Direction: BULLISH (CE) when Price > VWAP and Price > 20-EMA and Supertrend(7, 3) is GREEN.
    2. Direction: BEARISH (PE) when Price < VWAP and Price < 20-EMA and Supertrend(7, 3) is RED.
    3. Volatility Gate: Require ATR(14) > 15.0 points to ensure intraday move potential.
    """
    print("\n==========================================================================")
    print("      MCX CRUDE OIL 5-MIN CANDLE TREND SCANNER (SESSION 2)")
    print("==========================================================================")

    df = fetch_mcx_crude_candles(access_token=access_token)
    if df is None or df.empty or len(df) < 30:
        print("[Crude Scanner Error] Insufficient candle data for MCX Crude Oil scanning.")
        return None

    # Compute Technical Indicators
    df["vwap"] = calculate_vwap(df)
    df["ema_20"] = calculate_ema(df["close"], period=20)
    df["atr_14"] = calculate_atr(df, period=14)
    st_val, st_dir = calculate_supertrend(df, period=7, multiplier=3.0)
    df["supertrend_val"] = st_val
    df["supertrend_dir"] = st_dir

    latest = df.iloc[-1]
    spot_price = float(latest["close"])
    vwap_val = float(latest["vwap"])
    ema_20_val = float(latest["ema_20"])
    atr_14_val = float(latest["atr_14"])
    st_val_latest = float(latest["supertrend_val"])
    st_dir_latest = int(latest["supertrend_dir"])

    print(f"  Underlying Asset     : {MCX_CRUDE_SYMBOL} (MCX Futures)")
    print(f"  Current Spot Price   : Rs {spot_price:,.2f} INR")
    print(f"  VWAP Line            : Rs {vwap_val:,.2f} INR (Price vs VWAP: {'ABOVE' if spot_price > vwap_val else 'BELOW'})")
    print(f"  20-EMA Line          : Rs {ema_20_val:,.2f} INR (Price vs EMA: {'ABOVE' if spot_price > ema_20_val else 'BELOW'})")
    print(f"  Supertrend(7,3)      : Rs {st_val_latest:,.2f} INR ({'GREEN / BULLISH' if st_dir_latest == 1 else 'RED / BEARISH'})")
    print(f"  ATR(14) Volatility   : {atr_14_val:.2f} points (Required Gate: > {MCX_CRUDE_MIN_ATR:.1f} pts)")

    # Volatility Gate Verification (ATR(14) > 15.0 pts)
    if atr_14_val < MCX_CRUDE_MIN_ATR:
        print(f"\n[VOLATILITY GATE BLOCKED] ATR(14) ({atr_14_val:.2f} pts) < Required Min Gate ({MCX_CRUDE_MIN_ATR:.1f} pts). Market too flat. Execution aborted.")
        return None

    # Trend Alignment Strategy Signal Checks
    is_bullish = (spot_price > vwap_val) and (spot_price > ema_20_val) and (st_dir_latest == 1)
    is_bearish = (spot_price < vwap_val) and (spot_price < ema_20_val) and (st_dir_latest == -1)

    if not is_bullish and not is_bearish:
        print("\n[NO TREND SIGNAL] Mixed trend alignment. Price/VWAP/EMA/Supertrend not strictly aligned. Skipping trade.")
        return None

    direction = "BULLISH" if is_bullish else "BEARISH"
    option_type = "CE" if is_bullish else "PE"
    
    composite_score = round(75.0 + (min(atr_14_val / MCX_CRUDE_MIN_ATR, 2.0) * 10.0), 2)

    candidate = {
        "symbol": MCX_CRUDE_SYMBOL,
        "exchange": "MCX_FO",
        "instrument_key": MCX_CRUDE_INSTRUMENT_KEY,
        "spot_price": spot_price,
        "direction": direction,
        "option_type": option_type,
        "vwap": vwap_val,
        "ema_20": ema_20_val,
        "supertrend_val": st_val_latest,
        "supertrend_dir": st_dir_latest,
        "atr_14": atr_14_val,
        "strike_interval": MCX_CRUDE_STRIKE_STEP,
        "lot_size": MCX_CRUDE_LOT_SIZE,
        "is_index": False,
        "is_mcx": True,
        "composite_rating": {
            "composite_score": composite_score,
            "tech_score": 50.0,
            "news_score": 35.0
        }
    }

    print("\n[MCX CRUDE OIL SIGNAL QUALIFIED]")
    print(f"  Direction            : {direction} ({option_type} Option)")
    print(f"  Composite Score      : {composite_score} / 100 Pts")
    print("==========================================================================")

    return candidate

if __name__ == "__main__":
    cand = scan_mcx_crude_oil(access_token="MOCK_TEST_TOKEN")
    print("Scan Result:", cand)
