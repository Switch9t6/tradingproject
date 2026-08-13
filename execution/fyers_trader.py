import os
import sys
import time
import json
import pyotp
import requests
import threading
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

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_broker_check_cache = {"ts": 0.0, "result": None}
_broker_check_lock = threading.Lock()

_SEGMENT_ACTIVATION_KEYWORDS = (
    "segment",
    "not permitted",
    "not allowed",
    "not activate",
    "inactive segment",
    "privileg",
    "commodity segment",
    "not enabled",
    "unable to place",
)


def check_broker_trade_executed_today(access_token: Optional[str] = None, max_age_seconds: float = 30.0) -> Dict[str, Any]:
    """
    Broker-side ground truth: has this account ALREADY executed a trade today
    (an open option position, OR any fill in an option segment)?

    Reads Fyers directly (positions + tradebook), never local state files, so it
    survives Railway redeploys where state.json/scheduler_state.json are lost.
    Returns {"blocked": bool, "reason": str|None, "details": [str], "checked": bool}.
    """
    now = time.time()
    with _broker_check_lock:
        cached = _broker_check_cache.get("result")
        if cached and cached.get("checked") and (now - _broker_check_cache.get("ts", 0.0)) < max_age_seconds:
            return cached

    result = {"blocked": False, "reason": None, "details": [], "checked": False}

    tok = access_token or get_active_fyers_token()
    if not tok or tok.startswith("MOCK"):
        result["details"].append("No valid token; broker check skipped")
        return result

    try:
        app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
        fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))
        result["checked"] = True

        # 1) Open option positions -> always block new entries
        try:
            pos = fyers.positions()
            if isinstance(pos, dict) and pos.get("s") == "ok":
                for p in pos.get("netPositions", []):
                    if not isinstance(p, dict):
                        continue
                    sym = str(p.get("symbol") or "")
                    net_qty = int(p.get("netQty", 0) or 0)
                    if sym and net_qty != 0:
                        result["blocked"] = True
                        result["reason"] = result["reason"] or "LIVE_POSITION_OPEN"
                        result["details"].append(f"{sym} qty {net_qty}")
        except Exception as pos_err:
            print(f"[Broker Check Positions Notice] {pos_err}")

        # 2) Filled trades today in an option segment -> block new entries
        try:
            tb = fyers.tradebook()
            if isinstance(tb, dict) and tb.get("s") == "ok":
                today_ist = datetime.datetime.now(IST_TZ).date()
                for t in tb.get("tradeBook", []):
                    if not isinstance(t, dict):
                        continue
                    sym = str(t.get("symbol") or "")
                    if not (sym.startswith("NSE:") or sym.startswith("MCX:")):
                        continue
                    if int(t.get("filledQty", 0) or 0) <= 0:
                        continue
                    day_match = True
                    ts_raw = t.get("tradeTime") or t.get("exchTradeTime") or 0
                    try:
                        ts_raw = int(ts_raw)
                        if ts_raw:
                            ts_seconds = (ts_raw / 1000.0) if ts_raw > 1e12 else float(ts_raw)
                            trade_dt = datetime.datetime.fromtimestamp(ts_seconds, IST_TZ)
                            day_match = (trade_dt.date() == today_ist)
                    except Exception:
                        day_match = True
                    if day_match:
                        result["blocked"] = True
                        result["reason"] = result["reason"] or "TRADE_EXECUTED_TODAY"
                        result["details"].append(f"{sym} qty {t.get('filledQty')} @ {t.get('tradePrice') or t.get('price') or '?'}")
        except Exception as tb_err:
            print(f"[Broker Check Tradebook Notice] {tb_err}")
    except Exception as init_err:
        print(f"[Broker Check Notice] {init_err}")
        result["details"].append(f"Broker check failed: {init_err}")

    with _broker_check_lock:
        _broker_check_cache["ts"] = time.time()
        _broker_check_cache["result"] = result
    return result


def is_segment_activation_rejection(status: str, remarks: str) -> bool:
    """True if a rejected order indicates the account cannot trade that segment."""
    if status not in ("REJECTED",):
        return False
    low = str(remarks).lower()
    return any(k in low for k in _SEGMENT_ACTIVATION_KEYWORDS)


def mark_segment_disabled(segment: str, remarks: str):
    """Persists a 'segment disabled for today' marker (state.json + flag file under
    the persistent dir) so the engine stops retrying a segment the account cannot trade."""
    today = datetime.datetime.now(IST_TZ).date().isoformat()
    try:
        from config.settings import SEGMENT_DISABLED_FLAG_PATTERN
        flag = SEGMENT_DISABLED_FLAG_PATTERN.format(segment=segment)
        os.makedirs(os.path.dirname(flag) or ".", exist_ok=True)
        with open(flag, "w", encoding="utf-8") as f:
            f.write(f"SEGMENT={segment}|DATE={today}|REASON={remarks}")
    except Exception as e:
        print(f"[Segment Disabled Flag Notice] {e}")
    try:
        sm = StateManager()
        disabled = sm.state.setdefault("disabled_segments", {})
        disabled[segment] = {"date": today, "remarks": str(remarks)[:300]}
        sm._save_state(sm.state)
    except Exception as e:
        print(f"[Segment Disabled State Notice] {e}")
    print(f"[Segment Disabled] {segment} marked disabled for {today}: {remarks}")


def segment_disabled_today(segment: str) -> bool:
    """True if the given segment (e.g. MCX_FO) was marked disabled today."""
    today = datetime.datetime.now(IST_TZ).date().isoformat()
    try:
        from config.settings import SEGMENT_DISABLED_FLAG_PATTERN
        flag = SEGMENT_DISABLED_FLAG_PATTERN.format(segment=segment)
        if os.path.exists(flag):
            with open(flag, "r", encoding="utf-8") as f:
                if f"DATE={today}" in f.read():
                    return True
    except Exception:
        pass
    try:
        sm = StateManager()
        entry = sm.state.get("disabled_segments", {}).get(segment, {})
        return entry.get("date") == today
    except Exception:
        return False


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

        session = requests.Session()
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

        # Step 1: Send FY ID (web login app_id "2") -> request_key
        url_step1 = "https://api-t2.fyers.in/vagator/v2/send_login_otp"
        payload_step1 = {
            "fy_id": username,
            "app_id": "2"
        }
        res1 = session.post(url_step1, json=payload_step1, headers=headers, timeout=20)
        res1j = res1.json()
        request_key = res1j.get("request_key")
        if not request_key:
            print(f"[Fyers Auth Step 1 Notice] status={res1.status_code} {res1j}")

        # Step 2: Validate TOTP -> new request_key
        totp_code = pyotp.TOTP(totp_secret.replace(" ", "").upper()).now()
        url_step2 = "https://api-t2.fyers.in/vagator/v2/verify_otp"
        payload_step2 = {
            "request_key": request_key,
            "otp": totp_code
        }
        res2 = session.post(url_step2, json=payload_step2, headers=headers, timeout=20)
        res2j = res2.json()
        if res2j.get("s") != "ok" or not res2j.get("request_key"):
            print(f"[Fyers Auth Step 2 Notice] status={res2.status_code} {res2j}")
        request_key = res2j.get("request_key", request_key)

        # Step 3: Validate PIN -> trade access token
        url_step3 = "https://api-t2.fyers.in/vagator/v2/verify_pin"
        payload_step3 = {
            "request_key": request_key,
            "identity_type": "pin",
            "identifier": pin_code
        }
        res3 = session.post(url_step3, json=payload_step3, headers=headers, timeout=20)
        res3j = res3.json()
        trade_token = (res3j.get("data") or {}).get("access_token")
        if not trade_token:
            print(f"[Fyers Auth Step 3 Notice] status={res3.status_code} {res3j}")

        auth_code = ""
        if trade_token:
            # Step 4: Exchange trade token for v3 auth_code (308 redirect carries Url with auth_code)
            import hashlib
            from urllib.parse import urlparse, parse_qs
            app_id_core = app_id.split("-")[0] if "-" in app_id else app_id
            app_type = app_id.split("-")[1] if "-" in app_id else "100"
            url_step4 = "https://api-t1.fyers.in/api/v3/token"
            payload_step4 = {
                "fyers_id": username,
                "app_id": app_id_core,
                "redirect_uri": redirect_uri,
                "appType": app_type,
                "code_challenge": "",
                "state": "sample_state",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True
            }
            res4 = session.post(url_step4, json=payload_step4, headers={**headers, "Authorization": f"Bearer {trade_token}"}, timeout=20, allow_redirects=False)
            try:
                token_url = res4.json().get("Url", "")
                auth_code = parse_qs(urlparse(token_url).query).get("auth_code", [""])[0]
            except Exception as parse_err:
                print(f"[Fyers Auth Step 4 Notice] status={res4.status_code} parse_err={parse_err} body={res4.text[:300]}")

        if auth_code:
            # Step 5: Exchange Auth Code for v3 Access Token
            app_id_hash = hashlib.sha256(f"{app_id}:{secret_key}".encode()).hexdigest()
            url_step5 = "https://api-t1.fyers.in/api/v3/validate-authcode"
            payload_step5 = {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code
            }
            res5 = session.post(url_step5, json=payload_step5, headers=headers, timeout=20).json()
            access_token = res5.get("access_token")

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


def _poll_order_fill(fyers, order_id: str, qty: int, timeout_seconds: float) -> Dict[str, Any]:
    """
    Polls the Fyers orderbook until the order fills, is rejected, or times out.
    Returns {"status": "TRADED"|"REJECTED"|"PENDING", ...}.
    Fyers orderbook 'status' codes: 1=Cancelled, 2=Complete, 3=Rejected, 4=Pending.
    """
    deadline = time.time() + max(5.0, timeout_seconds)
    last_status = "4"
    while time.time() < deadline:
        try:
            ob = fyers.orderbook()
            if isinstance(ob, dict) and ob.get("s") == "ok":
                for o in ob.get("orderBook", []):
                    if o.get("orderId") != order_id:
                        continue
                    status = str(o.get("status", ""))
                    last_status = status
                    filled_qty = int(o.get("filledQty", 0) or 0)
                    avg_price = float(o.get("avgPrice", 0) or 0)
                    if filled_qty >= qty:
                        return {"status": "TRADED", "filled_price": avg_price if avg_price > 0 else None, "order_status": status}
                    if status in ("1", "3", "Cancelled", "Rejected", "CANCELLED", "REJECTED"):
                        return {"status": "REJECTED", "message": f"order_status={status}", "order_status": status}
        except Exception as poll_err:
            print(f"[FYERS FILL POLL WARNING] {poll_err}")
        time.sleep(2)
    return {"status": "PENDING", "order_status": last_status}


def place_aggressive_limit_order(
    symbol: str,
    quantity: int,
    transaction_type: str = "BUY",
    product_type: str = "INTRADAY",
    limit_price: float = 0.0,
    dry_run: bool = False,
    access_token: Optional[str] = None,
    tick_size: float = 0.05,
    poll_for_fill: bool = True,
    fill_timeout_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """
    Executes an order on Fyers API v3 gateway.
    """
    # Fyers rejects limit prices that are not a multiple of the instrument tick size
    if tick_size <= 0:
        tick_size = 0.05
    limit_price = round(round(limit_price / tick_size) * tick_size, 2)

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
            order_id = response.get("id") or response.get("orderid")
            if not order_id:
                return {"status": "REJECTED", "order_id": None, "remarks": "Order accepted by broker but no order id returned"}

            if not poll_for_fill:
                return {
                    "status": "DISPATCHED",
                    "order_id": order_id,
                    "filled_price": limit_price,
                    "quantity": quantity,
                    "symbol": symbol,
                    "remarks": "Order dispatched to broker (fill pending confirmation)"
                }

            timeout = fill_timeout_seconds if fill_timeout_seconds is not None else LIMIT_ORDER_TIMEOUT_SECONDS
            fill = _poll_order_fill(fyers, order_id, quantity, timeout)

            if fill["status"] == "TRADED":
                return {
                    "status": "TRADED",
                    "order_id": order_id,
                    "filled_price": fill.get("filled_price") or limit_price,
                    "quantity": quantity,
                    "symbol": symbol,
                    "remarks": f"Order filled (broker status: {fill.get('order_status')})"
                }
            if fill["status"] == "REJECTED":
                return {
                    "status": "REJECTED",
                    "order_id": order_id,
                    "remarks": f"Order rejected by broker after dispatch: {fill.get('message')}"
                }
            return {
                "status": "PENDING",
                "order_id": order_id,
                "quantity": quantity,
                "symbol": symbol,
                "remarks": f"Order still pending after {timeout:.0f}s (broker status: {fill.get('order_status')})"
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
            access_token=self.access_token,
            tick_size=float(option_contract.get("tick_size") or 0.05)
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

            # Start background TSL / target / stop-loss monitoring for this position.
            try:
                from execution.position_monitor import start_position_monitor
                start_position_monitor(
                    symbol=symbol,
                    quantity=lot_size,
                    trade_id=trade_id,
                    entry_premium=filled_price,
                    target_p=target_p,
                    stop_p=stop_p,
                    tick_size=float(option_contract.get("tick_size") or 0.05),
                    access_token=self.access_token,
                    dry_run=self.dry_run,
                    exchange=exchange,
                )
            except Exception as mon_err:
                print(f"[Position Monitor Start Notice] {mon_err}")

            return result
        elif order_res.get("status") in ("PENDING", "DISPATCHED"):
            # Order was accepted but fill is unconfirmed: do NOT record an OPEN
            # position or consume the session cap on a fill that never happened.
            print(f"[Fyers Execution Pending] Order {order_res.get('order_id')} not yet filled: {order_res.get('remarks')}")
            return {
                "trade_id": None,
                "order_id": order_res.get("order_id"),
                "instrument_key": symbol,
                "option_symbol": option_contract["option_symbol"],
                "entry_premium": None,
                "quantity": lot_size,
                "target_price": None,
                "stop_price": None,
                "status": order_res.get("status"),
                "exchange": exchange,
                "remarks": order_res.get("remarks")
            }
        else:
            status = order_res.get("status", "REJECTED")
            remarks = order_res.get("remarks", "Order not filled")
            print(f"[Fyers Execution Aborted] Order status: {status}. Remarks: {remarks}")

            segment = "MCX_FO" if "MCX" in symbol else "NSE_FO"

            # Segment-activation rejection (e.g. MCX not enabled on the account): mark the
            # segment disabled for today and alert the user - do NOT halt the whole engine.
            if not self.dry_run and is_segment_activation_rejection(status, str(remarks)):
                mark_segment_disabled(segment, f"{status}: {remarks}")
                try:
                    from reporting.telegram_bot import send_telegram_message
                    send_telegram_message(
                        f"🚫 <b>[SEGMENT DISABLED ON BROKER]</b>\n"
                        "========================================\n"
                        f"<b>Segment   :</b> {segment}\n"
                        f"<b>Contract  :</b> <code>{symbol}</code>\n"
                        f"<b>Broker Msg:</b> <code>{remarks}</code>\n"
                        "========================================\n"
                        "Your Fyers account cannot trade this segment. The engine will NOT retry it today. "
                        "Enable the segment on your broker and it will be re-evaluated tomorrow.",
                    )
                except Exception as tele_err:
                    print(f"[Segment Disabled Telegram Notice] {tele_err}")
                return {
                    "trade_id": None,
                    "order_id": order_res.get("order_id"),
                    "instrument_key": symbol,
                    "option_symbol": option_contract["option_symbol"],
                    "status": "SEGMENT_DISABLED",
                    "exchange": segment,
                    "remarks": remarks,
                }

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


def _close_uncovered_positions(
    access_token: Optional[str] = None,
    dry_run: bool = False,
    exit_reason: str = "EOD_SQUAREOFF",
    exit_timeout_seconds: Optional[float] = None,
    send_telegram_alert: bool = True,
) -> Dict[str, Any]:
    """
    Safety net: reconciles against Fyers positions() and closes any open option
    position that is NOT tracked in local state (e.g. an entry that filled after
    the fill-poll timeout). Best-effort; never halts on parsing issues.
    """
    tok = access_token or get_active_fyers_token()
    if not tok or tok.startswith("MOCK"):
        print("  [Square-off] No valid token; skipping broker reconciliation.")
        return {"status": "no_position"}

    try:
        app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
        fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))
        res = fyers.positions()
    except Exception as pos_err:
        print(f"  [Square-off] Broker positions query failed: {pos_err}")
        return {"status": "error", "message": f"Broker positions query failed: {pos_err}"}

    if not (isinstance(res, dict) and res.get("s") == "ok"):
        print(f"  [Square-off] Broker positions query returned: {res}")
        return {"status": "no_position"}

    open_positions = []
    for p in res.get("netPositions", []):
        if not isinstance(p, dict):
            continue
        symbol = str(p.get("symbol") or "").strip()
        net_qty = int(p.get("netQty", 0) or 0)
        if symbol and net_qty != 0:
            open_positions.append({"symbol": symbol, "net_qty": net_qty})

    if not open_positions:
        print("  [Square-off] 0 open positions on broker. All positions squared off cleanly.")
        return {"status": "no_position"}

    print(f"  [Square-off] Broker shows {len(open_positions)} open position(s) not in local state. Closing...")
    closed = []
    for pos in open_positions:
        symbol = pos["symbol"]
        qty = abs(pos["net_qty"])
        if pos["net_qty"] < 0:
            print(f"  [Square-off] Skipping short position (not owned): {symbol} ({pos['net_qty']})")
            continue

        # Resolve tick size from the instrument maps
        tick_size = 0.05
        try:
            prefix = symbol.split(":")[0] if ":" in symbol else "NSE"
            if prefix == "MCX":
                from scanner.option_mapper import get_fyers_mcx_instrument_map
                inst_map = get_fyers_mcx_instrument_map()
            else:
                from scanner.option_mapper import get_fyers_nse_instrument_map
                inst_map = get_fyers_nse_instrument_map()
            for entry in inst_map.values():
                if isinstance(entry, dict) and entry.get("fyers_symbol") == symbol:
                    tick_size = float(entry.get("tick_size") or 0.05)
                    break
        except Exception:
            pass

        # Marketable SELL limit off current LTP
        ltp = 0.0
        try:
            q = fyers.quotes(data={"symbols": symbol})
            if isinstance(q, dict) and q.get("s") == "ok" and q.get("d"):
                ltp = float(q["d"][0].get("v", {}).get("lp", 0) or 0)
        except Exception as quote_err:
            print(f"  [Square-off] LTP fetch failed for {symbol}: {quote_err}")
        exit_limit = (ltp - max(tick_size, ltp * 0.005)) if ltp > 0 else 0.05

        order_res = place_aggressive_limit_order(
            symbol=symbol,
            quantity=qty,
            transaction_type="SELL",
            product_type="INTRADAY",
            limit_price=exit_limit,
            dry_run=dry_run,
            access_token=tok,
            tick_size=tick_size,
            fill_timeout_seconds=exit_timeout_seconds
        )

        trade_id = 0
        if order_res.get("status") == "TRADED":
            # Match an OPEN row in the DB so P&L can be settled
            try:
                sm = StateManager()
                rows = sm.get_today_trades()
                for r in rows:
                    if r.get("status") == "OPEN" and symbol.endswith(str(r.get("option_symbol", "")).split(":")[-1]):
                        trade_id = int(r.get("id") or 0)
                        break
            except Exception:
                pass
            if trade_id:
                sm.record_exit_trade(trade_id=trade_id, exit_premium=float(order_res.get("filled_price") or exit_limit), exit_reason=exit_reason)
            else:
                sm.state["active_position"] = None
                sm.state["active_trade_id"] = None
                sm._save_state(sm.state)
        closed.append({"symbol": symbol, "status": order_res.get("status"), "order_id": order_res.get("order_id"), "trade_id": trade_id})

        if send_telegram_alert:
            try:
                from reporting.telegram_bot import send_telegram_message
                send_telegram_message(
                    "✅ <b>[UNTRACKED POSITION CLOSED]</b>\n"
                    f"<b>Symbol :</b> <code>{symbol}</code>\n"
                    f"<b>Qty    :</b> {qty}\n"
                    f"<b>Status :</b> {order_res.get('status')} (order {order_res.get('order_id')})\n"
                    f"<b>Reason :</b> {exit_reason}"
                )
            except Exception:
                pass

    return {"status": "closed_uncovered", "positions": closed}


def resolve_squareoff_symbol(active_position: Dict[str, Any]) -> tuple:
    """
    Returns the full Fyers symbol + tick size needed to close an active position.
    Uses the fyers_symbol stored at entry time; falls back to a best-effort lookup
    in the Fyers instrument maps for positions recorded before that field existed.
    """
    sym = str(active_position.get("fyers_symbol") or "").strip()
    tick = float(active_position.get("tick_size") or 0.05)
    if sym:
        return sym, tick

    option_symbol = str(active_position.get("option_symbol") or "").strip()
    exchange = str(active_position.get("exchange") or "").upper()
    prefix = "MCX" if "MCX" in exchange else "NSE"
    candidate = f"{prefix}:{option_symbol}"
    if not option_symbol:
        return candidate, tick

    try:
        if prefix == "MCX":
            from scanner.option_mapper import get_fyers_mcx_instrument_map
            inst_map = get_fyers_mcx_instrument_map()
        else:
            from scanner.option_mapper import get_fyers_nse_instrument_map
            inst_map = get_fyers_nse_instrument_map()
        for entry in inst_map.values():
            if isinstance(entry, dict) and entry.get("fyers_symbol") == candidate:
                return candidate, float(entry.get("tick_size") or tick)
    except Exception as lookup_err:
        print(f"[Square-off Symbol Lookup Notice] {lookup_err}")

    return candidate, tick


def square_off_active_position(
    access_token: Optional[str] = None,
    dry_run: bool = False,
    exit_reason: str = "EOD_SQUAREOFF",
    exit_timeout_seconds: Optional[float] = None,
    send_telegram_alert: bool = True,
) -> Dict[str, Any]:
    """
    CLOSES any open position by placing a marketable SELL limit order on the held
    symbol and settling the P&L via StateManager.record_exit_trade().
    Prices the exit off the current Fyers LTP (rounded down to tick) so the SELL
    is immediately marketable. Returns a status dict.
    """
    sm = StateManager()
    active_pos = sm.state.get("active_position")
    if not active_pos:
        print("  [Square-off] No active position in local state. Checking broker positions for uncovered exposure...")
        return _close_uncovered_positions(
            access_token=access_token,
            dry_run=dry_run,
            exit_reason=exit_reason,
            exit_timeout_seconds=exit_timeout_seconds,
            send_telegram_alert=send_telegram_alert,
        )

    symbol, tick_size = resolve_squareoff_symbol(active_pos)
    qty = int(active_pos.get("quantity") or 0)
    trade_id = int(active_pos.get("trade_id") or 0)

    print(f"\n  [Square-off] Closing active position: {symbol} x {qty}")
    if qty <= 0 or not symbol:
        return {"status": "error", "message": f"Invalid position for square-off (symbol={symbol}, qty={qty})"}

    # Fetch current LTP to make the exit order marketable
    ltp = 0.0
    tok = access_token or get_active_fyers_token()
    if tok and not tok.startswith("MOCK") and not dry_run:
        try:
            app_id = FYERS_APP_ID or os.getenv("FYERS_APP_ID", "").strip()
            fyers = fyersModel.FyersModel(client_id=app_id, token=tok, is_async=False, log_path=os.path.dirname(TOKEN_FILE_PATH))
            q = fyers.quotes(data={"symbols": symbol})
            if isinstance(q, dict) and q.get("s") == "ok" and q.get("d"):
                ltp = float(q["d"][0].get("v", {}).get("lp", 0) or 0)
        except Exception as quote_err:
            print(f"[Square-off Quote Notice] Could not fetch LTP for {symbol}: {quote_err}")

    # SELL at or below the marketable price so the exit fills immediately
    if ltp > 0:
        exit_limit = ltp - max(tick_size, ltp * 0.005)
    else:
        exit_limit = float(active_pos.get("entry_premium") or 0.05)

    res = place_aggressive_limit_order(
        symbol=symbol,
        quantity=qty,
        transaction_type="SELL",
        product_type="INTRADAY",
        limit_price=exit_limit,
        dry_run=dry_run,
        access_token=tok,
        tick_size=tick_size,
        fill_timeout_seconds=exit_timeout_seconds
    )

    if res.get("status") == "TRADED":
        filled_price = float(res.get("filled_price") or exit_limit)
        if trade_id:
            sm.record_exit_trade(trade_id=trade_id, exit_premium=filled_price, exit_reason=exit_reason)
        else:
            sm.state["active_position"] = None
            sm.state["active_trade_id"] = None
            sm._save_state(sm.state)

        print(f"  [Square-off] Position closed @ Rs {filled_price:.2f} (order {res.get('order_id')}). P&L settled.")
        if send_telegram_alert:
            try:
                from reporting.telegram_bot import send_telegram_message
                msg = (
                    "✅ <b>[POSITION SQUARED OFF]</b>\n"
                    "========================================\n"
                    f"<b>Symbol       :</b> <code>{symbol}</code>\n"
                    f"<b>Qty          :</b> {qty}\n"
                    f"<b>Exit Premium :</b> Rs {filled_price:.2f}\n"
                    f"<b>Reason       :</b> {exit_reason}\n"
                    f"<b>Order ID     :</b> <code>{res.get('order_id')}</code>\n"
                    "========================================"
                )
                send_telegram_message(msg)
            except Exception as tele_err:
                print(f"[Square-off Telegram Notice] {tele_err}")
        return {
            "status": "TRADED",
            "order_id": res.get("order_id"),
            "symbol": symbol,
            "quantity": qty,
            "exit_premium": filled_price,
            "trade_id": trade_id,
            "remarks": res.get("remarks")
        }

    print(f"  [Square-off] Could not close position: {res.get('status')} -> {res.get('remarks')}")
    if not dry_run and res.get("status") in ("REJECTED", "REJECTED_EXCEPTION"):
        handle_execution_issue_and_halt(
            issue_title="EOD SQUARE-OFF FAILED - POSITION STILL OPEN",
            detailed_reason=f"Broker Response: {res.get('remarks')}\nSymbol: {symbol}",
            symbol=symbol
        )
    return {
        "status": res.get("status"),
        "order_id": res.get("order_id"),
        "symbol": symbol,
        "quantity": qty,
        "trade_id": trade_id,
        "remarks": res.get("remarks")
    }
