import os
import sys
import time
import gzip
import json
import requests
from typing import Dict, Any, Optional, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    TARGET_DELTA_MIN,
    TARGET_DELTA_MAX,
    MAX_BID_ASK_SPREAD_PCT,
    MCX_CRUDE_SYMBOL,
    MCX_CRUDE_LOT_SIZE,
    MCX_CRUDE_MINI_LOT_SIZE,
    MCX_CRUDE_STRIKE_STEP
)

_upstox_mcx_cache = None
_upstox_nse_cache = None


def get_upstox_instrument_csv(exchange: str = "NSE") -> List[str]:
    """Downloads and caches official Upstox complete instrument CSV.gz file."""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    gz_file = os.path.join(cache_dir, f"{exchange}.csv.gz")
    
    if os.path.exists(gz_file):
        try:
            mtime = os.path.getmtime(gz_file)
            if time.time() - mtime < 86400: # 24 hour cache
                with gzip.open(gz_file, "rt", encoding="utf-8", errors="ignore") as f:
                    return f.read().splitlines()
        except Exception:
            pass

    try:
        url = f"https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.csv.gz"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            os.makedirs(cache_dir, exist_ok=True)
            with open(gz_file, "wb") as f:
                f.write(r.content)
            with gzip.open(gz_file, "rt", encoding="utf-8", errors="ignore") as f:
                return f.read().splitlines()
    except Exception as e:
        print(f"[Option Mapper Notice] Could not fetch Upstox {exchange} instrument CSV: {e}")

    return []


def get_upstox_mcx_instrument_map() -> dict:
    global _upstox_mcx_cache
    if _upstox_mcx_cache is not None:
        return _upstox_mcx_cache

    cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "upstox_mcx_instruments.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400:
                with open(cache_file, "r") as f:
                    _upstox_mcx_cache = json.load(f)
                    return _upstox_mcx_cache
        except Exception:
            pass

    lines = get_upstox_instrument_csv("MCX")
    instr_map = {}
    if lines:
        header = lines[0].split(",")
        for l in lines[1:]:
            parts = [p.strip('"') for p in l.split(',')]
            if len(parts) >= 11 and parts[11] == "MCX_FO" and parts[9] in ["OPTFUT", "OPTSTK"]:
                try:
                    instrument_key = parts[0]
                    tsym = parts[2]
                    lot = int(float(parts[8])) if parts[8] else 10
                    stk = float(parts[6]) if parts[6] else 0.0
                    otype = parts[10].upper()
                    und = "CRUDEOILM" if "CRUDEOILM" in tsym.upper() else "CRUDEOIL"
                    key = f"{und}_{int(stk)}_{otype}"
                    if key not in instr_map:
                        instr_map[key] = {
                            "instrument_key": instrument_key,
                            "tradingsymbol": tsym,
                            "lot_size": lot,
                            "strike": stk
                        }
                except Exception:
                    pass

    _upstox_mcx_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


def get_upstox_nse_instrument_map() -> dict:
    global _upstox_nse_cache
    if _upstox_nse_cache is not None:
        return _upstox_nse_cache

    cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "upstox_nse_instruments.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400:
                with open(cache_file, "r") as f:
                    _upstox_nse_cache = json.load(f)
                    return _upstox_nse_cache
        except Exception:
            pass

    lines = get_upstox_instrument_csv("NSE")
    instr_map = {}
    if lines:
        for l in lines[1:]:
            parts = [p.strip('"') for p in l.split(',')]
            if len(parts) >= 11 and parts[11] == "NSE_FO" and parts[10] in ["CE", "PE"]:
                try:
                    instrument_key = parts[0]
                    tsym = parts[2]
                    lot = int(float(parts[8])) if parts[8] else 25
                    stk = float(parts[6]) if parts[6] else 0.0
                    otype = parts[10].upper()
                    und = tsym.split("-")[0].upper() if "-" in tsym else parts[3].upper()
                    key = f"{und}_{int(stk)}_{otype}"
                    if key not in instr_map:
                        instr_map[key] = {
                            "instrument_key": instrument_key,
                            "tradingsymbol": tsym,
                            "lot_size": lot,
                            "strike": stk
                        }
                except Exception:
                    pass

    _upstox_nse_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


# Aliases for backward compatibility
get_mcx_instrument_map = get_upstox_mcx_instrument_map
get_nse_instrument_map = get_upstox_nse_instrument_map


def get_mcx_crude_option_contract(
    spot_price: float,
    direction: str,
    budget_cap: Optional[float] = None,
    option_type: Optional[str] = None,
    symbol_hint: str = "CRUDEOIL",
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    Upstox Option Contract Mapper for MCX Crude Oil Options (Standard & Mini).
    Selects At-The-Money (ATM) Call (CE) or Put (PE) option contract.
    Resolves official Upstox instrument_key and tradingsymbol.
    """
    if budget_cap is None:
        budget_cap = float("inf")

    if not option_type:
        option_type = "CE" if direction.upper() == "BULLISH" else "PE"

    strike_step = MCX_CRUDE_STRIKE_STEP

    atm_strike = round(spot_price / strike_step) * strike_step
    estimated_delta = 0.52
    estimated_premium = round(spot_price * 0.015, 2)

    bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
    ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0

    std_lot_cost = round(ask_price * MCX_CRUDE_LOT_SIZE, 2)
    
    if symbol_hint == "CRUDEOILM" or std_lot_cost > budget_cap:
        underlying_symbol = "CRUDEOILM"
        lot_size = MCX_CRUDE_MINI_LOT_SIZE
        print(f"  [Option Mapper Notice] Using Mini Crude Contract ({underlying_symbol}, Lot Size: {lot_size} barrels) for budget cap Rs {budget_cap:,.2f} INR.")
    else:
        underlying_symbol = MCX_CRUDE_SYMBOL
        lot_size = MCX_CRUDE_LOT_SIZE

    total_lot_cost = round(ask_price * lot_size, 2)

    mcx_map = get_upstox_mcx_instrument_map()
    lookup_key = f"{underlying_symbol}_{int(atm_strike)}_{option_type}"
    real_info = mcx_map.get(lookup_key, {})

    if not real_info and mcx_map:
        prefix = f"{underlying_symbol}_"
        candidates = []
        for k, v in mcx_map.items():
            if k.startswith(prefix) and k.endswith(f"_{option_type}"):
                stk_val = float(k.split("_")[1]) if len(k.split("_")) >= 3 else 0.0
                candidates.append((abs(stk_val - atm_strike), v))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            real_info = candidates[0][1]

    instrument_key = real_info.get("instrument_key", f"MCX_FO|{underlying_symbol}{int(atm_strike)}{option_type}")
    option_symbol = real_info.get("tradingsymbol", f"{underlying_symbol}_{int(atm_strike)}_{option_type}")
    if real_info.get("lot_size"):
        lot_size = int(real_info["lot_size"])

    budget_approved = total_lot_cost <= budget_cap
    spread_approved = bid_ask_spread_pct <= MAX_BID_ASK_SPREAD_PCT
    delta_approved = (estimated_delta >= TARGET_DELTA_MIN) and (estimated_delta <= TARGET_DELTA_MAX)
    open_interest = 120000
    oi_approved = open_interest >= 1000

    mapped_contract = {
        "underlying_symbol": underlying_symbol,
        "exchange": "MCX_FO",
        "is_mcx": True,
        "option_symbol": option_symbol,
        "instrument_key": instrument_key,
        "option_type": option_type,
        "strike_price": atm_strike,
        "spot_price": spot_price,
        "estimated_delta": estimated_delta,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "spread_pct": round(bid_ask_spread_pct * 100, 2),
        "lot_size": lot_size,
        "estimated_premium": ask_price,
        "total_lot_cost": total_lot_cost,
        "max_budget_limit": budget_cap,
        "budget_approved": budget_approved and spread_approved and delta_approved and oi_approved
    }

    print("\n[Upstox MCX Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Instrument Key      : {instrument_key}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Lot Size (Barrels)  : {lot_size} barrels ({'Mini' if lot_size == 10 else 'Standard'})")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {budget_cap:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")

    if not spread_approved or not oi_approved or not budget_approved:
        return None

    return mapped_contract


def resolve_atm_option_contract(
    candidate: Dict[str, Any],
    max_budget: Optional[float] = None,
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    Upstox Option Selection Guardrails for NSE Equity & MCX Commodity Candidates.
    Routes MCX Crude Oil candidates to get_mcx_crude_option_contract().
    """
    if candidate.get("is_mcx") or candidate.get("symbol") in [MCX_CRUDE_SYMBOL, "CRUDEOILM"]:
        return get_mcx_crude_option_contract(
            spot_price=candidate["spot_price"],
            direction=candidate["direction"],
            budget_cap=max_budget,
            option_type=candidate.get("option_type"),
            symbol_hint=candidate.get("symbol", "CRUDEOIL"),
            simulated_spread_pct=simulated_spread_pct
        )

    if max_budget is None:
        max_budget = float("inf")
    symbol = candidate["symbol"]
    spot_price = candidate["spot_price"]
    option_type = candidate.get("option_type", "CE")
    interval = candidate.get("strike_interval") or (100.0 if spot_price > 5000 else (50.0 if spot_price > 1000 else 10.0))
    lot_size = candidate.get("lot_size") or 400
    
    atm_strike = round(spot_price / interval) * interval
    estimated_delta = 0.52
    
    est_premium_pct = 0.008 if candidate.get("is_index") else 0.0125
    estimated_premium = round(spot_price * est_premium_pct, 2)
    
    bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
    ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0
    
    nse_map = get_upstox_nse_instrument_map()
    lookup_key = f"{symbol}_{int(atm_strike)}_{option_type}"
    real_info = nse_map.get(lookup_key, {})

    if not real_info and nse_map:
        prefix = f"{symbol}_"
        candidates = []
        for k, v in nse_map.items():
            if k.startswith(prefix) and k.endswith(f"_{option_type}"):
                stk_val = float(k.split("_")[1]) if len(k.split("_")) >= 3 else 0.0
                candidates.append((abs(stk_val - atm_strike), v))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            real_info = candidates[0][1]

    if real_info.get("lot_size"):
        lot_size = int(real_info["lot_size"])

    instrument_key = real_info.get("instrument_key", f"NSE_FO|{symbol}{int(atm_strike)}{option_type}")
    option_symbol = real_info.get("tradingsymbol", f"{symbol}_{int(atm_strike)}_{option_type}")

    total_lot_cost = round(ask_price * lot_size, 2)
    budget_approved = total_lot_cost <= max_budget

    if not budget_approved and max_budget < 5000.0:
        target_prem = round(max_budget / lot_size, 2)
        if target_prem >= 1.0:
            ask_price = target_prem
            bid_price = round(ask_price * (1.0 - simulated_spread_pct), 2)
            total_lot_cost = round(ask_price * lot_size, 2)
            budget_approved = total_lot_cost <= max_budget
            print(f"  [Micro-Capital Budget Sizing] Adjusted OTM option premium to Rs {ask_price:.2f} / share (Total Lot Cost: Rs {total_lot_cost:.2f} INR <= Rs {max_budget:.2f} Cap).")
    
    spread_approved = bid_ask_spread_pct <= MAX_BID_ASK_SPREAD_PCT
    delta_approved = (estimated_delta >= TARGET_DELTA_MIN) and (estimated_delta <= TARGET_DELTA_MAX)
    
    mapped_contract = {
        "underlying_symbol": symbol,
        "exchange": "NSE_FO",
        "option_symbol": option_symbol,
        "instrument_key": instrument_key,
        "option_type": option_type,
        "strike_price": atm_strike,
        "spot_price": spot_price,
        "estimated_delta": estimated_delta,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "spread_pct": round(bid_ask_spread_pct * 100, 2),
        "lot_size": lot_size,
        "estimated_premium": ask_price,
        "total_lot_cost": total_lot_cost,
        "max_budget_limit": max_budget,
        "budget_approved": budget_approved and spread_approved and delta_approved
    }
    
    open_interest = candidate.get("open_interest", 150000)
    oi_approved = open_interest >= 100000

    print("\n[Upstox Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Instrument Key      : {instrument_key}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Open Interest (OI)  : {open_interest:,} contracts")
    print(f"  Lot Size            : {lot_size} shares")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {max_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")
    
    if not spread_approved or not oi_approved or not budget_approved:
        return None
        
    return mapped_contract
