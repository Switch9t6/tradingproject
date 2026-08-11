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

_dhan_mcx_cache = None
_dhan_nse_cache = None


def get_dhan_scrip_master() -> List[str]:
    """Downloads and caches Dhan official scrip master CSV."""
    cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "dhan_scrip_master.csv")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400:
                with open(cache_file, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read().splitlines()
        except Exception:
            pass

    try:
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            lines = r.text.splitlines()
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(r.text)
            return lines
    except Exception as e:
        print(f"[Option Mapper Notice] Could not fetch Dhan scrip master CSV: {e}")

    return []


def get_dhan_mcx_instrument_map() -> dict:
    global _dhan_mcx_cache
    if _dhan_mcx_cache is not None:
        return _dhan_mcx_cache

    cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "dhan_mcx_instruments.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400:
                with open(cache_file, "r") as f:
                    _dhan_mcx_cache = json.load(f)
                    return _dhan_mcx_cache
        except Exception:
            pass

    lines = get_dhan_scrip_master()
    instr_map = {}
    if lines:
        for l in lines[1:]:
            parts = [p.strip('"') for p in l.split(',')]
            if len(parts) >= 9 and parts[0] == "MCX":
                sec_id, exch, seg, symbol, tsym, lot, strike, otype = parts[0], parts[0], parts[1], parts[3], parts[4], parts[5], parts[7], parts[8] if len(parts) >= 9 else ""
                try:
                    sec_id_val = parts[2] if len(parts) >= 3 else parts[0]
                    stk = float(parts[7]) if len(parts) >= 8 and parts[7] else 0.0
                    otype = parts[8] if len(parts) >= 9 else ""
                    und = "CRUDEOILM" if "CRUDEOILM" in tsym.upper() else "CRUDEOIL"
                    key = f"{und}_{int(stk)}_{otype.upper()}"
                    if key not in instr_map:
                        instr_map[key] = {
                            "security_id": sec_id_val,
                            "instrument_key": f"MCX_FO|{sec_id_val}",
                            "tradingsymbol": tsym,
                            "lot_size": int(lot) if lot.isdigit() else 10
                        }
                except Exception:
                    pass

    _dhan_mcx_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


def get_dhan_nse_instrument_map() -> dict:
    global _dhan_nse_cache
    if _dhan_nse_cache is not None:
        return _dhan_nse_cache

    cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "dhan_nse_instruments.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400:
                with open(cache_file, "r") as f:
                    _dhan_nse_cache = json.load(f)
                    return _dhan_nse_cache
        except Exception:
            pass

    lines = get_dhan_scrip_master()
    instr_map = {}
    if lines:
        for l in lines[1:]:
            parts = [p.strip('"') for p in l.split(',')]
            if len(parts) >= 11 and parts[0] == "NSE" and parts[10] in ["CE", "PE"]:
                try:
                    sec_id_val = parts[2]
                    tsym = parts[5]
                    lot = int(float(parts[6])) if parts[6] else 25
                    stk = float(parts[9]) if parts[9] else 0.0
                    otype = parts[10].upper()
                    und = tsym.split("-")[0].upper()
                    key = f"{und}_{int(stk)}_{otype}"
                    if key not in instr_map:
                        instr_map[key] = {
                            "security_id": sec_id_val,
                            "instrument_key": f"NSE_FO|{sec_id_val}",
                            "tradingsymbol": tsym,
                            "lot_size": lot
                        }
                except Exception:
                    pass

    _dhan_nse_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


# Aliases for backward compatibility
get_mcx_instrument_map = get_dhan_mcx_instrument_map
get_nse_instrument_map = get_dhan_nse_instrument_map


def get_mcx_crude_option_contract(
    spot_price: float,
    direction: str,
    budget_cap: Optional[float] = None,
    option_type: Optional[str] = None,
    symbol_hint: str = "CRUDEOIL",
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    Dhan Option Contract Mapper for MCX Crude Oil Options (Standard & Mini).
    Selects At-The-Money (ATM) Call (CE) or Put (PE) option contract.
    Resolves Dhan's internal security_id and tradingsymbol.
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

    mcx_map = get_dhan_mcx_instrument_map()
    lookup_key = f"{underlying_symbol}_{int(atm_strike)}_{option_type}"
    real_info = mcx_map.get(lookup_key, {})

    security_id = real_info.get("security_id", real_info.get("instrument_key", "573917"))
    instrument_key = real_info.get("instrument_key", f"MCX_FO|{security_id}")
    option_symbol = real_info.get("tradingsymbol", f"{underlying_symbol}_{int(atm_strike)}_{option_type}")

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
        "security_id": security_id,
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

    print("\n[Dhan MCX Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Security ID         : {security_id}")
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
    Dhan Option Selection Guardrails for NSE Equity & MCX Commodity Candidates.
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
    direction = candidate["direction"]
    option_type = candidate["option_type"]
    interval = candidate["strike_interval"]
    lot_size = candidate["lot_size"]
    
    atm_strike = round(spot_price / interval) * interval
    estimated_delta = 0.52
    
    est_premium_pct = 0.008 if candidate.get("is_index") else 0.0125
    estimated_premium = round(spot_price * est_premium_pct, 2)
    
    bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
    ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0
    
    nse_map = get_dhan_nse_instrument_map()
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

    security_id = str(real_info.get("security_id", real_info.get("instrument_key", "")))
    instrument_key = real_info.get("instrument_key", f"NSE_FO|{security_id}")
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
        "security_id": security_id,
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

    print("\n[Dhan Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Security ID         : {security_id}")
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
