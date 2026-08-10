import os
import sys
import math
from typing import Dict, Any, Optional

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

def get_mcx_crude_option_contract(
    spot_price: float,
    direction: str,
    budget_cap: Optional[float] = None,
    option_type: Optional[str] = None,
    symbol_hint: str = "CRUDEOIL",
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    MCX Option Contract Mapper for MCX Crude Oil Options (Standard & Mini).
    Selects At-The-Money (ATM) or Slightly ITM Call (CE) or Put (PE) option contract.
    Supports Standard CRUDEOIL (100 barrels) & Mini CRUDEOILM (10 barrels).
    Automatically falls back to Mini contract if standard lot cost exceeds wallet budget cap.
    """
    if budget_cap is None:
        budget_cap = float("inf")

    if not option_type:
        option_type = "CE" if direction.upper() == "BULLISH" else "PE"

    strike_step = MCX_CRUDE_STRIKE_STEP # 50.0 points

    # Calculate ATM Strike (rounded to nearest 50 points)
    atm_strike = round(spot_price / strike_step) * strike_step
    estimated_delta = 0.52 # ATM Delta target range (0.50 to 0.55)

    # MCX Crude Oil Option Premium estimate (~1.5% of spot price)
    estimated_premium = round(spot_price * 0.015, 2)

    bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
    ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0

    # Determine lot size: Standard (100 barrels) vs Mini (10 barrels)
    std_lot_cost = round(ask_price * MCX_CRUDE_LOT_SIZE, 2)
    
    if symbol_hint == "CRUDEOILM" or std_lot_cost > budget_cap:
        underlying_symbol = "CRUDEOILM"
        lot_size = MCX_CRUDE_MINI_LOT_SIZE
        print(f"  [Option Mapper Notice] Using Mini Crude Contract ({underlying_symbol}, Lot Size: {lot_size} barrels) for budget cap Rs {budget_cap:,.2f} INR.")
    else:
        underlying_symbol = MCX_CRUDE_SYMBOL
        lot_size = MCX_CRUDE_LOT_SIZE

    total_lot_cost = round(ask_price * lot_size, 2)

    option_symbol = f"{underlying_symbol}_{int(atm_strike)}_{option_type}"
    instrument_key = f"MCX_FO|{underlying_symbol}_{int(atm_strike)}_{option_type}"

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

    print("\n[MCX Crude Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
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
    Option Selection Guardrails for NSE Equity & MCX Commodity Candidates.
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
    option_type = candidate["option_type"] # 'CE' or 'PE'
    interval = candidate["strike_interval"]
    lot_size = candidate["lot_size"]
    
    # Calculate At-The-Money (ATM) Strike (Delta ~ 0.50 - 0.55)
    atm_strike = round(spot_price / interval) * interval
    estimated_delta = 0.52 # ATM Delta target range: 0.50 to 0.55
    
    # Estimated ATM Option Premium (~1.25% of spot price for equities, ~0.8% for indices)
    if candidate.get("is_index"):
        est_premium_pct = 0.008
    else:
        est_premium_pct = 0.0125
        
    estimated_premium = round(spot_price * est_premium_pct, 2)
    
    # Bid-Ask Spread Verification
    bid_price = round(estimated_premium * (1.0 - (simulated_spread_pct / 2.0)), 2)
    ask_price = round(estimated_premium * (1.0 + (simulated_spread_pct / 2.0)), 2)
    bid_ask_spread_pct = (ask_price - bid_price) / ask_price if ask_price > 0 else 0.0
    
    total_lot_cost = round(ask_price * lot_size, 2)
    
    option_symbol = f"{symbol}_{int(atm_strike)}_{option_type}"
    instrument_key = f"NSE_FO|{symbol}_{int(atm_strike)}_{option_type}"
    
    budget_approved = total_lot_cost <= max_budget
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

    print("\n[Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Open Interest (OI)  : {open_interest:,} contracts")
    print(f"  Lot Size            : {lot_size} shares")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {max_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED (Wide Spread)'}")
    print(f"  Liquidity OI Check  : {'APPROVED (>= 100k)' if oi_approved else 'REJECTED (Low OI)'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")
    
    if not spread_approved or not oi_approved or not budget_approved:
        return None
        
    return mapped_contract

if __name__ == "__main__":
    mcx_cand = {
        "symbol": "CRUDEOIL",
        "spot_price": 6250.00,
        "direction": "BULLISH",
        "option_type": "CE",
        "is_mcx": True
    }
    res = resolve_atm_option_contract(mcx_cand, max_budget=1000.0)
    print("MCX Resolved:", res)
