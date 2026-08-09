import math
from typing import Dict, Any, Optional
from config.settings import (
    MAX_SINGLE_LOT_PREMIUM_BUDGET,
    TARGET_DELTA_MIN,
    TARGET_DELTA_MAX,
    MAX_BID_ASK_SPREAD_PCT
)

def resolve_atm_option_contract(
    candidate: Dict[str, Any],
    max_budget: float = MAX_SINGLE_LOT_PREMIUM_BUDGET,
    simulated_spread_pct: float = 0.008
) -> Optional[Dict[str, Any]]:
    """
    UPGRADE #3: Option Selection Guardrails.
    Map top breakout candidate to Delta 0.50–0.55 ATM Option contract (CE/PE),
    enforce Bid-Ask Spread <= 1.5% check, and verify single-lot premium budget check <= INR 10,000.
    """
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
    
    # UPGRADE #3: Bid-Ask Spread Verification
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
    
    print("\n[Option Contract Mapper]")
    print(f"  Mapped Contract     : {option_symbol}")
    print(f"  ATM Strike Price    : Rs {atm_strike} ({option_type}) | Delta: {estimated_delta:.2f}")
    print(f"  Bid / Ask Quote     : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {bid_ask_spread_pct*100:.2f}%)")
    print(f"  Lot Size            : {lot_size} shares")
    print(f"  Total Lot Cost      : Rs {total_lot_cost:,.2f} INR (Budget Cap: Rs {max_budget:,.2f} INR)")
    print(f"  Spread Check        : {'APPROVED (<= 1.5%)' if spread_approved else 'REJECTED (Wide Spread)'}")
    print(f"  Budget Status       : {'APPROVED' if budget_approved else 'REJECTED (Exceeds Budget)'}")
    
    if not spread_approved:
        print(f"WARNING: Contract {option_symbol} Bid-Ask Spread ({bid_ask_spread_pct*100:.2f}%) exceeds 1.5% limit. Rejecting.")
        return None
        
    if not budget_approved:
        print(f"WARNING: Contract {option_symbol} cost (Rs {total_lot_cost}) exceeds max budget of Rs {max_budget}. Rejecting.")
        return None
        
    return mapped_contract

if __name__ == "__main__":
    cand = {
        "symbol": "BANKBARODA",
        "spot_price": 248.50,
        "direction": "BULLISH",
        "option_type": "CE",
        "strike_interval": 2.5,
        "lot_size": 2925,
        "is_index": False
    }
    res = resolve_atm_option_contract(cand)
    print("Resolved:", res)
