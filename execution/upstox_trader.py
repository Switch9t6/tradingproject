"""
Upstox API v2 Live Execution Gateway
======================================
Professional Upstox trading gateway supplying live user wallet balance queries,
bid-ask spread validation, aggressive limit order execution, and 5-second order fill verification.
"""

import os
import sys
import json
import time
import datetime
from typing import Dict, Any, Optional

import upstox_client
from upstox_client.rest import ApiException

from config.settings import (
    UPSTOX_ACCESS_TOKEN,
    TOKEN_FILE_PATH,
    INITIAL_WALLET_CAPITAL,
    MAX_BID_ASK_SPREAD_PCT,
    LIMIT_ORDER_TIMEOUT_SECONDS
)
from execution.state_manager import StateManager


def auto_generate_upstox_token() -> str:
    """
    Programmatically logs in to Upstox using TOTP via upstox-totp, generates a new 24-hour Access Token,
    and updates active system environment variables, .env, and access_token.json without manual browser intervention.
    """
    try:
        from upstox_totp import UpstoxTOTP
        print("🔐 [Upstox Auth] Initiating Headless Auto-Login Sequence...")
        upx = UpstoxTOTP()
        response = upx.app_token.get_access_token()
        
        if response.success and response.data and response.data.access_token:
            access_token = response.data.access_token
            os.environ["UPSTOX_ACCESS_TOKEN"] = access_token
            
            # Save to .env
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    new_lines = []
                    found = False
                    for l in lines:
                        if l.startswith("UPSTOX_ACCESS_TOKEN="):
                            new_lines.append(f'UPSTOX_ACCESS_TOKEN="{access_token}"\n')
                            found = True
                        else:
                            new_lines.append(l)
                    if not found:
                        new_lines.append(f'UPSTOX_ACCESS_TOKEN="{access_token}"\n')
                    with open(env_path, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
                except Exception as env_err:
                    print(f"⚠️ [Upstox Auth Notice] Could not update .env: {env_err}")

            # Save to logs/access_token.json
            user_name = getattr(response.data, "user_name", "")
            user_id = getattr(response.data, "user_id", "")
            token_payload = {
                "access_token": access_token,
                "user_name": user_name,
                "user_id": user_id,
                "updated_at": datetime.datetime.now().isoformat(),
                "saved_timestamp": time.time(),
                "expiry_prompt_sent": False
            }
            os.makedirs(os.path.dirname(TOKEN_FILE_PATH), exist_ok=True)
            with open(TOKEN_FILE_PATH, "w") as f:
                json.dump(token_payload, f, indent=4)
                
            # Update state.json
            from execution.state_manager import StateManager
            sm = StateManager()
            sm.state["token_saved_at"] = time.time()
            sm.state["expiry_prompt_sent"] = False
            sm._save_state(sm.state)
            
            print(f"✅ [Upstox Auth] Auto-Login Successful! Fresh Access Token acquired for {user_name} ({user_id}).")
            return access_token
        else:
            print(f"❌ [Upstox Auth Error] Failed to generate token: {response}")
    except Exception as err:
        print(f"💥 [Upstox Auth Exception] Error during automated login: {err}")
        
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()


def get_active_upstox_token() -> str:
    """Helper to retrieve active Upstox access token from memory, env, access_token.json or auto-login."""
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip() or UPSTOX_ACCESS_TOKEN.strip()
    if (not token or token.startswith("MOCK") or token.startswith("your_")) and os.path.exists(TOKEN_FILE_PATH):
        try:
            with open(TOKEN_FILE_PATH, "r") as f:
                tdata = json.load(f)
                token = tdata.get("access_token", token)
        except Exception:
            pass
    if not token or token.startswith("MOCK") or token.startswith("your_"):
        token = auto_generate_upstox_token()
    return token.strip()


def get_upstox_api_client(access_token: Optional[str] = None) -> upstox_client.ApiClient:
    """Configures and returns an Upstox ApiClient instance."""
    tok = access_token or get_active_upstox_token()
    configuration = upstox_client.Configuration()
    configuration.access_token = tok
    return upstox_client.ApiClient(configuration)


def get_live_wallet_balance(access_token: Optional[str] = None) -> float:
    """
    Queries Upstox User API (get_user_fund_and_margin) and returns available cash balance.
    Falls back gracefully if token is absent or API call fails.
    """
    tok = access_token or get_active_upstox_token()
    if not tok or tok.startswith("MOCK") or tok.startswith("your_"):
        print("[Upstox Wallet Inspector] No valid Upstox token configured. Returning default wallet base.")
        return INITIAL_WALLET_CAPITAL

    try:
        api_client = get_upstox_api_client(tok)
        user_api = upstox_client.UserApi(api_client)
        api_response = user_api.get_user_fund_margin(api_version="2.0")
        
        data = getattr(api_response, "data", api_response)
        if isinstance(data, dict):
            sec_data = data.get("SEC", {}) or data.get("equity", {})
            avail = float(sec_data.get("available_margin", 0.0) or sec_data.get("cash", 0.0) or 0.0)
        else:
            sec_data = getattr(data, "sec", None) or getattr(data, "equity", None)
            avail = float(getattr(sec_data, "available_margin", 0.0) if sec_data else 0.0)

        if avail > 0:
            print(f"[Upstox Wallet Inspector] Live Available Cash Balance: Rs {avail:,.2f} INR.")
            return avail
    except ApiException as e:
        print(f"[Upstox Wallet Warning] Upstox User API Exception: {e}")
    except Exception as ex:
        print(f"[Upstox Wallet Warning] Failed to fetch live wallet balance: {ex}")

    return INITIAL_WALLET_CAPITAL


def place_aggressive_limit_order(
    instrument_key: str,
    quantity: int,
    transaction_type: str = "BUY",
    product: str = "I",
    bid_price: float = 0.0,
    ask_price: float = 0.0,
    dry_run: bool = False,
    access_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes an aggressive limit order on Upstox API v2:
    1. Validates Bid-Ask Spread sanity check (Spread % <= 1.5%).
    2. Calculates aggressive limit price = bid_price + 0.25 * (ask_price - bid_price).
    3. Dispatches order via OrderApi().place_order() with product="I" (Intraday) and order_type="LIMIT".
    4. Runs 5-second fill verification loop polling OrderApi().get_order_details().
       If unfilled after 5 seconds, issues cancel_order() and returns status='CANCELLED_TIMEOUT'.
    """
    # 1. Bid-Ask Spread Sanity Check
    if ask_price > 0 and bid_price > 0:
        spread_pct = (ask_price - bid_price) / ask_price
        if spread_pct > MAX_BID_ASK_SPREAD_PCT:
            print(f"[Upstox Order Rejected] Spread {spread_pct*100:.2f}% exceeds maximum limit of {MAX_BID_ASK_SPREAD_PCT*100:.1f}%.")
            return {
                "status": "REJECTED_SPREAD",
                "order_id": None,
                "remarks": f"Bid-Ask Spread ({spread_pct*100:.2f}%) exceeds safety threshold."
            }
        aggressive_price = round(bid_price + 0.25 * (ask_price - bid_price), 2)
    elif ask_price > 0:
        aggressive_price = round(ask_price, 2)
    else:
        aggressive_price = round(bid_price, 2) if bid_price > 0 else 0.0

    print(f"\n===========================================================================")
    print(f"  EXECUTING AGGRESSIVE LIMIT ORDER (UPSTOX API V2)")
    print(f"  Instrument Key       : {instrument_key}")
    print(f"  Transaction Type     : {transaction_type}")
    print(f"  Quantity             : {quantity} shares/contracts")
    print(f"  Bid / Ask Quote      : Rs {bid_price:.2f} / Rs {ask_price:.2f}")
    print(f"  Aggressive Limit     : Rs {aggressive_price:.2f}")
    print(f"  Execution Mode       : {'DRY_RUN (SIMULATION)' if dry_run else 'LIVE PRODUCTION'}")
    print(f"===========================================================================")

    if dry_run:
        sim_order_id = f"DRY_UPSTOX_{int(time.time())}"
        print(f"[UPSTOX DRY_RUN] Simulated order placed. Order ID: {sim_order_id}")
        return {
            "status": "TRADED",
            "order_id": sim_order_id,
            "filled_price": aggressive_price,
            "quantity": quantity,
            "instrument_key": instrument_key,
            "remarks": "Simulated Dry Run Fill"
        }

    tok = access_token or get_active_upstox_token()
    if not tok or tok.startswith("MOCK") or tok.startswith("your_"):
        return {
            "status": "REJECTED_NO_TOKEN",
            "order_id": None,
            "remarks": "No valid Upstox Access Token provided."
        }

    api_client = get_upstox_api_client(tok)
    order_api = upstox_client.OrderApi(api_client)

    body = upstox_client.PlaceOrderRequest(
        quantity=quantity,
        product=product,
        validity="DAY",
        price=aggressive_price,
        tag="quant_engine",
        instrument_token=instrument_key,
        order_type="LIMIT",
        transaction_type=transaction_type.upper(),
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False
    )

    try:
        print(f"[UPSTOX LIVE ORDER] Dispatching LIMIT order: {instrument_key} | Qty: {quantity} | Price: Rs {aggressive_price:.2f}")
        response = order_api.place_order(body, api_version="2.0")
        
        data = getattr(response, "data", response)
        order_id = getattr(data, "order_id", None) if data else None
        if isinstance(data, dict):
            order_id = data.get("order_id", order_id)

        if not order_id:
            print(f"[UPSTOX ORDER ERROR] Failed to extract order_id from response: {response}")
            return {"status": "REJECTED", "order_id": None, "remarks": str(response)}

        print(f"[UPSTOX LIVE ORDER DISPATCHED] Order ID: {order_id}. Starting 5-second fill verification loop...")

        # 4. 5-Second Fill Verification Loop
        start_time = time.time()
        filled = False
        final_status = "PENDING"
        filled_price = aggressive_price

        while time.time() - start_time < LIMIT_ORDER_TIMEOUT_SECONDS:
            time.sleep(1.0)
            try:
                details_resp = order_api.get_order_details(order_id=order_id, api_version="2.0")
                d_data = getattr(details_resp, "data", details_resp)
                if isinstance(d_data, dict):
                    status = str(d_data.get("status", "")).upper()
                    avg_p = float(d_data.get("average_price", 0.0) or 0.0)
                else:
                    status = str(getattr(d_data, "status", "")).upper()
                    avg_p = float(getattr(d_data, "average_price", 0.0) or 0.0)

                print(f"  [Fill Verification] Order #{order_id} Status: {status}")
                if status in ["complete", "traded", "filled", "success"]:
                    filled = True
                    final_status = "TRADED"
                    filled_price = avg_p if avg_p > 0 else aggressive_price
                    break
                elif status in ["rejected", "cancelled"]:
                    final_status = status
                    break
            except Exception as poll_err:
                print(f"  [Fill Verification Warning] Could not check status: {poll_err}")

        if filled:
            print(f"[UPSTOX ORDER FILLED] Order #{order_id} filled at Rs {filled_price:.2f}.")
            return {
                "status": "TRADED",
                "order_id": order_id,
                "filled_price": filled_price,
                "quantity": quantity,
                "instrument_key": instrument_key,
                "remarks": "Order Filled Successfully"
            }
        else:
            # Cancel unfilled limit order after 5 seconds timeout
            print(f"[UPSTOX TIMEOUT] Order #{order_id} unfilled after {LIMIT_ORDER_TIMEOUT_SECONDS}s. Cancelling order...")
            try:
                order_api.cancel_order(order_id=order_id, api_version="2.0")
                print(f"[UPSTOX ORDER CANCELLED] Order #{order_id} cancelled.")
            except Exception as cancel_err:
                print(f"[UPSTOX CANCEL ERROR] Failed to cancel order #{order_id}: {cancel_err}")

            return {
                "status": "CANCELLED_TIMEOUT",
                "order_id": order_id,
                "remarks": f"Order unfilled after {LIMIT_ORDER_TIMEOUT_SECONDS}s timeout and was cancelled."
            }

    except ApiException as ae:
        print(f"[UPSTOX API EXCEPTION] {ae}")
        return {"status": "REJECTED_API_ERROR", "order_id": None, "remarks": str(ae)}
    except Exception as ex:
        print(f"[UPSTOX EXECUTION ERROR] {ex}")
        return {"status": "REJECTED_EXCEPTION", "order_id": None, "remarks": str(ex)}


class UpstoxTrader:
    """
    Main Upstox Execution Gateway Class orchestrating live trading,
    wallet balance inspection, and persistent trade logging.
    """
    def __init__(self, dry_run: bool = False, force_reset: bool = False):
        self.dry_run = dry_run
        self.access_token = get_active_upstox_token()
        self.state_mgr = StateManager(force_reset=force_reset)
        self.api_client = get_upstox_api_client(self.access_token)
        
        mode_str = "DRY_RUN (SIMULATION)" if self.dry_run else "LIVE PRODUCTION"
        print(f"[Upstox Gateway] Upstox API client initialized. Mode: {mode_str}")

    def get_read_only_wallet_balance(self) -> float:
        """Queries live available cash balance from Upstox User API."""
        return get_live_wallet_balance(self.access_token)

    def execute_option_trade(
        self,
        option_contract: Dict[str, Any],
        max_budget: float,
        session_name: str = "NSE Equity Morning Session"
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a single-lot option trade on Upstox API v2.
        """
        instrument_key = option_contract["instrument_key"]
        lot_size = int(option_contract["lot_size"])
        ask_price = float(option_contract["ask_price"])
        bid_price = float(option_contract.get("bid_price", ask_price * 0.99))
        exchange = "MCX_FO" if "MCX" in instrument_key else "NSE_FO"

        order_res = place_aggressive_limit_order(
            instrument_key=instrument_key,
            quantity=lot_size,
            transaction_type="BUY",
            product="I",
            bid_price=bid_price,
            ask_price=ask_price,
            dry_run=self.dry_run,
            access_token=self.access_token
        )

        if order_res.get("status") == "TRADED":
            filled_price = float(order_res.get("filled_price", ask_price))
            target_p = round(filled_price * 1.25, 2)
            stop_p = round(filled_price * 0.88, 2)
            
            trade_id = self.state_mgr.record_entry_trade(
                option_contract=option_contract,
                entry_premium=filled_price,
                target_p=target_p,
                stop_p=stop_p,
                execution_mode="DRY_RUN" if self.dry_run else "LIVE",
                exchange=exchange
            )

            result = {
                "trade_id": trade_id,
                "order_id": order_res.get("order_id"),
                "instrument_key": instrument_key,
                "option_symbol": option_contract["option_symbol"],
                "entry_premium": filled_price,
                "quantity": lot_size,
                "target_price": target_p,
                "stop_price": stop_p,
                "status": "OPEN",
                "exchange": exchange
            }
            return result
        else:
            print(f"[Upstox Execution Aborted] Order status: {order_res.get('status')}. Remarks: {order_res.get('remarks')}")
            return None
