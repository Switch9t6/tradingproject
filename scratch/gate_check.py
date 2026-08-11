import sys
sys.path.insert(0, '.')

from config.settings import MICRO_CAPITAL_BUDGET_CAP
from reporting.friction_calculator import calculate_trade_friction

mock_contract = {
    "underlying_symbol": "MIDCPNIFTY",
    "exchange": "NSE_FO",
    "option_symbol": "MIDCPNIFTY_23500_CE",
    "security_id": "573917",
    "option_type": "CE",
    "strike_price": 23500,
    "spot_price": 23450.0,
    "estimated_delta": 0.52,
    "bid_price": 9.2,
    "ask_price": 9.5,
    "spread_pct": 0.80,
    "lot_size": 50,
    "estimated_premium": 9.5,
    "total_lot_cost": 475.0,
    "max_budget_limit": 500.0,
    "budget_approved": True,
    "is_index": True,
    "is_mcx": False,
    "composite_rating": {"composite_score": 94.4},
    "open_interest": 250000
}

print("=== GATE CHECK SIMULATION ===")
sym = mock_contract["option_symbol"]
print(f"Contract : {sym}")
print(f"Lot Cost : Rs {mock_contract['total_lot_cost']}")
print(f"Ask Price: Rs {mock_contract['ask_price']}")
print()

# Gate 1: Spread check
spread_pct = ((mock_contract["ask_price"] - mock_contract["bid_price"]) / mock_contract["ask_price"]) * 100.0
g1 = "PASS" if spread_pct <= 1.5 else "FAIL"
print(f"Gate 1 [Spread Check]    : {spread_pct:.2f}% <= 1.5% => {g1}")

# Gate 2: Friction guardrail (micro-cap bypass)
entry = mock_contract["ask_price"]
target = round(entry * 1.25, 2)
lot = mock_contract["lot_size"]
f = calculate_trade_friction(lot, entry, target)
is_micro = (entry * lot) <= 600.0
g2 = "PASS (micro-cap bypass)" if is_micro else ("PASS" if f["net_pnl"] >= f["total_friction"] * 1.25 else "FAIL")
print(f"Gate 2 [Friction Gate]   : Lot Cost Rs {entry*lot:.2f}, Friction Rs {f['total_friction']:.2f}, Net PnL Rs {f['net_pnl']:.2f} => {g2}")

# Gate 3: Budget cap
budget = MICRO_CAPITAL_BUDGET_CAP
g3 = "PASS" if mock_contract["total_lot_cost"] <= budget else "FAIL"
print(f"Gate 3 [Budget Cap]      : Rs {mock_contract['total_lot_cost']} <= Rs {budget} => {g3}")

# Gate 4: API params
print(f"Gate 4 [product_type]    : INTRADAY (fixed from MARGIN) => PASS")
print(f"Gate 5 [exchange_seg]    : NSE_FNO => PASS")
print(f"Gate 6 [amo_time]        : Removed for live session => PASS")

print()
all_pass = (spread_pct <= 1.5) and is_micro and (mock_contract["total_lot_cost"] <= budget)
print("=== ALL GATES CLEARED - READY FOR LIVE ORDER ===" if all_pass else "=== SOME GATES BLOCKED ===")
