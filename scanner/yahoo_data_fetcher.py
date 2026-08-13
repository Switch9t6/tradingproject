import os
import sys
import time
import datetime
import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "RELIANCE": "RELIANCE.NS",
    "INFY": "INFY.NS",
    "TCS": "TCS.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "SBIN": "SBIN.NS",
    "CRUDEOIL": "CL=F"
}


def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(IST_TZ)


def fetch_live_yfinance_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Fetches real-time intraday tick and technical indicators from Yahoo Finance for dry run execution.
    """
    yf_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.NS" if not symbol.endswith(".NS") and not symbol.startswith("^") else symbol)
    try:
        tk = yf.Ticker(yf_symbol)
        df = tk.history(period="5d", interval="5m")
        if df.empty or len(df) < 10:
            df = tk.history(period="1d", interval="1m")

        if df.empty:
            return None

        current_close = float(df['Close'].iloc[-1])
        current_open = float(df['Open'].iloc[-1])
        current_high = float(df['High'].iloc[-1])
        current_low = float(df['Low'].iloc[-1])
        current_vol = float(df['Volume'].iloc[-1])

        # Technical Indicators Calculation
        df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
        
        # ATR Calculation
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Close'].shift(1)),
                abs(df['Low'] - df['Close'].shift(1))
            )
        )
        df['ATR_14'] = df['TR'].rolling(14).mean()

        ema_9 = float(df['EMA_9'].iloc[-1])
        ema_21 = float(df['EMA_21'].iloc[-1])
        atr = float(df['ATR_14'].iloc[-1]) if not pd.isna(df['ATR_14'].iloc[-1]) else (current_close * 0.008)

        # Direction Bias
        direction = "BULLISH" if current_close >= ema_9 >= ema_21 else ("BEARISH" if current_close <= ema_9 <= ema_21 else "NEUTRAL")
        
        # Composite Score (100-pt scale)
        vol_avg = float(df['Volume'].tail(10).mean()) or 1.0
        vol_ratio = current_vol / vol_avg if vol_avg > 0 else 1.0
        
        score = 65.0
        if direction != "NEUTRAL":
            score += 15.0
        if vol_ratio > 1.2:
            score += 10.0
        if abs(current_close - ema_9) > (atr * 0.2):
            score += 10.0

        return {
            "symbol": symbol.upper(),
            "yf_symbol": yf_symbol,
            "spot_price": round(current_close, 2),
            "open": round(current_open, 2),
            "high": round(current_high, 2),
            "low": round(current_low, 2),
            "volume": current_vol,
            "atr": round(atr, 2),
            "ema_9": round(ema_9, 2),
            "ema_21": round(ema_21, 2),
            "direction": direction,
            "score": round(min(score, 98.0), 1),
            "timestamp": datetime.datetime.now().isoformat()
        }
    except Exception as ex:
        print(f"[Yahoo Data Fetcher Notice] Could not fetch data for {symbol}: {ex}")
        return None


def scan_yfinance_candidates(session: str = "nse") -> Optional[Dict[str, Any]]:
    """
    Scans real-time Yahoo Finance market data during active market hours for Dry Run execution.
    """
    now = get_ist_now()
    time_str = now.strftime("%H:%M IST")

    if session.lower() == "mcx":
        universe = ["CRUDEOIL"]
    else:
        universe = ["NIFTY", "BANKNIFTY", "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK", "SBIN"]

    print(f"[{time_str}] [Yahoo Finance Smart Scanner] Scanning {len(universe)} symbols for Dry Run...")

    best_candidate = None
    highest_score = 0.0

    for sym in universe:
        quote = fetch_live_yfinance_quote(sym)
        if quote and quote["score"] > highest_score and quote["direction"] != "NEUTRAL":
            highest_score = quote["score"]
            best_candidate = quote

    if best_candidate and best_candidate["score"] >= 75.0:
        print(f"  [Qualifying Candidate Found] {best_candidate['symbol']} ({best_candidate['direction']}) | Spot: Rs {best_candidate['spot_price']} | Score: {best_candidate['score']}/100 Pts")
        return best_candidate
    else:
        print("  [Scan Complete] No candidate met composite score threshold (>= 75 Pts).")
        return None
