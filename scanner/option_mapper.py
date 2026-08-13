import os
import csv
import sys
import time
import math
import gzip
import json
import re
import datetime
import requests
from typing import Dict, Any, Optional, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    MAX_BID_ASK_SPREAD_PCT,
    MCX_CRUDE_SYMBOL,
    MCX_CRUDE_LOT_SIZE,
    MCX_CRUDE_MINI_LOT_SIZE,
    MCX_CRUDE_STRIKE_STEP,
    MOMENTUM_MODERATE_PCT,
    MOMENTUM_STRONG_PCT,
    MAX_STRIKE_OFFSET,
    MAX_STRIKE_DEVIATION_STEPS,
    MAX_ALLOCATION_PCT,
    SLIPPAGE_BUFFER_PCT,
    OPTION_DELTA_MIN,
    OPTION_DELTA_MAX,
    ENABLE_OI_FILTER,
    NSE_MIN_OPTION_OPEN_INTEREST,
    MCX_MIN_OPTION_OPEN_INTEREST,
)

# ---------------------------------------------------------------------------
# Fyers Symbol Master Cache (v3)
# ---------------------------------------------------------------------------
# Sources: Fyers official master CSVs (updated daily) - exact broker symbols,
# lot sizes, tick sizes and expiry dates.
#   NSE options : https://public.fyers.in/sym_details/NSE_FO.csv
#   MCX options : https://public.fyers.in/sym_details/MCX_COM.csv
# ---------------------------------------------------------------------------
# v4: expiry normalized from the epoch column (parts[18]) to a comparable ISO
#     date, expired contracts are dropped at build time, and per-strike entries
#     keep the nearest ACTIVE expiry (never an expired/far-past contract).
_CACHE_VERSION = 4
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


# ---------------------------------------------------------------------------
# Strike selection helpers
# ---------------------------------------------------------------------------
def _snap_to_strike(spot_price: float, strike_step: float) -> float:
    """Snaps spot to the nearest strike using HALF-UP rounding (no banker's-even bias).

    Fix: Python's built-in round() rounds .5 to even (boundary flip-flopping);
    floor(x + 0.5) always rounds half away from zero.
    """
    if strike_step <= 0:
        return float(spot_price)
    return math.floor((float(spot_price) / strike_step) + 0.5) * strike_step


def _momentum_strike_offset(momentum_pct: float) -> int:
    """
    Number of strikes to move OTM based on momentum strength:
      0 -> 0 OTM (ATM), |mom| >= MOMENTUM_MODERATE_PCT -> 1, >= STRONG -> 2.
    Hard-capped by MAX_STRIKE_OFFSET.
    """
    try:
        mom = abs(float(momentum_pct or 0.0))
    except Exception:
        mom = 0.0
    if mom >= MOMENTUM_STRONG_PCT:
        offset = 2
    elif mom >= MOMENTUM_MODERATE_PCT:
        offset = 1
    else:
        offset = 0
    return min(offset, MAX_STRIKE_OFFSET)


def _apply_otm_offset(base_strike: float, strike_step: float, offset: int, option_type: str) -> float:
    """Moves the strike OTM: higher for CE (bullish), lower for PE (bearish)."""
    step = float(strike_step)
    if option_type.upper() == "PE":
        return base_strike - (offset * step)
    return base_strike + (offset * step)


def _underlying_fyers_symbol(candidate: Dict[str, Any]) -> str:
    """Underlying symbol string needed by the Fyers optionchain endpoint."""
    symbol = str(candidate.get("symbol") or "").upper()
    if candidate.get("is_mcx") or symbol in ("CRUDEOIL", "CRUDEOILM"):
        return ""  # MCX chains not available via optionchain -> estimate mode
    index_map = {
        "NIFTY": "NSE:NIFTY50-INDEX",
        "FINNIFTY": "NSE:FINNIFTY50-INDEX",
        "BANKNIFTY": "NSE:BANKNIFTY-INDEX",
        "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    }
    if candidate.get("is_index"):
        return index_map.get(symbol, "")
    return f"NSE:{symbol}-EQ"


# ---------------------------------------------------------------------------
# Live data helpers (wallet + option chain) - best-effort, never raise
# ---------------------------------------------------------------------------
def _live_available_cash(access_token: Optional[str]) -> Optional[float]:
    """Fetches available unencumbered broker cash. None when unavailable."""
    if not access_token or str(access_token).startswith(("MOCK", "your_")):
        return None
    try:
        from execution.fyers_trader import get_live_wallet_balance
        bal = get_live_wallet_balance(access_token=access_token)
        if bal and bal > 0:
            return float(bal)
    except Exception as e:
        print(f"  [Option Mapper Notice] Live wallet balance unavailable ({e}). Using passed budget cap.")
    return None


def _dynamic_usable_budget(requested_cap: Optional[float], access_token: Optional[str]):
    """
    Per-trade usable budget from LIVE available wallet cash:
        usable_budget = available_cash * MAX_ALLOCATION_PCT * (1 - SLIPPAGE_BUFFER_PCT)
    Never exceeds the requested cap (micro-capital / caller cap). Falls back to the
    requested cap in dry-run or when live cash cannot be fetched.
    Returns (usable_budget, wallet_source) where wallet_source in
    {"live_wallet", "passed_cap"}.
    """
    req = float(requested_cap) if requested_cap is not None else float("inf")
    cash = _live_available_cash(access_token)
    if cash and cash > 0:
        usable = cash * MAX_ALLOCATION_PCT * (1.0 - SLIPPAGE_BUFFER_PCT)
        return min(req, usable), "live_wallet"
    return req, "passed_cap"


def _live_option_chain(access_token: str, underlying_symbol: str, option_type: str,
                       strikecount: int = 12) -> Optional[Dict[str, Dict[str, float]]]:
    """
    Fetches real strike-wise OI / bid / ask / delta via the Fyers optionchain API.
    Returns {int(strike): {"oi","delta","bid","ask","ltp"}} or None on any failure.
    Never raises; callers fall back to deterministic estimation when None.
    """
    if not underlying_symbol:
        return None
    if not access_token or str(access_token).startswith(("MOCK", "your_")):
        return None
    try:
        from fyers_apiv3 import fyersModel
        from config.settings import FYERS_APP_ID, FYERS_TOKEN_FILE_PATH
        app_id = (FYERS_APP_ID or os.getenv("FYERS_APP_ID", "")).strip()
        if not app_id or app_id.startswith("YOUR_"):
            return None
        fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=False,
                                      log_path=os.path.dirname(FYERS_TOKEN_FILE_PATH))
        resp = fyers.optionchain(data={"symbol": underlying_symbol, "strikecount": strikecount, "greeks": "1"})
        if not (isinstance(resp, dict) and resp.get("s") == "ok"):
            print(f"  [Option Mapper Notice] optionchain failed for {underlying_symbol}: {str(resp)[:120]}")
            return None
        chain: Dict[str, Dict[str, float]] = {}
        for exp_key, exp in (resp.get("data") or {}).items():
            if not isinstance(exp, dict):
                continue
            for row in (exp.get("optionsChain") or []):
                if not isinstance(row, dict):
                    continue
                try:
                    strike = float(row.get("strikePrice") or 0)
                except Exception:
                    continue
                if strike <= 0:
                    continue
                side = row.get(option_type)
                if not isinstance(side, dict):
                    continue
                greeks = side.get("greeks") or {}
                try:
                    chain[int(strike)] = {
                        "oi": float(side.get("openInterest") or side.get("oi") or 0),
                        "delta": float(greeks.get("delta") or 0),
                        "bid": float(side.get("bid") or 0),
                        "ask": float(side.get("ask") or 0),
                        "ltp": float(side.get("ltp") or side.get("last_price") or 0),
                    }
                except Exception:
                    continue
        return chain or None
    except Exception as e:
        print(f"  [Option Mapper Notice] Live option chain unavailable for {underlying_symbol} ({e}). Using estimates.")
        return None


def _pick_live_strike(chain: Dict[str, Dict[str, float]], base_strike: float, strike_step: float,
                      option_type: str, preferred_offset: int, lot_size: int, budget: float,
                      min_o_i: float) -> Optional[Dict[str, Any]]:
    """
    Selects the best strike from REAL option-chain data with a budget-aware OTM walk:
      * prefer the momentum-preferred OTM strike;
      * if its cost (ask x lot) exceeds the budget, step FURTHER OTM (up to
        MAX_STRIKE_OFFSET) where premium is cheaper;
      * otherwise fall back toward ATM;
      * real delta must stay inside [OPTION_DELTA_MIN, OPTION_DELTA_MAX] when the
        chain carries greeks (absent greeks -> no fake delta gate);
      * real OI must pass the liquidity floor when ENABLE_OI_FILTER is True.
    Returns {"strike","entry",...} or None when nothing qualifies.
    """
    step = max(1, int(strike_step))
    base = int(base_strike)
    has_delta = any(e.get("delta", 0) > 0 for e in chain.values())
    has_oi = any(e.get("oi", 0) > 0 for e in chain.values())

    def _qualifies(strike: int) -> Optional[Dict[str, float]]:
        entry = chain.get(strike)
        if not entry:
            return None
        if has_delta and not (OPTION_DELTA_MIN <= entry.get("delta", 0) <= OPTION_DELTA_MAX):
            return None
        if has_oi and ENABLE_OI_FILTER and entry.get("oi", 0) < min_o_i:
            return None
        ask = entry.get("ask", 0)
        if ask <= 0:
            return None
        if (ask * lot_size) > budget:
            return None
        return entry

    order = list(range(preferred_offset, MAX_STRIKE_OFFSET + 1)) + list(range(preferred_offset - 1, -1, -1))
    for off in order:
        strike = base + (off * step) if option_type.upper() == "CE" else base - (off * step)
        entry = _qualifies(strike)
        if entry:
            return {"strike": strike, "entry": entry, "gates_estimated": False,
                    "real_delta": bool(has_delta), "real_oi": bool(has_oi), "otm_offset": off}
    return None


# ---------------------------------------------------------------------------
# Master cache parsing (stale-safe)
# ---------------------------------------------------------------------------
def get_fyers_instrument_csv(segment: str = "NSE_FO") -> List[str]:
    """Downloads + caches the official Fyers symbol master CSV."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base, "logs")
    gz_file = os.path.join(cache_dir, f"fyers_{segment}.csv.gz")
    url = FYERS_MASTER_URLS.get(segment)
    if not url:
        return []

    def _read_lines() -> List[str]:
        # The downloader stores raw CSV; tolerate both raw and true gzip payloads.
        try:
            with gzip.open(gz_file, "rt", encoding="utf-8", errors="ignore") as f:
                return f.read().splitlines()
        except Exception:
            try:
                with open(gz_file, "r", encoding="utf-8", errors="ignore") as f:
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


def _expiry_iso_from_parts(parts: List[str]) -> str:
    """
    Normalizes the CONTRACT expiry to a comparable ISO date (YYYY-MM-DD).

    Fix: the publish date lives in parts[7] and is IDENTICAL for every row, so it
    can never be used for expiry selection. The real expiry epoch is parts[8] for
    NSE master rows (MCX mirrors it in parts[18] too), so both are probed.
    """
    ts = 0
    for idx in (8, 18):
        try:
            if len(parts) > idx and str(parts[idx]).strip():
                ts = int(float(parts[idx]))
                break
        except Exception:
            continue
    if ts > 0:
        try:
            return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass
    raw = parts[7].strip() if len(parts) > 7 else ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return raw  # unknown format -> keep as-is


def _nearest_expiry(pick: dict, cand: dict) -> dict:
    """
    Keeps the contract with the nearest (earliest) expiry for the same key.
    Entries are already normalized to ISO YYYY-MM-DD, so string ordering == time
    ordering. Cache metadata rows (no 'expiry') keep the existing pick.
    """
    if pick is None or cand.get("expiry", "") < pick.get("expiry", ""):
        return cand
    return pick


def _today_iso() -> str:
    """Current UTC date as YYYY-MM-DD (contracts expiring before this are dead)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


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
    today_iso = _today_iso()
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
            expiry_iso = _expiry_iso_from_parts(parts)
            # Safety: never collapse to / serve an already-expired contract.
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_iso) and expiry_iso < today_iso:
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
                "expiry": expiry_iso,
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
    today_iso = _today_iso()
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
            expiry_iso = _expiry_iso_from_parts(parts)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_iso) and expiry_iso < today_iso:
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
                "expiry": expiry_iso,
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
    elif kind == "STRIKE_OUT_OF_BOUNDS_OR_MISSING":
        print(f"  [Option Mapper Notice] {segment}: {detail}. TRADE SKIPPED (never trade a wrong/deep ITM strike).")
    elif kind == "INSUFFICIENT_WALLET_BALANCE":
        print(f"  [Option Mapper Notice] {segment}: {detail}. TRADE SKIPPED (insufficient usable wallet).")
    else:
        print(f"  [Option Mapper Notice] {segment}: no tradable contract found. {detail}. No trade.")


def _clear_mapping_error():
    global _last_mapping_error
    _last_mapping_error = None


def _lookup_with_deviation_guard(instr_map: dict, symbol: str, target_strike: float,
                                 option_type: str, strike_step: float, segment: str) -> Optional[dict]:
    """
    Exact-key lookup on the master map; on miss, a nearest-available-strike
    fallback that is STRICTLY bounded by MAX_STRIKE_DEVIATION_STEPS.

    Fix: previously the fallback grabbed the nearest key with no boundary check,
    which produced deep-ITM/OTM orders from a stale/partial cache (e.g. buying a
    6200 CE when spot was ~7800). Now any deviation beyond the guard skips the
    trade (STRIKE_OUT_OF_BOUNDS_OR_MISSING).
    """
    try:
        target = float(target_strike)
    except Exception:
        target = 0.0
    key = f"{symbol}_{int(target)}_{option_type}"
    real_info = instr_map.get(key, {})
    if not real_info:
        prefix = f"{symbol}_"
        near = []  # the two nearest strikes as (deviation, strike_value)
        for k, v in instr_map.items():
            if k.startswith("_"):
                continue
            if not (k.startswith(prefix) and k.endswith(f"_{option_type}")):
                continue
            try:
                stk_val = float(k.split("_")[1])
            except Exception:
                continue
            dev = abs(stk_val - target)
            if len(near) < 2 or dev < near[1][0]:
                near.append((dev, stk_val))
                # prefer OTM side on equal deviation (higher for CE, lower for PE)
                if option_type.upper() == "CE":
                    near.sort(key=lambda x: (x[0], -x[1]))
                else:
                    near.sort(key=lambda x: (x[0], x[1]))
                del near[2:]
        if near:
            best_dev = near[0][0]
            # Tolerance is based on the REAL strike spacing observed in the master
            # data (gap between the two nearest strikes), not the scanner's
            # heuristic interval - so a valid nearest-strike mapping (e.g. a
            # 50-pt step resolved from a 5-pt heuristic) is accepted, while a
            # shifted/stale strike range is still rejected by real-step distance.
            if len(near) > 1:
                real_spacing = abs(near[1][1] - near[0][1])
            else:
                real_spacing = float(strike_step or 1.0)
            allowed_dev = max(1.0, MAX_STRIKE_DEVIATION_STEPS * max(real_spacing, float(strike_step or 1.0)))
            if best_dev > allowed_dev:
                _record_mapping_error(
                    segment, "STRIKE_OUT_OF_BOUNDS_OR_MISSING",
                    f"nearest {symbol} {option_type} strike deviates {best_dev:.1f} pts "
                    f"(allowed <= {allowed_dev:.1f} = {MAX_STRIKE_DEVIATION_STEPS} real steps); "
                    f"likely stale/partial master cache")
                return None
            real_info = instr_map[f"{symbol}_{int(near[0][1])}_{option_type}"]
    if not real_info:
        _record_mapping_error(segment, "STRIKE_OUT_OF_BOUNDS_OR_MISSING",
                              f"{symbol} {int(target)} {option_type} not in Fyers master (no in-range strike)")
        return None
    return real_info


# ===========================================================================
# MCX CRUDE OIL OPTION CONTRACT RESOLVER
# ===========================================================================
def _resolve_mcx_underlying(
    mcx_map: dict,
    underlying_symbol: str,
    spot_price: float,
    option_type: str,
    base_strike: float,
    strike_step: float,
    preferred_offset: int,
    lot_size: int,
    usable_budget: float,
    chain: Optional[Dict[str, Dict[str, float]]],
    simulated_spread_pct: float,
) -> Optional[Dict[str, Any]]:
    """Resolves + builds the mapped MCX contract for one underlying (std or mini)."""
    pick = None
    if chain:
        pick = _pick_live_strike(chain, base_strike, strike_step, option_type, preferred_offset,
                                 lot_size, usable_budget, MCX_MIN_OPTION_OPEN_INTEREST)
    if pick:
        target_strike = float(pick["strike"])
        estimated_delta = float(pick["entry"]["delta"])
        oi_value = float(pick["entry"]["oi"]) if pick["real_oi"] else None
        ask_price = _round_to_tick(pick["entry"]["ask"], 0.05)
        bid_price = _round_to_tick(pick["entry"]["bid"], 0.05)
        gates_estimated = False
        offset_used = pick["otm_offset"]
    else:
        # Estimate mode: same estimated premium for every strike (no fake deltas).
        target_strike = _apply_otm_offset(base_strike, strike_step, preferred_offset, option_type)
        estimated_delta = 0.52  # display only; never used as a gate
        estimated_premium = round(spot_price * 0.015, 2)
        ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
        bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
        oi_value = None
        gates_estimated = True
        offset_used = preferred_offset

    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0

    real_info = _lookup_with_deviation_guard(mcx_map, underlying_symbol, target_strike,
                                             option_type, strike_step, "MCX")
    if not real_info:
        return None

    # The guard may have snapped to the nearest real contract strike; report the
    # ACTUAL resolved strike, never the computed (possibly mid-step) target.
    target_strike = float(real_info.get("strike") or target_strike)

    tick_size = float(real_info.get("tick_size") or 0.05)
    ask_price = _round_to_tick(ask_price, tick_size)
    bid_price = _round_to_tick(bid_price, tick_size)
    total_lot_cost = round(ask_price * lot_size, 2)

    option_symbol = real_info.get("tradingsymbol", f"{underlying_symbol}_{int(target_strike)}_{option_type}")
    fyers_symbol = real_info.get("fyers_symbol", f"MCX:{option_symbol}")

    budget_approved = total_lot_cost <= usable_budget
    spread_approved = bid_ask_spread_pct <= MAX_BID_ASK_SPREAD_PCT
    delta_approved = True  # real delta enforced inside _pick_live_strike; estimate mode never fakes
    oi_approved = True
    if not gates_estimated and oi_value is not None:
        oi_approved = (not ENABLE_OI_FILTER) or (oi_value >= MCX_MIN_OPTION_OPEN_INTEREST)

    if not (budget_approved and spread_approved and delta_approved and oi_approved):
        _record_mapping_error("MCX", "REJECTED_GUARDRAILS", "spread/OI/budget/delta guardrail failed")
        return None

    mapped_contract = {
        "underlying_symbol": underlying_symbol,
        "exchange": "MCX_FO",
        "is_mcx": True,
        "option_symbol": option_symbol,
        "instrument_key": fyers_symbol,
        "fyers_symbol": fyers_symbol,
        "tick_size": tick_size,
        "option_type": option_type,
        "strike_price": target_strike,
        "spot_price": spot_price,
        "estimated_delta": estimated_delta,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "spread_pct": round(bid_ask_spread_pct * 100, 2),
        "lot_size": lot_size,
        "estimated_premium": ask_price,
        "total_lot_cost": total_lot_cost,
        "max_budget_limit": usable_budget,
        "usable_budget": usable_budget,
        "gates_estimated": gates_estimated,
        "strike_offset": offset_used,
        "budget_approved": budget_approved and spread_approved and delta_approved and oi_approved
    }
    if oi_value is not None:
        mapped_contract["open_interest"] = oi_value

    print("\n[Fyers MCX Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Fyers Symbol        : {fyers_symbol}")
    print(f"  Strike Selection    : ATM Rs {base_strike:.2f} +{offset_used} step(s) OTM -> Rs {target_strike:.2f} ({option_type})")
    print(f"  Wallet Budget       : Rs {usable_budget:,.2f} INR")
    if not gates_estimated:
        print(f"  Live Delta          : {estimated_delta:.2f} (real)")
        print(f"  Live OI             : {int(oi_value) if oi_value is not None else 'n/a'} contracts (real)")
    else:
        print(f"  Live Data           : unavailable (est. delta {estimated_delta:.2f}, OI gate skipped)")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Tick Size           : Rs {tick_size:.2f}")
    print(f"  Lot Size (Barrels)  : {lot_size} barrels ({'Mini' if lot_size == 10 else 'Standard'})")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Usable Budget: Rs {usable_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")

    _clear_mapping_error()
    return mapped_contract


def get_mcx_crude_option_contract(
    spot_price: float,
    direction: str,
    budget_cap: Optional[float] = None,
    option_type: Optional[str] = None,
    symbol_hint: str = "CRUDEOIL",
    simulated_spread_pct: float = 0.008,
    momentum_pct: float = 0.0,
    access_token: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fyers Option Contract Mapper for MCX Crude Oil Options (Standard & Mini).

    Fixes:
      * Half-up strike snapping (MCX_CRUDE_STRIKE_STEP=50 strictly enforced).
      * Momentum-based OTM offset for cheaper premium.
      * Dynamic usable wallet budget (MAX_ALLOCATION_PCT + SLIPPAGE_BUFFER_PCT).
      * Contract downgrade: Standard (100bbl) -> Mini (10bbl) when the standard lot
        exceeds the usable budget, then a budget-aware OTM walk.
      * Strict nearest-strike deviation guard; INSUFFICIENT_WALLET_BALANCE when no
        affordable strike exists within MAX_STRIKE_OFFSET OTM steps.
      * Real OI/delta gates from live option-chain data (estimate mode never fakes).
    """
    if budget_cap is None:
        budget_cap = float("inf")
    if not option_type:
        option_type = "CE" if direction.upper() == "BULLISH" else "PE"

    strike_step = MCX_CRUDE_STRIKE_STEP
    base_strike = _snap_to_strike(spot_price, strike_step)
    offset_steps = _momentum_strike_offset(momentum_pct)

    usable_budget, wallet_src = _dynamic_usable_budget(budget_cap, access_token)
    if wallet_src == "live_wallet":
        print(f"  [Option Mapper Notice] Dynamic MCX wallet budget: Rs {usable_budget:,.2f} INR "
              f"(available cash x {MAX_ALLOCATION_PCT} x (1-{SLIPPAGE_BUFFER_PCT})).")

    mcx_map = get_fyers_mcx_instrument_map()
    if not mcx_map or mcx_map.get("_cache_version") != _CACHE_VERSION:
        _record_mapping_error("MCX", "STALE_OR_MISSING_CACHE", "Fyers MCX symbol master unavailable")
        return None

    chain = _live_option_chain(access_token, "MCX:CRUDEOIL", option_type)
    forced_mini = str(symbol_hint).upper() == "CRUDEOILM"

    if not forced_mini:
        mapped = _resolve_mcx_underlying(
            mcx_map, MCX_CRUDE_SYMBOL, spot_price, option_type, base_strike, strike_step,
            offset_steps, MCX_CRUDE_LOT_SIZE, usable_budget, chain, simulated_spread_pct)
        if mapped is not None:
            return mapped

    mapped = _resolve_mcx_underlying(
        mcx_map, "CRUDEOILM", spot_price, option_type, base_strike, strike_step,
        offset_steps, MCX_CRUDE_MINI_LOT_SIZE, usable_budget, chain, simulated_spread_pct)
    if mapped is not None:
        return mapped

    # Nothing affordable -> keep any specific master/strike error already recorded,
    # otherwise report cleanly as insufficient wallet budget.
    if last_mapping_error() not in ("STALE_OR_MISSING_CACHE", "STRIKE_OUT_OF_BOUNDS_OR_MISSING", "NO_CONTRACT"):
        _record_mapping_error("MCX", "INSUFFICIENT_WALLET_BALANCE",
                              f"no affordable {option_type} strike within {MAX_STRIKE_OFFSET} OTM steps "
                              f"<= Rs {usable_budget:,.2f}")
    return None


# ===========================================================================
# NSE / ROUTER OPTION CONTRACT RESOLVER
# ===========================================================================
def resolve_atm_option_contract(
    candidate: Dict[str, Any],
    max_budget: Optional[float] = None,
    simulated_spread_pct: float = 0.008,
    access_token: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fyers Option Selection Guardrails for NSE Equity & MCX Commodity Candidates.
    Routes MCX Crude Oil candidates to get_mcx_crude_option_contract().

    Same fixes as the MCX path: half-up snapping, momentum OTM offset, dynamic
    usable wallet budget with a budget-aware OTM walk, strict strike deviation
    guard, and real delta/OI gates when live data is available.
    """
    if candidate.get("is_mcx") or candidate.get("symbol") in [MCX_CRUDE_SYMBOL, "CRUDEOILM"]:
        return get_mcx_crude_option_contract(
            spot_price=candidate["spot_price"],
            direction=candidate["direction"],
            budget_cap=max_budget,
            option_type=candidate.get("option_type"),
            symbol_hint=candidate.get("symbol", "CRUDEOIL"),
            simulated_spread_pct=simulated_spread_pct,
            momentum_pct=candidate.get("momentum_pct") or 0.0,
            access_token=access_token,
        )

    if max_budget is None:
        max_budget = float("inf")
    symbol = candidate["symbol"]
    spot_price = candidate["spot_price"]
    option_type = candidate.get("option_type", "CE")
    interval = candidate.get("strike_interval") or (100.0 if spot_price > 5000 else (50.0 if spot_price > 1000 else 10.0))
    lot_size = candidate.get("lot_size") or 400

    usable_budget, wallet_src = _dynamic_usable_budget(max_budget, access_token)
    if wallet_src == "live_wallet":
        print(f"  [Option Mapper Notice] Dynamic NSE wallet budget: Rs {usable_budget:,.2f} INR "
              f"(available cash x {MAX_ALLOCATION_PCT} x (1-{SLIPPAGE_BUFFER_PCT})).")

    base_strike = _snap_to_strike(spot_price, interval)
    offset_steps = _momentum_strike_offset(candidate.get("momentum_pct") or 0.0)

    nse_map = get_fyers_nse_instrument_map()
    if not nse_map or nse_map.get("_cache_version") != _CACHE_VERSION:
        _record_mapping_error("NSE", "STALE_OR_MISSING_CACHE", "Fyers NSE symbol master unavailable")
        return None

    underlying_symbol = _underlying_fyers_symbol(candidate)
    chain = _live_option_chain(access_token, underlying_symbol, option_type)

    pick = None
    if chain:
        pick = _pick_live_strike(chain, base_strike, interval, option_type, offset_steps,
                                 lot_size, usable_budget, NSE_MIN_OPTION_OPEN_INTEREST)
    if pick:
        target_strike = float(pick["strike"])
        estimated_delta = float(pick["entry"]["delta"])
        oi_value = float(pick["entry"]["oi"]) if pick["real_oi"] else None
        ask_price = _round_to_tick(pick["entry"]["ask"], 0.05)
        bid_price = _round_to_tick(pick["entry"]["bid"], 0.05)
        gates_estimated = False
        offset_used = pick["otm_offset"]
    else:
        target_strike = _apply_otm_offset(base_strike, interval, offset_steps, option_type)
        estimated_delta = 0.52  # display only; never used as a gate
        est_premium_pct = 0.008 if candidate.get("is_index") else 0.0125
        estimated_premium = round(spot_price * est_premium_pct, 2)
        ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
        bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
        oi_value = None
        gates_estimated = True
        offset_used = offset_steps

    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0

    real_info = _lookup_with_deviation_guard(nse_map, symbol, target_strike, option_type, interval, "NSE")
    if not real_info:
        return None

    # The guard may have snapped to the nearest real contract strike; report the
    # ACTUAL resolved strike, never the computed (possibly mid-step) target.
    target_strike = float(real_info.get("strike") or target_strike)

    tick_size = float(real_info.get("tick_size") or 0.05)
    ask_price = _round_to_tick(ask_price, tick_size)
    bid_price = _round_to_tick(bid_price, tick_size)
    if real_info.get("lot_size"):
        lot_size = int(real_info["lot_size"])
    total_lot_cost = round(ask_price * lot_size, 2)

    budget_approved = total_lot_cost <= usable_budget
    spread_approved = bid_ask_spread_pct <= MAX_BID_ASK_SPREAD_PCT
    delta_approved = True  # real delta enforced inside _pick_live_strike; estimate mode never fakes
    oi_approved = True
    if not gates_estimated and oi_value is not None:
        oi_approved = (not ENABLE_OI_FILTER) or (oi_value >= NSE_MIN_OPTION_OPEN_INTEREST)

    if not (budget_approved and spread_approved and delta_approved and oi_approved):
        if not budget_approved:
            _record_mapping_error("NSE", "INSUFFICIENT_WALLET_BALANCE",
                                  f"no affordable {option_type} strike within {MAX_STRIKE_OFFSET} OTM steps "
                                  f"<= Rs {usable_budget:,.2f}")
        else:
            _record_mapping_error("NSE", "REJECTED_GUARDRAILS", "spread/OI/delta guardrail failed")
        return None

    option_symbol = real_info.get("tradingsymbol", f"{symbol}_{int(target_strike)}_{option_type}")
    fyers_symbol = real_info.get("fyers_symbol", f"NSE:{option_symbol}")

    mapped_contract = {
        "underlying_symbol": symbol,
        "exchange": "NSE_FO",
        "option_symbol": option_symbol,
        "instrument_key": fyers_symbol,
        "fyers_symbol": fyers_symbol,
        "tick_size": tick_size,
        "option_type": option_type,
        "strike_price": target_strike,
        "spot_price": spot_price,
        "estimated_delta": estimated_delta,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "spread_pct": round(bid_ask_spread_pct * 100, 2),
        "lot_size": lot_size,
        "estimated_premium": ask_price,
        "total_lot_cost": total_lot_cost,
        "max_budget_limit": usable_budget,
        "usable_budget": usable_budget,
        "gates_estimated": gates_estimated,
        "strike_offset": offset_used,
        "budget_approved": budget_approved and spread_approved and delta_approved and oi_approved
    }
    if oi_value is not None:
        mapped_contract["open_interest"] = oi_value

    print("\n[Fyers Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  Fyers Symbol        : {fyers_symbol}")
    print(f"  Strike Selection    : ATM Rs {base_strike:.2f} +{offset_used} step(s) OTM -> Rs {target_strike:.2f} ({option_type})")
    print(f"  Wallet Budget       : Rs {usable_budget:,.2f} INR")
    if not gates_estimated:
        print(f"  Live Delta          : {estimated_delta:.2f} (real)")
        print(f"  Live OI             : {int(oi_value) if oi_value is not None else 'n/a'} contracts (real)")
    else:
        print(f"  Live Data           : unavailable (est. delta {estimated_delta:.2f}, OI gate skipped)")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Tick Size           : Rs {tick_size:.2f}")
    print(f"  Lot Size            : {lot_size} shares")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Usable Budget: Rs {usable_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")

    _clear_mapping_error()
    return mapped_contract
