"""
UPSTOX API PURGED & REPLACED WITH FYERS API V3
===================================================
Upstox integration has been completely removed upon request.
This file provides clean compatibility aliases pointing to Fyers API v3.
"""
from execution.fyers_trader import (
    FyersTrader as UpstoxTrader,
    get_live_wallet_balance,
    auto_generate_fyers_token as auto_generate_upstox_token,
    get_active_fyers_token as get_active_upstox_token,
    verify_and_fetch_live_fyers_balance as verify_and_fetch_live_upstox_balance,
    handle_execution_issue_and_halt,
    place_aggressive_limit_order
)
