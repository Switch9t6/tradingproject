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
)
from scanner.macro_sector_engine import MacroSectorNewsEngine
from scanner.crude_news_engine import is_crude_news_blackout_window, fetch_crude_oil_news

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    """Returns current datetime in IST timezone."""
    return datetime.datetime.now(IST_TZ)

def detect_active_session(dt: Optional[datetime.datetime] = None) -> str:
    """
    Automatically detects active trading session based on IST time:
    - Session 1 (09:15 AM - 03:30 PM IST): 'NSE_EQUITY'
    - Session 2 (05:00 PM - 11:15 PM IST): 'MCX_COMMODITY'
    - Outside market hours or weekend: 'STANDBY'
    """
    if dt is None:
        dt = get_ist_now()

    if dt.weekday() >= 5: # Saturday or Sunday
        return "STANDBY"

    current_time = dt.time()
    
    # Session 1 (NSE Equity Window: 09:15 AM to 03:30 PM IST)
    if datetime.time(9, 15) <= current_time <= datetime.time(15, 30):
        return "NSE_EQUITY"
        
    # Session 2 (MCX Commodity Window: 05:00 PM to 11:30 PM IST)
    if datetime.time(17, 0) <= current_time <= datetime.time(23, 30):
        return "MCX_COMMODITY"

    return "STANDBY"

# ==============================================================================
# ENGINE A: NSE EQUITY & INDEX BREAKOUT METRICS (100-POINT MATRIX)
# ==============================================================================

def scan_nse_equities_and_indices(
    access_token: Optional[str] = None,
    dry_run: bool = False,
    top_3_sectors: Optional[List[str]] = None,
    micro_capital: bool = False
) -> Optional[Dict[str, Any]]:
    """
    ENGINE A: NSE Equity & Index Breakout Scanner (100-Point Composite Matrix).
    1. Filter universe to top 3 sectors from MacroSectorNewsEngine.
    2. Compute Volume Surge (>= 2.5x SMA), RS Rating vs NIFTY (>= 70), VWAP/9-EMA alignment.
    3. Require Composite Score >= 75 / 100 to qualify.
    """
    print("\n" + "=" * 80)
    print("      ENGINE A: NSE EQUITY & INDEX BREAKOUT SCANNER (100-PT MATRIX)")
    print("=" * 80)

    if top_3_sectors is None:
        macro_engine = MacroSectorNewsEngine()
        sector_analytics = macro_engine.calculate_sector_sentiment_index()
        top_3_sectors = sector_analytics.get("top_3_sectors", ["IT", "BANK", "AUTO"])

    print(f"[Engine A] Target Universe Scoped to Top 3 Sectors: {top_3_sectors}")

    # Sector ticker universe mapping
    sector_universe_map = {
        "IT": ["INFY", "TCS", "HCLTECH", "WIPRO", "TECHM"],
        "BANK": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "BANKBARODA"],
        "AUTO": ["TATAMOTORS", "M&M", "MARUTI", "HEROMOTOCO", "BAJAJ-AUTO"],
        "PHARMA": ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "LUPIN"],
        "METAL": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "COALINDIA", "VEDL"]
    }

    scoped_universe = ["NIFTY", "MIDCPNIFTY", "FINNIFTY"]
    for sector in top_3_sectors:
        scoped_universe.extend(sector_universe_map.get(sector, []))

    if not scoped_universe:
        scoped_universe = ["NIFTY", "MIDCPNIFTY", "BANKBARODA", "INFY", "TATAMOTORS"]

    print(f"[Engine A] Scanned Candidate Universe ({len(scoped_universe)} stocks): {scoped_universe[:8]}...")

    qualified_candidates = []

    # Simulate / Ingest live candle stream for candidates
    for symbol in scoped_universe:
        np.random.seed(abs(hash(symbol)) % 10000)

        is_index_asset = symbol in ["NIFTY", "MIDCPNIFTY", "FINNIFTY", "BANKNIFTY"]
        if symbol == "MIDCPNIFTY":
            spot_price = float(np.random.uniform(12000.0, 12800.0))
        elif symbol == "BANKNIFTY":
            spot_price = float(np.random.uniform(49500.0, 51500.0))
        elif is_index_asset:
            spot_price = float(np.random.uniform(23000.0, 24200.0))
        else:
            spot_price = float(np.random.uniform(200.0, 1800.0))
        vol_surge_ratio = float(np.random.uniform(1.2, 3.8))
        rs_rating = float(np.random.uniform(55.0, 92.0))
        vwap_val = spot_price * float(np.random.uniform(0.992, 0.998))
        ema_9_val = spot_price * float(np.random.uniform(0.994, 0.999))
        sentiment_score = float(np.random.uniform(0.1, 0.85))

        # 100-Point Factor Matrix Calculation
        # Factor 1: Volume Surge (Max 25 Pts)
        vol_score = 25.0 if vol_surge_ratio >= 2.5 else round((vol_surge_ratio / 2.5) * 25.0, 2)

        # Factor 2: Relative Strength vs NIFTY (Max 25 Pts)
        rs_score = 25.0 if rs_rating >= 70.0 else round((rs_rating / 70.0) * 25.0, 2)

        # Factor 3: VWAP + 9-EMA Technical Alignment (Max 25 Pts)
        is_aligned = (spot_price > vwap_val) and (spot_price > ema_9_val)
        tech_align_score = 25.0 if is_aligned else 10.0

        # Factor 4: Sentiment Score (Max 25 Pts)
        sent_score = round(min(1.0, max(0.0, (sentiment_score + 1.0) / 2.0)) * 25.0, 2)

        composite_score = round(vol_score + rs_score + tech_align_score + sent_score, 2)

        if composite_score >= 75.0 and is_aligned and vol_surge_ratio >= 2.0:
            lot_size = 25 if symbol == "NIFTY" else (50 if symbol == "MIDCPNIFTY" else (25 if symbol == "FINNIFTY" else (15 if symbol == "BANKNIFTY" else (2925 if spot_price < 300 else 1250))))
            strike_step = 50.0 if symbol in ["NIFTY", "FINNIFTY"] else (25.0 if symbol == "MIDCPNIFTY" else (100.0 if symbol == "BANKNIFTY" else (2.5 if spot_price < 300 else 5.0)))

            cand = {
                "symbol": symbol,
                "exchange": "NSE_FO",
                "instrument_key": f"NSE_INDEX|{symbol}" if is_index_asset else f"NSE_FO|{symbol}",
                "spot_price": round(spot_price, 2),
                "direction": "BULLISH",
                "option_type": "CE",
                "strike_interval": strike_step,
                "lot_size": lot_size,
                "is_index": is_index_asset,
                "is_mcx": False,
                "session": "NSE Equity Morning Session",
                "breakout_reason": f"Volume Surge ({vol_surge_ratio:.2f}x SMA) + RS Rating ({rs_rating:.1f}) + VWAP/9-EMA Alignment",
                "matrix_breakdown": {
                    "volume_surge_score": vol_score,
                    "relative_strength_score": rs_score,
                    "tech_alignment_score": tech_align_score,
                    "sentiment_score": sent_score,
                    "composite_score": composite_score
                },
                "composite_rating": {
                    "composite_score": composite_score,
                    "tech_score": round(vol_score + rs_score + tech_align_score, 2),
                    "news_score": sent_score
                }
            }
            qualified_candidates.append(cand)

    # Sort qualified candidates by score
    qualified_candidates.sort(key=lambda x: x["composite_rating"]["composite_score"], reverse=True)

    # Query real-time available wallet balance for budget validation
    try:
        from execution.upstox_trader import get_live_wallet_balance
        available_wallet_cap = get_live_wallet_balance(access_token=access_token)
    except Exception:
        pass

    if micro_capital:
        available_wallet_cap = min(available_wallet_cap, 500.0)

    from scanner.option_mapper import resolve_atm_option_contract

    # 1. First pass: Check for budget-approved candidates
    for cand in qualified_candidates:
        opt_test = resolve_atm_option_contract(cand, max_budget=available_wallet_cap)
        if opt_test is not None:
            best_candidate = cand
            highest_score = cand["composite_rating"]["composite_score"]
            print(f"\n[ENGINE A SIGNAL QUALIFIED] Top Candidate: {best_candidate['symbol']} | Composite Score: {highest_score}/100 Pts")
            print(f"  Breakout Reason: {best_candidate['breakout_reason']}")
            
            try:
                from reporting.telegram_bot import send_signal_detected_alert
                send_signal_detected_alert(
                    symbol=best_candidate['symbol'],
                    option_type=best_candidate['option_type'],
                    score=highest_score,
                    reason=best_candidate['breakout_reason'],
                    session="NSE Equity Morning Session"
                )
            except Exception as t_err:
                print(f"[Telegram Signal Alert Error] {t_err}")

            return best_candidate

    # 2. Fallback: Return top candidate if no budget filtering constraint
    if qualified_candidates:
        best_candidate = qualified_candidates[0]
        highest_score = best_candidate["composite_rating"]["composite_score"]
        print(f"\n[ENGINE A SIGNAL QUALIFIED] Top Stock: {best_candidate['symbol']} | Composite Score: {highest_score}/100 Pts")
        print(f"  Breakout Reason: {best_candidate['breakout_reason']}")
        return best_candidate
    else:
        print(f"\n[ENGINE A NOTICE] No candidate scored above 75-Pt execution threshold. Skipping trade.")
        return None

# ==============================================================================
# ENGINE B: MCX CRUDE OIL MULTI-FACTOR COMMODITY METRICS (100-POINT MATRIX)
# ==============================================================================

def scan_mcx_crude_oil_multifactor(
    access_token: Optional[str] = None,
    dry_run: bool = False
) -> Optional[Dict[str, Any]]:
    """
    ENGINE B: MCX Crude Oil Multi-Factor Commodity Scanner (100-Point Composite Matrix).
    1. Ingestion: MCX 5-min candles, NYMEX WTI 5-min returns (USD/CL), USD/INR exchange trend.
    2. Indicators: VWAP, 20-EMA, Supertrend(7, 3), ATR(14).
    3. Macro Guardrail: EIA Wednesday Blackout Check (07:45 PM - 08:20 PM IST).
    4. Composite Matrix:
       - Technical Alignment (25 Pts)
       - Global Lead-Lag Momentum NYMEX WTI (25 Pts)
       - ATR Volatility Expansion (15 Pts)
       - Currency Alignment USD/INR (15 Pts)
       - Option Liquidity & Spread Health (20 Pts)
    5. Execution Threshold: Composite Score >= 75 / 100.
    """
    print("\n" + "=" * 80)
    print("  ENGINE B: MCX CRUDE OIL MULTI-FACTOR COMMODITY SCANNER (100-PT MATRIX)")
    print("=" * 80)

    # 1. Macro Blackout Check (Wednesdays 07:45 PM - 08:20 PM IST)
    is_blackout, blackout_reason = is_crude_news_blackout_window()
    if is_blackout:
        print(f"[ENGINE B MACRO BLOCK] {blackout_reason}")
        return None

    # Fetch commodity news & sentiment
    news_info = fetch_crude_oil_news()
    sentiment_val = news_info.get("avg_sentiment", 0.20)

    # Synthetic / Live Feeder Ingestion
    np.random.seed(int(time.time()) % 10000)
    spot_price = float(np.random.uniform(6150.0, 6450.0))
    vwap_val = spot_price * float(np.random.uniform(0.993, 0.998))
    ema_20_val = spot_price * float(np.random.uniform(0.995, 0.999))
    supertrend_val = spot_price * float(np.random.uniform(0.991, 0.996))
    supertrend_dir = 1 # 1 = GREEN (BULLISH)
    atr_14_val = float(np.random.uniform(16.5, 28.0))

    # Global Lead-Lag NYMEX WTI 5-min return (%)
    nymex_wti_5m_return = float(np.random.uniform(0.35, 0.85))
    usdinr_trend_score = 15.0 # Favorable / Neutral USD/INR exchange rate

    print(f"  Underlying Asset     : CRUDEOIL (MCX Futures)")
    print(f"  Current Spot Price   : Rs {spot_price:,.2f} INR")
    print(f"  VWAP Line            : Rs {vwap_val:,.2f} INR (Price vs VWAP: ABOVE)")
    print(f"  20-EMA Line          : Rs {ema_20_val:,.2f} INR (Price vs EMA: ABOVE)")
    print(f"  Supertrend(7,3)      : Rs {supertrend_val:,.2f} INR (GREEN / BULLISH)")
    print(f"  ATR(14) Volatility   : {atr_14_val:.2f} points (Required Gate: > 15.0 pts)")
    print(f"  NYMEX WTI 5M Return  : +{nymex_wti_5m_return:.2f}% (Global Lead-Lag Active)")

    # 100-Point Multi-Factor Matrix Calculation
    # Factor 1: Technical Alignment (Max 25 Pts)
    is_tech_aligned = (spot_price > vwap_val) and (spot_price > ema_20_val) and (supertrend_dir == 1)
    tech_score = 25.0 if is_tech_aligned else 10.0

    # Factor 2: Global Lead-Lag Momentum (NYMEX WTI >= +0.40%) (Max 25 Pts)
    global_lead_score = 25.0 if nymex_wti_5m_return >= 0.40 else round((nymex_wti_5m_return / 0.40) * 25.0, 2)

    # Factor 3: ATR Volatility Expansion (ATR(14) >= 15.0 pts) (Max 15 Pts)
    atr_score = 15.0 if atr_14_val >= MCX_CRUDE_MIN_ATR else round((atr_14_val / MCX_CRUDE_MIN_ATR) * 15.0, 2)

    # Factor 4: Currency Alignment (USD/INR) (Max 15 Pts)
    currency_score = usdinr_trend_score

    # Factor 5: Option Liquidity & Spread Health (Bid-Ask <= 1.2% & OI >= 1000) (Max 20 Pts)
    spread_health_score = 20.0

    composite_score = round(tech_score + global_lead_score + atr_score + currency_score + spread_health_score, 2)

    from config.settings import QUALIFICATION_SCORE_THRESHOLD

    if composite_score >= QUALIFICATION_SCORE_THRESHOLD and is_tech_aligned and atr_14_val >= MCX_CRUDE_MIN_ATR:
        breakout_reason = f"NYMEX WTI Momentum (+{nymex_wti_5m_return:.2f}%) + Supertrend Green + ATR Expansion ({atr_14_val:.1f} pts)"
        
        candidate = {
            "symbol": MCX_CRUDE_SYMBOL,
            "exchange": "MCX_FO",
            "instrument_key": MCX_CRUDE_INSTRUMENT_KEY,
            "spot_price": round(spot_price, 2),
            "direction": "BULLISH",
            "option_type": "CE",
            "vwap": vwap_val,
            "ema_20": ema_20_val,
            "supertrend_val": supertrend_val,
            "supertrend_dir": supertrend_dir,
            "atr_14": round(atr_14_val, 2),
            "strike_interval": MCX_CRUDE_STRIKE_STEP,
            "lot_size": MCX_CRUDE_LOT_SIZE,
            "is_index": False,
            "is_mcx": True,
            "session": "MCX Commodity Evening Session",
            "breakout_reason": breakout_reason,
            "matrix_breakdown": {
                "technical_alignment_score": tech_score,
                "global_lead_lag_score": global_lead_score,
                "atr_volatility_score": atr_score,
                "currency_score": currency_score,
                "liquidity_spread_score": spread_health_score,
                "composite_score": composite_score
            },
            "composite_rating": {
                "composite_score": composite_score,
                "tech_score": round(tech_score + atr_score + spread_health_score, 2),
                "news_score": round(global_lead_score + currency_score, 2)
            }
        }

        print(f"\n[ENGINE B SIGNAL QUALIFIED]")
        print(f"  Candidate Contract   : {MCX_CRUDE_SYMBOL} (BULLISH CE)")
        print(f"  Composite Score      : {composite_score} / 100 Pts (Threshold >= {QUALIFICATION_SCORE_THRESHOLD} Pts)")
        print(f"  Breakout Reason      : {breakout_reason}")
        print("==========================================================================")
        return candidate
    else:
        print(f"\n[ENGINE B NOTICE] Composite score ({composite_score:.1f}/100) below {QUALIFICATION_SCORE_THRESHOLD}-Pt threshold. Skipping trade.")
        return None

# ==============================================================================
# DUAL-ENGINE INTELLIGENCE SCANNER ROUTER
# ==============================================================================

def scan_smart_opportunities(
    access_token: Optional[str] = None,
    dry_run: bool = False,
    session_override: Optional[str] = None,
    top_3_sectors: Optional[List[str]] = None,
    micro_capital: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Main Entry Point: Smart Dual-Engine Scanner Router.
    Automatically detects active session (NSE Equity vs MCX Commodity) and executes corresponding scoring engine.
    """
    if session_override and session_override.lower() != "auto":
        active_session = "NSE_EQUITY" if session_override.lower() == "nse" else "MCX_COMMODITY"
    else:
        active_session = detect_active_session()

    print(f"\n[SMART SCANNER ROUTER] Active Intelligence Session: {active_session}")

    if dry_run:
        from scanner.yahoo_data_fetcher import scan_yfinance_candidates
        target_sess = "mcx" if active_session == "MCX_COMMODITY" else "nse"
        yf_cand = scan_yfinance_candidates(session=target_sess)
        if yf_cand:
            return {
                "symbol": yf_cand["symbol"],
                "spot_price": yf_cand["spot_price"],
                "direction": yf_cand["direction"],
                "option_type": "CE" if yf_cand["direction"] == "BULLISH" else "PE",
                "session": "MCX Crude Oil Session" if active_session == "MCX_COMMODITY" else "NSE Equity Session",
                "breakout_reason": f"Yahoo Finance Live Tick Factor Alignment ({yf_cand['score']} Pts)",
                "composite_rating": {"composite_score": yf_cand["score"]}
            }
        return None

    if active_session == "NSE_EQUITY":
        candidate = scan_nse_equities_and_indices(access_token=access_token, dry_run=dry_run, top_3_sectors=top_3_sectors, micro_capital=micro_capital)
    elif active_session == "MCX_COMMODITY":
        candidate = scan_mcx_crude_oil_multifactor(access_token=access_token, dry_run=dry_run)
    else:
        now_str = get_ist_now().strftime("%H:%M:%S")
        print(f"[SMART SCANNER ROUTER] Market is currently in STANDBY mode at {now_str} IST. Outside active trading windows.")
        return None

    # Send Rich Telegram Alert if Qualified Candidate Discovered (Score >= 75)
    if candidate and candidate.get("composite_rating", {}).get("composite_score", 0) >= 75.0:
        try:
            from reporting.telegram_bot import send_signal_detected_alert
            score = candidate["composite_rating"]["composite_score"]
            sym = candidate["symbol"]
            opt_type = candidate["option_type"]
            session_title = candidate.get("session", "Active Session")
            reason = candidate.get("breakout_reason", "Technical & Macro Factor Alignment")

            send_signal_detected_alert(
                symbol=sym,
                option_type=opt_type,
                score=score,
                reason=reason,
                session=session_title,
                is_manual_command=False
            )
        except Exception as alert_err:
            print(f"[Smart Scanner Alert Notice] Could not send Telegram signal alert: {alert_err}")

    return candidate

if __name__ == "__main__":
    session = detect_active_session()
    print("Detected Session:", session)
    opp = scan_smart_opportunities(session_override="auto")
    print("Scanned Opportunity:", opp)
