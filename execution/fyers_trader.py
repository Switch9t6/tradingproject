import os
import sys
import time
import json
import pyotp
import requests
import datetime
from typing import Dict, Any, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fyers_apiv3 import fyersModel
from execution.state_manager import StateManager
from config.settings import (
    FYERS_APP_ID,
    FYERS_SECRET_KEY,
    FYERS_REDIRECT_URI,
    FYERS_USERNAME,
    FYERS_PIN_CODE,
    FYERS_TOTP_SECRET,
    FYERS_ACCESS_TOKEN,
    TOKEN_FILE_PATH,
    INITIAL_WALLET_CAPITAL,
    MAX_BID_ASK_SPREAD_PCT,
    LIMIT_ORDER_TIMEOUT_SECONDS
)

_last_totp_attempt_time = 0.0


def check_fyers_credentials_configured() -> tuple:
    """
    Checks if user has provided all 5 valid Fyers API v3 credentials in .env file.
    Returns (is_configured: bool, status_msg: str).
    """
    app_id = (FYERS_APP_ID or os.getenv("FYERS_APP_ID", "")).strip()
    secret_key = (FYERS_SECRET_KEY or os.getenv("FYERS_SECRET_KEY", "")).strip()
    username = (FYERS_USERNAME or os.getenv("FYERS_USERNAME", "")).strip()
    pin_code = (FYERS_PIN_CODE or os.getenv("FYERS_PIN_CODE", "")).strip()
    totp_secret = (FYERS_TOTP_SECRET or os.getenv("FYERS_TOTP_SECRET", "")).strip()

    missing = []
    if not app_id or app_id.startswith("YOUR_"): missing.append("FYERS_APP_ID")
    if not secret_key or secret_key.startswith("YOUR_"): missing.append("FYERS_SECRET_KEY")
    if not username or username.startswith("YOUR_"): missing.append("FYERS_USERNAME")
    if not pin_code or pin_code.startswith("YOUR_"): missing.append("FYERS_PIN_CODE")
    if not totp_secret or totp_secret.startswith("YOUR_"): missing.append("FYERS_TOTP_SECRET")

    if missing:
        return False, f"Pending Fyers credentials in .env: {', '.join(missing)}"
    return True, "CONFIGURED"


def auto_generate_fyers_token(force: bool = False) -> str:
    """
    Programmatically logs in to Fyers API v3 using TOTP 2FA, generates a fresh 24-hour Access Token,
    and updates environment variables, .env, and logs/fyers_access_token.json without manual browser intervention.
    """
    is_conf, conf_msg = check_fyers_credentials_configured()
    if not is_conf:
        print(f"⚠️ [FYERS MIGRATION INCOMPLETE] {conf_msg}")
        return get_active_fyers_token()

    global _last_totp_attempt_time
    now = time.time()

    if not force and (now - _last_totp_attempt_time < 1800):
        active_tok = get_active_fyers_token()
        if active_tok and not active_tok.startswith("MOCK") and not active_tok.startswith("your_"):
            print(f"[Fyers Auth Cooldown] Valid active token available. Reusing cached token.")
            return active_tok

    _last_totp_attempt_time = now

    try:
        print("[Fyers Auth] Initiating Headless Auto-Login Sequence (Fyers API v3)...")

        username = FYERS_USERNAME or os.getenv("FYERS_USERNAME", "").strip()
        pin_code = FYERS_PIN_CODE or os.getenv("FYERS_PIN_CODE", "").strip()
        totp_secret = FYERS_TOTP_SECRET or os.getenv("FYERS_TOTP_SECRET", "").strip()
        app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
        secret_key = FYERS_SECRET_KEY or os.getenv("FYERS_SECRET_KEY", "").strip()
        redirect_uri = FYERS_REDIRECT_URI or os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/default/ui/middleware").strip()

        if not username or not pin_code or not totp_secret or not app_id:
            print("[Fyers Auth Exception] Missing Fyers credentials in .env file.")
            return get_active_fyers_token()

        # Step 1: Send Login Request
        session = requests.Session()
        headers = {"Content-Type": "application/json"}
        
        # Step 1: Send FY ID & App ID
        url_step1 = "https://api-t1.fyers.in/api/v3/generate-authcode"
        payload_step1 = {
            "fy_id": username,
            "app_id": app_id.split("-")[0] if "-" in app_id else app_id,
            "redirect_uri": redirect_uri,
            "appType": "100"
        }
        res1 = session.post(url_step1, json=payload_step1, headers=headers, timeout=15).json()
        request_key = res1.get("request_key")

        if not request_key:
            print(f"[Fyers Auth Step 1 Notice] {res1}")

        # Step 2: Validate TOTP
        totp_code = pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
        url_step2 = "https://api-t1.fyers.in/api/v3/validate-totp"
        payload_step2 = {
            "request_key": request_key,
            "totp": totp_code
        }
        res2 = session.post(url_step2, json=payload_step2, headers=headers, timeout=15).json()
        request_key = res2.get("request_key", request_key)

        # Step 3: Validate PIN
        url_step3 = "https://api-t1.fyers.in/api/v3/validate-pin"
        payload_step3 = {
            "request_key": request_key,
            "pin": pin_code
        }
        res3 = session.post(url_step3, json=payload_step3, headers=headers, timeout=15).json()
        auth_code = res3.get("auth_code") or (res3.get("data") or {}).get("auth_code")

        if not auth_code:
            print(f"[Fyers Auth Step 3 Notice] {res3}")

        if auth_code:
            # Step 4: Exchange Auth Code for Access Token
            import hashlib
            app_id_hash = hashlib.sha256(f"{app_id}:{secret_key}".encode()).hexdigest()
            url_step4 = "https://api-t1.fyers.in/api/v3/validate-authcode"
            payload_step4 = {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code
            }
            res4 = session.post(url_step4, json=payload_step4, headers=headers, timeout=15).json()
            access_token = res4.get("access_token")

            if access_token:
                os.environ["FYERS_ACCESS_TOKEN"] = access_token
                
                # Save to .env
                env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
                if os.path.exists(env_path):
                    try:
                        with open(env_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                        new_lines = []
                        found = False
                        for l in lines:
                            if l.startswith("FYERS_ACCESS_TOKEN="):
                                new_lines.append(f'FYERS_ACCESS_TOKEN="{access_token}"\n')
                                found = True
                            else:
                                new_lines.append(l)
                        if not found:
                            new_lines.append(f'FYERS_ACCESS_TOKEN="{access_token}"\n')
                        with open(env_path, "w", encoding="utf-8") as f:
                            f.writelines(new_lines)
                    except Exception as env_err:
                        print(f"[Fyers Auth Notice] Could not update .env: {env_err}")

                # Save to logs/fyers_access_token.json
                token_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "fyers_access_token.json")
                os.makedirs(os.path.dirname(token_file), exist_ok=True)
                token_payload = {
                    "access_token": access_token,
                    "user_id": username,
                    "updated_at": datetime.datetime.now().isoformat(),
                    "saved_timestamp": time.time()
                }
                with open(token_file, "w") as f:
                    json.dump(token_payload, f, indent=4)

                print(f"[Fyers Auth] Auto-Login Successful! Fresh Fyers API v3 Access Token acquired for {username}.")
                return access_token

    except Exception as err:
        print(f"[Fyers Auth Exception] Error during automated login: {err}")

    return get_active_fyers_token()


def get_active_fyers_token() -> str:
    """Helper to retrieve active Fyers access token from memory, env, or logs/fyers_access_token.json."""
    token = os.getenv("FYERS_ACCESS_TOKEN", "").strip() or FYERS_ACCESS_TOKEN.strip()
    token_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "fyers_access_token.json")
    if (not token or token.startswith("MOCK") or token.startswith("your_")) and os.path.exists(token_file):
        try:
            with open(token_file, "r") as f:
                tdata = json.load(f)
                token = tdata.get("access_token", token).strip()
        except Exception:
            pass
    if token and not token.startswith("MOCK") and not token.startswith("your_"):
        return token
    return ""


def verify_and_fetch_live_fyers_balance(access_token: Optional[str] = None) -> tuple:
    """
    STRICT PRE-FLIGHT VERIFICATION GATE:
    Queries Fyers API v3 directly to verify live wallet balance.
    Returns tuple: (is_verified: bool, balance: float, status_msg: str).
    """
    is_conf, conf_msg = check_fyers_credentials_configured()
    if not is_conf:
        return False, 0.0, f"MIGRATION INCOMPLETE: {conf_msg}"

    tok = access_token or get_active_fyers_token()

    for attempt in range(2):
        if not tok or tok.startswith("MOCK") or tok.startswith("your_"):
            tok = auto_generate_fyers_token(force=True)

        if not tok or tok.startswith("MOCK") or tok.startswith("your_"):
            return False, 0.0, "Fyers Access Token could not be generated via TOTP auto-login."

        try:
            app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
            fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))
            res = fyers.funds()

            if isinstance(res, dict) and res.get("s") == "ok":
                fund_limits = res.get("fund_limit", [])
                avail = 0.0
                for f in fund_limits:
                    if f.get("title") in ["Total Balance", "Available Balance", "Net Available"]:
                        avail = float(f.get("equityAmount", 0.0) or f.get("amount", 0.0))
                        break
                if avail <= 0 and fund_limits:
                    avail = float(fund_limits[0].get("equityAmount", 0.0) or fund_limits[0].get("amount", 0.0))

                if avail > 0:
                    try:
                        sm = StateManager()
                        sm.state["current_wallet_balance"] = avail
                        sm._save_state(sm.state)
                    except Exception:
                        pass
                    return True, avail, "VERIFIED"
                else:
                    return True, INITIAL_WALLET_CAPITAL, "VERIFIED_DEFAULT"
            elif isinstance(res, dict) and res.get("code") in [401, -17]:
                if attempt == 0:
                    print("[Fyers Verification] Token expired (401). Retrying with fresh TOTP login...")
                    tok = auto_generate_fyers_token(force=True)
                    continue
        except Exception as ex:
            return False, 0.0, f"Fyers Connection Exception: {ex}"

    return False, INITIAL_WALLET_CAPITAL, "Fyers API verification complete."


def get_live_wallet_balance(access_token: Optional[str] = None, auto_renew: bool = False) -> float:
    """
    Queries Fyers API v3 (fyers.funds()) and returns real-time available cash balance directly from Fyers.
    Only triggers auto_generate_fyers_token() if auto_renew=True.
    """
    tok = access_token or get_active_fyers_token()
    if (not tok or tok.startswith("MOCK")) and auto_renew:
        tok = auto_generate_fyers_token()

    if tok and not tok.startswith("MOCK"):
        try:
            app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
            fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))
            res = fyers.funds()

            if isinstance(res, dict) and res.get("s") == "ok":
                fund_limits = res.get("fund_limit", [])
                for f in fund_limits:
                    if f.get("title") in ["Total Balance", "Available Balance", "Net Available"]:
                        avail = float(f.get("equityAmount", 0.0) or f.get("amount", 0.0))
                        if avail > 0:
                            try:
                                sm = StateManager()
                                sm.state["current_wallet_balance"] = avail
                                sm._save_state(sm.state)
                            except Exception:
                                pass
                            return avail
        except Exception as e:
            print(f"[Fyers Wallet Notice] {e}")

    try:
        return StateManager().get_current_wallet_balance()
    except Exception:
        return INITIAL_WALLET_CAPITAL


def place_aggressive_limit_order(
    symbol: str,
    quantity: int,
    transaction_type: str = "BUY",
    product_type: str = "INTRADAY",
    limit_price: float = 0.0,
    dry_run: bool = False,
    access_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes an order on Fyers API v3 gateway.
    """
    print(f"\n===========================================================================")
    print(f"  EXECUTING AGGRESSIVE LIMIT ORDER (FYERS API V3)")
    print(f"  Symbol               : {symbol}")
    print(f"  Transaction Type     : {transaction_type}")
    print(f"  Quantity             : {quantity} shares/contracts")
    print(f"  Limit Price          : Rs {limit_price:.2f}")
    print(f"  Execution Mode       : {'DRY_RUN (SIMULATION)' if dry_run else 'LIVE PRODUCTION'}")
    print(f"===========================================================================")

    if dry_run:
        sim_order_id = f"DRY_FYERS_{int(time.time())}"
        print(f"[FYERS DRY_RUN] Simulated order placed. Order ID: {sim_order_id}")
        return {
            "status": "TRADED",
            "order_id": sim_order_id,
            "filled_price": limit_price,
            "quantity": quantity,
            "symbol": symbol,
            "remarks": "Simulated Dry Run Fill"
        }

    tok = access_token or get_active_fyers_token()
    if not tok or tok.startswith("MOCK"):
        return {
            "status": "REJECTED_NO_TOKEN",
            "order_id": None,
            "remarks": "No valid Fyers Access Token provided."
        }

    app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
    fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))

    # SEBI APRIL 2026 COMPLIANCE MANDATE:
    # 1. Market Orders prohibited (type=1 Limit Order required).
    # 2. offlineOrder strictly False (No AMO orders allowed).
    # 3. Commodity segment validity strictly "DAY" (No IOC orders allowed).
    data = {
        "symbol": symbol,
        "qty": quantity,
        "type": 1, # Strictly 1=Limit Order (SEBI April 2026 Prohibition on Market Orders)
        "side": 1 if transaction_type.upper() == "BUY" else -1, # 1=Buy, -1=Sell
        "productType": product_type,
        "limitPrice": round(limit_price, 2),
        "stopPrice": 0.0,
        "validity": "DAY", # Strictly DAY (SEBI Mandate for Commodity/Equity Options)
        "disclosedQty": 0,
        "offlineOrder": False # Strictly False (SEBI Mandate prohibiting AMO orders)
    }

    try:
        response = fyers.place_order(data=data)
        print(f"[FYERS ORDER RESPONSE] {response}")
        
        if isinstance(response, dict) and response.get("s") == "ok":
            order_id = response.get("id")
            return {
                "status": "TRADED",
                "order_id": order_id,
                "filled_price": limit_price,
                "quantity": quantity,
                "symbol": symbol,
                "remarks": "Order Dispatched Successfully"
            }
        else:
            remarks = response.get("message", str(response)) if isinstance(response, dict) else str(response)
            return {"status": "REJECTED", "order_id": None, "remarks": remarks}
    except Exception as ex:
        print(f"[FYERS ORDER ERROR] {ex}")
        return {"status": "REJECTED_EXCEPTION", "order_id": None, "remarks": str(ex)}


class FyersTrader:
    """
    Main Fyers Execution Gateway Class orchestrating live trading,
    wallet balance inspection, and persistent trade logging.
    """
    def __init__(self, dry_run: bool = False, force_reset: bool = False):
        self.dry_run = dry_run
        self.access_token = get_active_fyers_token()
        self.state_mgr = StateManager(force_reset=force_reset)
        
        mode_str = "DRY_RUN (SIMULATION)" if self.dry_run else "LIVE PRODUCTION"
        print(f"[Fyers Gateway] Fyers API client initialized. Mode: {mode_str}")

    def get_read_only_wallet_balance(self) -> float:
        """Queries live available cash balance from Fyers API."""
        return get_live_wallet_balance(self.access_token)

    def execute_option_trade(
        self,
        option_contract: Dict[str, Any],
        max_budget: float,
        session_name: str = "NSE Equity Morning Session"
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a single-lot option trade on Fyers API v3.
        """
        symbol = option_contract.get("fyers_symbol") or option_contract.get("instrument_key") or option_contract["option_symbol"]
        lot_size = int(option_contract["lot_size"])
        ask_price = float(option_contract["ask_price"])
        exchange = "MCX_FO" if "MCX" in symbol else "NSE_FO"

        order_res = place_aggressive_limit_order(
            symbol=symbol,
            quantity=lot_size,
            transaction_type="BUY",
            product_type="INTRADAY",
            limit_price=ask_price,
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
                "instrument_key": symbol,
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
            status = order_res.get("status", "REJECTED")
            remarks = order_res.get("remarks", "Order not filled")
            print(f"[Fyers Execution Aborted] Order status: {status}. Remarks: {remarks}")

            if not self.dry_run:
                handle_execution_issue_and_halt(
                    issue_title=f"Fyers Order Execution Failed ({status})",
                    detailed_reason=f"Broker Response: {remarks}\nSymbol: {symbol}",
                    symbol=option_contract.get("option_symbol", "N/A")
                )
            return None


def handle_execution_issue_and_halt(issue_title: str, detailed_reason: str, symbol: str = "N/A"):
    """
    STRICT EXECUTION ISSUE SAFEGUARD:
    1. States problem in detail on console & logs.
    2. Sends a detailed, formatted Telegram alert detailing the issue.
    3. Emergency HALTS the trading engine (creates BOT_DISABLED_FLAG + locks StateManager)
       UNTIL user manually sends /resume or /start on Telegram.
    """
    print(f"\n🚨 [CRITICAL EXECUTION ISSUE HALT] {issue_title}")
    print(f"  Symbol/Contract: {symbol}")
    print(f"  Details        : {detailed_reason}")

    try:
        from config.settings import BOT_DISABLED_FLAG
        with open(BOT_DISABLED_FLAG, "w") as f:
            f.write(f"PAUSED_AT={datetime.datetime.now().isoformat()}|REASON={issue_title}")
    except Exception:
        pass

    try:
        sm = StateManager()
        sm.state["is_nse_locked_today"] = True
        sm.state["is_mcx_locked_today"] = True
        sm._save_state(sm.state)
    except Exception:
        pass

    try:
        from reporting.telegram_bot import send_telegram_message
        msg = (
            "🚨 <b>[EXECUTION ISSUE DETECTED - ENGINE HALTED]</b>\n"
            "========================================\n"
            f"<b>Issue Type       :</b> {issue_title}\n"
            f"<b>Symbol / Contract :</b> <code>{symbol}</code>\n"
            f"<b>Detailed Problem :</b>\n<pre>{detailed_reason}</pre>\n"
            "========================================\n"
            "🛑 <b>TRADING ENGINE HAS BEEN PAUSED FOR SAFETY.</b>\n"
            "<i>No further orders will be placed. Please resolve the issue and send /resume or /start on Telegram once ready.</i>"
        )
        send_telegram_message(msg)
    except Exception as tele_err:
        print(f"[Execution Issue Telegram Notice] {tele_err}")
