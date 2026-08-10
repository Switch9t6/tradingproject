import os
import sys
import json
import time
import datetime
import upstox_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import TOKEN_FILE_PATH, MICRO_CAPITAL_BUDGET_CAP
from execution.upstox_trader import UpstoxOptionsTrader
from reporting.telegram_bot import send_trade_entry_alert, send_trade_exit_alert
from execution.state_manager import StateManager

def test_micro_capital_trade():
    print("=" * 80)
    print("      UPSTOX REAL LIVE ORDER EXECUTION TEST WITH NEAR WEEKLY ACTIVE OPTION")
    print("=" * 80)

    # 1. Fetch valid OAuth token
    token_path = "access_token.json" if os.path.exists("access_token.json") else TOKEN_FILE_PATH
    if not os.path.exists(token_path):
        print("[ERROR] access_token.json not found. Run oauth_server.py first.")
        return

    with open(token_path, "r") as f:
        token = json.load(f).get("access_token", "")

    # 2. Instantiate trader
    trader = UpstoxOptionsTrader(access_token=token, dry_run=False)
    wallet = trader.get_read_only_wallet_balance()
    print(f"\n[Live Upstox Balance] Available Cash Margin: Rs {wallet:,.2f} INR")

    # 3. Query Upstox Options API for NEAREST WEEKLY active contract
    config = upstox_client.Configuration()
    config.access_token = token
    opt_api = upstox_client.OptionsApi(upstox_client.ApiClient(config))
    
    print("\n[Upstox API] Querying nearest weekly option contracts for NIFTY...")
    contracts_res = opt_api.get_option_contracts(instrument_key="NSE_INDEX|Nifty 50")
    
    today = datetime.datetime.now()
    valid_contracts = [
        c for c in (contracts_res.data if hasattr(contracts_res, "data") else [])
        if getattr(c, "instrument_type", "") == "CE" or (isinstance(c, dict) and c.get("instrument_type") == "CE")
    ]
    
    def get_attr(obj, attr, default=None):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    # Sort contracts by nearest expiry date
    valid_contracts = [c for c in valid_contracts if get_attr(c, "expiry") and get_attr(c, "expiry") > today]
    valid_contracts.sort(key=lambda x: (get_attr(x, "expiry"), abs(get_attr(x, "strike_price", 0.0) - 24500.0)))

    selected = valid_contracts[0] if valid_contracts else None
    if not selected:
        print("[Error] No active weekly contracts found.")
        return

    real_instrument_key = get_attr(selected, "instrument_key")
    trading_symbol = get_attr(selected, "trading_symbol")
    lot_size = get_attr(selected, "lot_size", 25)
    strike_price = get_attr(selected, "strike_price")
    expiry = get_attr(selected, "expiry")

    print("\n[Nearest Weekly Active Option Contract Selected]")
    print(f"  Trading Symbol   : {trading_symbol}")
    print(f"  Real Upstox Key  : {real_instrument_key}")
    print(f"  Expiry Date      : {expiry.strftime('%Y-%m-%d') if hasattr(expiry, 'strftime') else expiry}")
    print(f"  Lot Size         : {lot_size} shares")
    print(f"  Strike Price     : Rs {strike_price}")

    # Estimate test ask premium (micro-capital ~ Rs 8.50)
    ask_p = 8.50
    total_cost = round(ask_p * lot_size, 2)

    test_contract = {
        "underlying_symbol": "NIFTY",
        "option_symbol": trading_symbol.replace(" ", "_"),
        "instrument_key": real_instrument_key,
        "option_type": "CE",
        "strike_price": strike_price,
        "spot_price": 24350.0,
        "estimated_delta": 0.52,
        "bid_price": 8.45,
        "ask_price": ask_p,
        "spread_pct": 0.58,
        "lot_size": lot_size,
        "estimated_premium": ask_p,
        "total_lot_cost": total_cost,
        "max_budget_limit": 1258.0,
        "budget_approved": True,
        "open_interest": 250000,
        "composite_rating": {"composite_score": 88.5, "tech_score": 50, "news_score": 38.5}
    }

    # 4. Dispatch Telegram Trade Entry Alert
    print("\n[Telegram Alert] Sending Live Trade Entry Notification to Telegram...")
    send_trade_entry_alert({
        "option_symbol": test_contract["option_symbol"],
        "lot_size": test_contract["lot_size"],
        "entry_premium": test_contract["ask_price"],
        "target_price": round(test_contract["ask_price"] * 1.25, 2),
        "initial_stop_loss": round(test_contract["ask_price"] * 0.88, 2),
        "composite_score": 88.5,
        "execution_mode": "MICRO-CAPITAL LIVE TEST"
    }, wallet_balance=wallet)

    # 5. Execute Order on Upstox API v2
    print("\n[Upstox API] Executing Real Order on Upstox API v2...")
    res = trader.execute_option_trade(
        test_contract,
        sim_scenario="TARGET_HIT",
        override_daily_limit=True,
        auto_approve=True
    )

    print(f"\n[Trade Execution Result] {res}")

if __name__ == "__main__":
    test_micro_capital_trade()
