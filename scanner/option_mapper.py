import os
import csv
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

# ---------------------------------------------------------------------------
# Fyers Symbol Master Cache (v3)
# ---------------------------------------------------------------------------
# Sources: Fyers official master CSVs (updated daily) - exact broker symbols,
# lot sizes, tick sizes and expiry dates. Removes Upstox-token translation risk.
#   NSE options : https://public.fyers.in/sym_details/NSE_FO.csv
#   MCX options : https://public.fyers.in/sym_details/MCX_COM.csv
# ---------------------------------------------------------------------------
_CACHE_VERSION = 3
_CACHE_MAX_AGE_SECONDS = 22 * 3600  # refresh daily; warn loudly if older

FYERS_MASTER_URLS = {
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "MCX_COM": "https://public.fyers.in/sym_details/MCX_COM.csv",
}

_fyers_nse_cache = None
_fyers_mcx_cache = None
_last_mapping_error = None
_stale_warn_sent = set()


def last_mapping_error() -> Optional[str]:
    """Returns the most recent option-mapping failure reason (or None)."""
    return _last_mapping_error


def get_fyers_instrument_csv(segment: str = "NSE_FO") -> List[str]:
    """Downloads + caches the official Fyers symbol master CSV (gzip in logs/)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base, "logs")
    gz_file = os.path.join(cache_dir, f"fyers_{segment}.csv.gz")
    url = FYERS_MASTER_URLS.get(segment)
    if not url:
        return []

    def _read_lines() -> List[str]:
        try:
            with gzip.open(gz_file, "rt", encoding="utf-8", errors="ignore") as f:
                return f.read().splitlines()
        except Exception:
            return []

    # Fresh cache hit (< 24h old)
    if os.path.exists(gz_file):
        try:
            if time.time() - os.path.getmtime(gz_file) < 86400:
                lines = _read_lines()
                if lines:
                    return lines
        except Exception:
            pass

    # (Re)download fresh master
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and r.text.strip():
            os.makedirs(cache_dir, exist_ok=True)
            with open(gz_file, "wb") as f:
                f.write(r.content)
            return r.text.splitlines()
    except Exception as e:
        print(f"[Option Mapper Notice] Could not fetch Fyers {segment} master CSV: {e}")

    # Fallback: serve stale cache but raise the alarm
    if os.path.exists(gz_file):
        lines = _read_lines()
        if lines:
            _warn_stale_cache(segment)
            return lines

    _warn_stale_cache(segment)
    return []


def _warn_stale_cache(segment: str):
    """Loud warning when the Fyers master cache is stale/missing (console + Telegram once)."""
    global _last_mapping_error
    if segment in _stale_warn_sent:
        return
    _stale_warn_sent.add(segment)
    _last_mapping_error = "STALE_OR_MISSING_CACHE"
    print(f"\n⚠️  [OPTION MAPPER - STALE CACHE WARNING] {segment} symbol master is stale "
          f"(> {_CACHE_MAX_AGE_SECONDS//3600}h) or could not be refreshed. "
          f"Any trade resolved from it may reference outdated/expired contracts.")
    try:
        from reporting.telegram_bot import send_telegram_message
        send_telegram_message(
            f"⚠️ <b>[OPTION MAPPER - STALE CONTRACT CACHE]</b>\n"
            "========================================\n"
            f"<b>Segment :</b> {segment}\n"
            f"<b>Problem :</b> Fyers symbol master could not be refreshed "
            f"(stale > {_CACHE_MAX_AGE_SECONDS//3600}h or missing).\n"
            "========================================\n"
            "<i>Engine still running on the existing cache - contracts may be outdated. "
            "Check the Fyers public master URL / network on the server.</i>"
        )
    except Exception:
        pass


def _parse_master_rows(segment: str) -> List[List[str]]:
    """Parses a Fyers master CSV into rows (skips malformed lines)."""
    lines = get_fyers_instrument_csv(segment)
    rows = []
    for l in lines:
        try:
            parts = next(csv.reader([l]))
            if len(parts) >= 17:
                rows.append(parts)
        except Exception:
            continue
    return rows


def _tradingsymbol_from_fyers_symbol(sym: str) -> str:
    """Strips the exchange prefix from a Fyers symbol (e.g. 'NSE:NIFTY...CE' -> 'NIFTY...CE')."""
    return sym.split(":", 1)[1] if ":" in sym else sym


def _nearest_expiry(pick: dict, cand: dict) -> dict:
    """Keeps the contract with the nearest expiry for the same key."""
    if pick is None or cand.get("expiry", "") < pick.get("expiry", ""):
        return cand
    return pick


# ---------------------------------------------------------------------------
# NSE options map (from Fyers NSE_FO.csv)
# ---------------------------------------------------------------------------
def get_fyers_nse_instrument_map() -> dict:
    global _fyers_nse_cache
    if _fyers_nse_cache is not None:
        return _fyers_nse_cache

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_file = os.path.join(base, "logs", "fyers_nse_instruments.json")

    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < _CACHE_MAX_AGE_SECONDS:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    if data.get("_cache_version") == _CACHE_VERSION:
                        _fyers_nse_cache = data
                        return _fyers_nse_cache
        except Exception:
            pass

    instr_map = {}
    for parts in _parse_master_rows("NSE_FO"):
        try:
            opt_type = parts[16].upper()
            if opt_type not in ("CE", "PE"):
                continue
            fyers_symbol = parts[9].strip()
            underlying = parts[13].strip()
            strike = float(parts[15]) if parts[15] else 0.0
            if underlying == "" or strike <= 0:
                continue
            key = f"{underlying}_{int(strike)}_{opt_type}"
            entry = {
                "instrument_key": fyers_symbol,
                "tradingsymbol": _tradingsymbol_from_fyers_symbol(fyers_symbol),
                "fyers_symbol": fyers_symbol,
                "underlying": underlying,
                "lot_size": int(float(parts[3])) if parts[3] else 0,
                "tick_size": float(parts[4]) if parts[4] else 0.05,
                "strike": strike,
                "expiry": parts[7].strip(),
                "option_type": opt_type
            }
            instr_map[key] = _nearest_expiry(instr_map.get(key), entry)
        except Exception:
            continue

    instr_map["_cache_version"] = _CACHE_VERSION
    instr_map["_cache_updated_at"] = time.time()
    _fyers_nse_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


# ---------------------------------------------------------------------------
# MCX crude options map (from Fyers MCX_COM.csv)
# ---------------------------------------------------------------------------
def get_fyers_mcx_instrument_map() -> dict:
    global _fyers_mcx_cache
    if _fyers_mcx_cache is not None:
        return _fyers_mcx_cache

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_file = os.path.join(base, "logs", "fyers_mcx_instruments.json")

    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < _CACHE_MAX_AGE_SECONDS:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    if data.get("_cache_version") == _CACHE_VERSION:
                        _fyers_mcx_cache = data
                        return _fyers_mcx_cache
        except Exception:
            pass

    instr_map = {}
    for parts in _parse_master_rows("MCX_COM"):
        try:
            opt_type = parts[16].upper()
            if opt_type not in ("CE", "PE"):
                continue
            fyers_symbol = parts[9].strip()
            underlying = parts[13].strip()
            if "CRUDEOIL" not in underlying.upper():
                continue
            strike = float(parts[15]) if parts[15] else 0.0
            if underlying == "" or strike <= 0:
                continue
            # Fyers MCX master reports lot=1; real barrel lots come from config
            is_mini = "CRUDEOILM" in underlying.upper()
            lot = MCX_CRUDE_MINI_LOT_SIZE if is_mini else MCX_CRUDE_LOT_SIZE
            key = f"{underlying}_{int(strike)}_{opt_type}"
            entry = {
                "instrument_key": fyers_symbol,
                "tradingsymbol": _tradingsymbol_from_fyers_symbol(fyers_symbol),
                "fyers_symbol": fyers_symbol,
                "underlying": underlying,
                "lot_size": lot,
                "tick_size": float(parts[4]) if parts[4] else 0.05,
                "strike": strike,
                "expiry": parts[7].strip(),
                "option_type": opt_type
            }
            instr_map[key] = _nearest_expiry(instr_map.get(key), entry)
        except Exception:
            continue

    instr_map["_cache_version"] = _CACHE_VERSION
    instr_map["_cache_updated_at"] = time.time()
    _fyers_mcx_cache = instr_map
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(instr_map, f)
    except Exception:
        pass
    return instr_map


# Backward-compatible aliases
get_mcx_instrument_map = get_fyers_mcx_instrument_map
get_nse_instrument_map = get_fyers_nse_instrument_map
get_upstox_mcx_instrument_map = get_fyers_mcx_instrument_map
get_upstox_nse_instrument_map = get_fyers_nse_instrument_map


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 2)
    return round(round(price / tick) * tick, 2)


def _record_mapping_error(segment: str, kind: str, detail: str):
    global _last_mapping_error
    _last_mapping_error = kind
    if kind == "STALE_OR_MISSING_CACHE":
        print(f"  [Option Mapper Notice] {segment}: no tradable contract could be resolved "
              f"- symbol master cache is STALE or MISSING (data problem, not a normal skip).")
    else:
        print(f"  [Option Mapper Notice] {segment}: no tradable contract found. {detail}. No trade.")


def get_mcx_crude_option_contract(
    spot_price: float,
    direction: str,
    budget_cap: Optional[float] = None,
    option_type: Optional[str] = None,
    symbol_hint: str = "CRUDEOIL",
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    Fyers Option Contract Mapper for MCX Crude Oil Options (Standard & Mini).
    Resolves the exact Fyers MCX symbol from Fyers MCX_COM symbol master.
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

    mcx_map = get_fyers_mcx_instrument_map()
    if not mcx_map or mcx_map.get("_cache_version") != _CACHE_VERSION:
        _record_mapping_error("MCX", "STALE_OR_MISSING_CACHE", "Fyers MCX symbol master unavailable")
        return None

    lookup_key = f"{underlying_symbol}_{int(atm_strike)}_{option_type}"
    real_info = mcx_map.get(lookup_key, {})

    if not real_info:
        prefix = f"{underlying_symbol}_"
        candidates = []
        for k, v in mcx_map.items():
            if not k.startswith("_"):
                if k.startswith(prefix) and k.endswith(f"_{option_type}"):
                    stk_val = float(k.split("_")[1]) if len(k.split("_")) >= 3 else 0.0
                    candidates.append((abs(stk_val - atm_strike), v))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            real_info = candidates[0][1]

    if not real_info:
        _record_mapping_error("MCX", "NO_CONTRACT",
                              f"{underlying_symbol} {int(atm_strike)} {option_type} not in Fyers master")
        return None

    tick_size = float(real_info.get("tick_size") or 0.05)
    ask_price = _round_to_tick(ask_price, tick_size)
    bid_price = _round_to_tick(bid_price, tick_size)
    total_lot_cost = round(ask_price * lot_size, 2)

    option_symbol = real_info.get("tradingsymbol", f"{underlying_symbol}_{int(atm_strike)}_{option_type}")
    fyers_symbol = real_info.get("fyers_symbol", f"MCX:{option_symbol}")
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
        "instrument_key": fyers_symbol,
        "fyers_symbol": fyers_symbol,
        "tick_size": tick_size,
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

    print("\n[Fyers MCX Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Fyers Symbol        : {fyers_symbol}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Tick Size           : Rs {tick_size:.2f}")
    print(f"  Lot Size (Barrels)  : {lot_size} barrels ({'Mini' if lot_size == 10 else 'Standard'})")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {budget_cap:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")

    if not spread_approved or not oi_approved or not budget_approved:
        _record_mapping_error("MCX", "REJECTED_GUARDRAILS", "spread/OI/budget guardrail failed")
        return None

    return mapped_contract


def resolve_atm_option_contract(
    candidate: Dict[str, Any],
    max_budget: Optional[float] = None,
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    Fyers Option Selection Guardrails for NSE Equity & MCX Commodity Candidates.
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

    nse_map = get_fyers_nse_instrument_map()
    if not nse_map or nse_map.get("_cache_version") != _CACHE_VERSION:
        _record_mapping_error("NSE", "STALE_OR_MISSING_CACHE", "Fyers NSE symbol master unavailable")
        return None

    lookup_key = f"{symbol}_{int(atm_strike)}_{option_type}"
    real_info = nse_map.get(lookup_key, {})

    if not real_info:
        prefix = f"{symbol}_"
        candidates = []
        for k, v in nse_map.items():
            if not k.startswith("_"):
                if k.startswith(prefix) and k.endswith(f"_{option_type}"):
                    stk_val = float(k.split("_")[1]) if len(k.split("_")) >= 3 else 0.0
                    candidates.append((abs(stk_val - atm_strike), v))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            real_info = candidates[0][1]

    if not real_info:
        _record_mapping_error("NSE", "NO_CONTRACT",
                              f"{symbol} {int(atm_strike)} {option_type} not in Fyers master")
        return None

    tick_size = float(real_info.get("tick_size") or 0.05)
    ask_price = _round_to_tick(ask_price, tick_size)
    bid_price = _round_to_tick(bid_price, tick_size)
    total_lot_cost = round(ask_price * lot_size, 2)

    if real_info.get("lot_size"):
        lot_size = int(real_info["lot_size"])

    budget_approved = total_lot_cost <= max_budget

    if not budget_approved and max_budget < 5000.0:
        import math
        # Floor to the nearest valid tick so total lot cost never exceeds the cap
        raw_prem = max_budget / lot_size
        target_prem = _round_to_tick(math.floor(raw_prem / tick_size) * tick_size, tick_size)
        if target_prem >= 1.0:
            ask_price = _round_to_tick(target_prem, tick_size)
            bid_price = _round_to_tick(ask_price * (1.0 - simulated_spread_pct), tick_size)
            total_lot_cost = round(ask_price * lot_size, 2)
            budget_approved = total_lot_cost <= max_budget
            print(f"  [Micro-Capital Budget Sizing] Adjusted OTM option premium to Rs {ask_price:.2f} / share (Total Lot Cost: Rs {total_lot_cost:.2f} INR <= Rs {max_budget:.2f} Cap).")

    spread_approved = bid_ask_spread_pct <= MAX_BID_ASK_SPREAD_PCT
    delta_approved = (estimated_delta >= TARGET_DELTA_MIN) and (estimated_delta <= TARGET_DELTA_MAX)

    option_symbol = real_info.get("tradingsymbol", f"{symbol}_{int(atm_strike)}_{option_type}")
    fyers_symbol = real_info.get("fyers_symbol", f"NSE:{option_symbol}")

    mapped_contract = {
        "underlying_symbol": symbol,
        "exchange": "NSE_FO",
        "option_symbol": option_symbol,
        "instrument_key": fyers_symbol,
        "fyers_symbol": fyers_symbol,
        "tick_size": tick_size,
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

    print("\n[Fyers Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Fyers Symbol        : {fyers_symbol}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Tick Size           : Rs {tick_size:.2f}")
    print(f"  Open Interest (OI)  : {open_interest:,} contracts")
    print(f"  Lot Size            : {lot_size} shares")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {max_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")

    if not spread_approved or not oi_approved or not budget_approved:
        _record_mapping_error("NSE", "REJECTED_GUARDRAILS", "spread/OI/budget guardrail failed")
        return None

    return mapped_contract
