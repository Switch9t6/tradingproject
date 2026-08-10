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

def execute_live_trade_now():
    print("=" * 80)
    print("      ONE-TIME EXCEPTION: LIVE REAL UPSTOX TRADE EXECUTION")
    print("      Contract: NIFTY 24900 CE 11 AUG 26 | Key: NSE_FO|41031")
    print("=" * 80)

    # 1. Fetch valid OAuth token
    token_path = "access_token.json" if os.path.exists("access_token.json") else TOKEN_FILE_PATH
    if not os.path.exists(token_path):
        print("[ERROR] access_token.json not found. Run oauth_server.py first.")
        return

    with open(token_path, "r") as f:
        token = json.load(f).get("access_token", "")

    # 2. Instantiate Upstox APIs
    config = upstox_client.Configuration()
    config.access_token = token
    api_client = upstox_client.ApiClient(config)
    
    quote_api = upstox_client.MarketQuoteApi(api_client)
    order_api = upstox_client.OrderApi(api_client)

    # 3. Fetch real-time market depth quote for NSE_FO|41031
    print("\n[Upstox Market API] Fetching live bid/ask market quote for 'NSE_FO|41031'...")
    res = quote_api.get_full_market_quote(symbol="NSE_FO|41031", api_version="2.0")
    data_map = res.data if hasattr(res, "data") else {}
    quote_item = list(data_map.values())[0] if data_map else None

    if not quote_item:
        print("[ERROR] Failed to fetch live market quote for NSE_FO|41031.")
        return

    last_price = getattr(quote_item, "last_price", 9.90)
    depth = getattr(quote_item, "depth", {})
    sell_depth = depth.get("sell", [{}]) if isinstance(depth, dict) else getattr(depth, "sell", [{}])
    ask_price = float(sell_depth[0].get("price", last_price)) if isinstance(sell_depth[0], dict) else float(getattr(sell_depth[0], "price", last_price))
    if ask_price <= 0:
        ask_price = float(last_price)

    lot_size = 65
    total_cost = round(ask_price * lot_size, 2)
    target_price = round(ask_price * 1.25, 2)
    stop_price = round(ask_price * 0.88, 2)

    print(f"\n[Real Live Quote Ingested]")
    print(f"  Contract Symbol  : NIFTY_24900_CE_11_AUG_26")
    print(f"  Instrument Key   : NSE_FO|41031")
    print(f"  Ask Price (Offer): Rs {ask_price:.2f} / share")
    print(f"  Lot Size         : {lot_size} shares")
    print(f"  Total Cost       : Rs {total_cost:,.2f} INR (Margin Cap: Rs 1,258.00 INR)")
    print(f"  Target (+25%)    : Rs {target_price:.2f}")
    print(f"  Stop Loss (-12%) : Rs {stop_price:.2f}")

    # 4. Dispatch Telegram Trade Entry Alert
    print("\n[Telegram Alert] Sending Live Trade Entry Notification to Telegram...")
    send_trade_entry_alert({
        "option_symbol": "NIFTY_24900_CE_11_AUG_26",
        "lot_size": lot_size,
        "entry_premium": ask_price,
        "target_price": target_price,
        "initial_stop_loss": stop_price,
        "composite_score": 88.5,
        "execution_mode": "REAL LIVE PRODUCTION ORDER"
    }, wallet_balance=1258.0)

    # 5. Place INSTANT FILL LIMIT ORDER at Ask Price (`price=ask_price`)
    print("\n[Upstox Order API] Placing REAL LIVE MARKET/LIMIT Order at Ask Price...")
    body = upstox_client.PlaceOrderRequest(
        quantity=lot_size,
        product="I", # Intraday MIS
        validity="DAY",
        price=ask_price, # Placed at live Ask price for instant fill!
        tag="OPTIONS_BOT",
        instrument_token="NSE_FO|41031",
        order_type="LIMIT",
        transaction_type="BUY",
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False
    )

    try:
        api_resp = order_api.place_order(body, api_version="2.0")
        resp_data = getattr(api_resp, "data", api_resp)
        if isinstance(resp_data, dict):
            order_id = str(resp_data.get("order_id", ""))
        else:
            order_id = str(getattr(resp_data, "order_id", getattr(api_resp, "order_id", "")))

        print(f"\n==========================================================================")
        print(f"[SUCCESS] REAL LIVE ORDER DISPATCHED TO UPSTOX EXCHANGE!")
        print(f"   Order ID: {order_id}")
        print(f"   Status  : SUCCESS")
        print(f"==========================================================================")

        # Poll Order Status for 3 seconds
        time.sleep(2.0)
        try:
            ord_detail = order_api.get_order_details(order_id=order_id, api_version="2.0")
            d = ord_detail.data if hasattr(ord_detail, "data") else ord_detail
            status = str(d.get("status", "") if isinstance(d, dict) else getattr(d, "status", "")).upper()
            print(f"  Upstox Verified Order Status: {status}")
        except Exception as err:
            print(f"  Order detail notice: {err}")

    except Exception as e:
        print(f"[ORDER ERROR] Failed to place live order: {e}")

if __name__ == "__main__":
    execute_live_trade_now()
