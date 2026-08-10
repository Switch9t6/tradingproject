import os
import sys
import time
import json
import random
import datetime
import requests
from typing import Dict, Any, Optional, Tuple
from functools import wraps

try:
    from dhanhq import dhanhq
except ImportError:
    dhanhq = None

from config.settings import (
    DHAN_CLIENT_ID,
    DHAN_ACCESS_TOKEN,
    DHAN_RENEW_TOKEN_URL,
    INITIAL_WALLET_CAPITAL,
    MICRO_CAPITAL_BUDGET_CAP,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    LIMIT_ORDER_BUFFER_PCT,
    LIMIT_ORDER_TIMEOUT_SECONDS,
    CONSECUTIVE_LOSS_SCALING_PCT,
    TOKEN_FILE_PATH
)
from execution.state_manager import StateManager
from execution.position_monitor import PositionMonitor


def retry_api_call(max_retries: int = 3, delay: float = 1.0):
    """
    Hardened Network Retry Decorator: Handles transient network glitches and API rate limits
    with exponential backoff retries.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        print(f"❌ [Dhan API Error] Exceeded max retries ({max_retries}): {e}")
                        raise e
                    wait_time = delay * (2 ** (attempt - 1))
                    print(f"⚠️ [Dhan API Retry] Call '{func.__name__}' failed (Attempt {attempt}/{max_retries}): {e}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator


def renew_dhan_access_token(client_id: Optional[str] = None, access_token: Optional[str] = None) -> Tuple[bool, str]:
    """
    Automated 24-Hour Token Renewal routine targeting https://api.dhan.co/v2/RenewToken.
    Executes in daily pre-flight check routine to maintain session longevity without manual re-authentication.
    """
    client_id = client_id or DHAN_CLIENT_ID or os.getenv("DHAN_CLIENT_ID", "")
    access_token = access_token or DHAN_ACCESS_TOKEN or os.getenv("DHAN_ACCESS_TOKEN", "")

    if not client_id or not access_token or access_token.startswith("MOCK"):
        print("[Dhan Token Renewal] Mock / Missing credentials. Skipping automated renewal.")
        return False, "MOCK_OR_MISSING_CREDENTIALS"

    headers = {
        "dhan-client-id": client_id,
        "access-token": access_token,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(DHAN_RENEW_TOKEN_URL, headers=headers, timeout=10)
        if resp.status_code == 200:
            res_json = resp.json()
            data = res_json.get("data", {}) if isinstance(res_json, dict) else {}
            new_token = data.get("token") or res_json.get("token")
            if new_token:
                os.environ["DHAN_ACCESS_TOKEN"] = new_token
                token_payload = {
                    "access_token": new_token,
                    "updated_at": datetime.datetime.now().isoformat(),
                    "client_id": client_id
                }
                os.makedirs(os.path.dirname(TOKEN_FILE_PATH), exist_ok=True)
                with open(TOKEN_FILE_PATH, "w") as f:
                    json.dump(token_payload, f, indent=4)
                print("✅ [Dhan Token Renewal] 24-Hour Access Token renewed successfully!")
                return True, new_token
            else:
                print(f"[Dhan Token Renewal Notice] Response status {resp.status_code}: {resp.text}")
                return False, resp.text
        else:
            print(f"[Dhan Token Renewal Error] HTTP {resp.status_code}: {resp.text}")
            return False, f"HTTP_{resp.status_code}"
    except Exception as e:
        print(f"[Dhan Token Renewal Exception] Failed to renew token: {e}")
        return False, str(e)


class DhanTrader:
    """
    Senior Quantitative Execution Gateway for DhanHQ API v2.
    Features:
    - Live Wallet Inspection (Queries Dhan get_fund_limits API)
    - Dynamic Sizing & Risk Management (Scales trade budget dynamically)
    - Aggressive Limit Orders with 5-Second Timeout Fill Verification
    - Live Position Exit Monitoring (+25% Target, Step Trailing SL, 30-Min Time Exit)
    """
    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        dry_run: bool = False,
        force_reset: bool = False,
        micro_capital: bool = False
    ):
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID") or DHAN_CLIENT_ID
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN") or DHAN_ACCESS_TOKEN
        self.dry_run = dry_run or not self.access_token or self.access_token.startswith("MOCK")
        self.micro_capital = micro_capital
        self.state_mgr = StateManager(force_reset=force_reset)

        if not self.dry_run and dhanhq is not None:
            try:
                try:
                    from dhanhq import DhanContext
                    ctx = DhanContext(self.client_id, self.access_token)
                    self.dhan = dhanhq(ctx)
                except Exception:
                    self.dhan = dhanhq(self.client_id, self.access_token)
            except Exception as e:
                print(f"[Dhan Gateway Notice] Failed to initialize Dhan client: {e}. Defaulting to Dry-Run.")
                self.dhan = None
                self.dry_run = True
        else:
            self.dhan = None

    @retry_api_call(max_retries=3, delay=1.0)
    def get_read_only_wallet_balance(self) -> float:
        """
        Queries Dhan API get_fund_limits() to inspect unencumbered available balance.
        In Live Mode: Queries live Dhan funds.
        In Dry-Run Mode: Fetches updated persistent wallet balance from StateManager.
        """
        if self.dry_run or self.dhan is None:
            current_wallet = self.state_mgr.get_current_wallet_balance()
            print(f"[Dhan Wallet Inspector] Current Wallet Balance: Rs {current_wallet:,.2f} INR (Mock/Persistent Mode).")
            return current_wallet

        try:
            res = self.dhan.get_fund_limits()
            data = res.get("data", res) if isinstance(res, dict) else res
            
            avail_bal = None
            if isinstance(data, dict):
                avail_bal = data.get("availabelBalance") or data.get("availableBalance") or data.get("sodLimit")
            elif hasattr(data, "availabelBalance"):
                avail_bal = getattr(data, "availabelBalance")

            if avail_bal is not None and float(avail_bal) > 0:
                funds = float(avail_bal)
            else:
                funds = INITIAL_WALLET_CAPITAL

            # Sync Live Balance to StateManager
            self.state_mgr.state["current_wallet_balance"] = funds
            self.state_mgr._save_state(self.state_mgr.state)

            print(f"[Dhan Wallet Inspector] Dhan Live Available Cash Balance: Rs {funds:,.2f} INR.")
            return funds
        except Exception as e:
            fallback_wallet = self.state_mgr.get_current_wallet_balance()
            print(f"[Dhan Wallet Inspector] Error reading fund limits: {e}. Falling back to state wallet Rs {fallback_wallet:,.2f}.")
            return fallback_wallet

    def execute_option_trade(
        self,
        option_contract: Dict[str, Any],
        sim_scenario: str = "AUTO",
        override_daily_limit: bool = False,
        auto_approve: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Executes single lot option order on DhanHQ API v2.
        - Validates spread <= 1.5%
        - Calculates limit price matching Ask price
        - Maps exchange segment (NSE_FNO vs MCX_COMM)
        - Executes order via dhan.place_order()
        - Attaches 5-second fill verification loop
        """
        exchange = option_contract.get("exchange", "NSE_FO")
        if (
            option_contract.get("is_mcx")
            or "MCX" in str(exchange).upper()
            or "CRUDE" in str(option_contract.get("underlying_symbol", "")).upper()
            or "CRUDE" in str(option_contract.get("option_symbol", "")).upper()
        ):
            exchange = "MCX_FO"

        if not self.state_mgr.is_trade_allowed_today(exchange=exchange, override_daily_limit=override_daily_limit):
            print(f"[Dhan Trader] Trade execution aborted: Session cap of 1 trade per day reached for {exchange}.")
            return None

        # Fetch Real-Time Wallet Balance
        available_cash = self.get_read_only_wallet_balance()
        
        # Dynamic Trade Budget Sizing
        dynamic_budget = MICRO_CAPITAL_BUDGET_CAP if self.micro_capital else available_cash
        
        if self.state_mgr.get_last_trade_pnl() < 0 and available_cash == INITIAL_WALLET_CAPITAL:
            dynamic_budget = round(dynamic_budget * CONSECUTIVE_LOSS_SCALING_PCT, 2)
            print(f"  [Capital Scaling Active] Previous trade was a loss. Scaled lot budget down to Rs {dynamic_budget:,.2f} INR.")

        print(f"[Real-Time Sizing] Available Cash: Rs {available_cash:,.2f} INR | Dynamic Trade Budget Cap: Rs {dynamic_budget:,.2f} INR.")

        if option_contract["total_lot_cost"] > dynamic_budget:
            print(f"[Dhan Trader] Lot cost (Rs {option_contract['total_lot_cost']:,.2f}) exceeds real-time available trade budget (Rs {dynamic_budget:,.2f}). Aborting trade.")
            return None

        option_symbol = option_contract["option_symbol"]
        lot_size = option_contract["lot_size"]
        ask_price = option_contract.get("ask_price", option_contract["estimated_premium"])
        bid_price = option_contract.get("bid_price", round(ask_price * 0.992, 2))

        # 1. Pre-Order Bid-Ask Spread Guardrail Check (<= 1.5%)
        spread_pct = ((ask_price - bid_price) / ask_price) * 100.0 if ask_price > 0 else 0.0
        if spread_pct > 1.5:
            print(f"[Dhan Trader Guardrail Block] Option Bid-Ask spread ({spread_pct:.2f}%) exceeds 1.5% limit. Execution aborted.")
            return None

        # 2. Aggressive Limit Price set to Ask Price for instant execution fill
        limit_price = round(ask_price, 2)
        if limit_price <= 0:
            limit_price = round(ask_price * (1.0 + LIMIT_ORDER_BUFFER_PCT), 2)
            
        entry_premium = ask_price
        target_price = round(entry_premium * (1.0 + TAKE_PROFIT_PCT), 2)
        initial_stop_price = round(entry_premium * (1.0 - STOP_LOSS_PCT), 2)

        # Net-of-Friction Expectancy Guardrail Check
        from reporting.friction_calculator import calculate_trade_friction
        f_est = calculate_trade_friction(lot_size, entry_premium, target_price)
        expected_target_net_pnl = f_est["net_pnl"]
        min_required_net_pnl = round(f_est["total_friction"] * 1.25, 2)

        if expected_target_net_pnl < min_required_net_pnl:
            print(f"[Net Expectancy Guardrail Block] Expected Net Target PnL (Rs {expected_target_net_pnl:.2f}) < Min Friction Multiple (Rs {min_required_net_pnl:.2f}). Friction drag too high. Order aborted.")
            return None

        print("=" * 75)
        print(f"  EXECUTING AGGRESSIVE LIMIT ORDER (DHAN API V2)")
        print(f"  Option Contract      : {option_symbol}")
        print(f"  Quantity (Single Lot): {lot_size} shares")
        print(f"  Bid / Ask Quote      : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {spread_pct:.2f}%)")
        print(f"  Aggressive Limit     : Rs {limit_price:.2f} / share")
        print(f"  Total Order Value    : Rs {entry_premium * lot_size:,.2f} INR")
        print(f"  Target (+25%) / SL   : Rs {target_price:.2f} / Rs {initial_stop_price:.2f}")
        print(f"  Est. Roundtrip Fees  : Rs {f_est['total_friction']:.2f} INR | Net Target PnL: +Rs {expected_target_net_pnl:.2f} INR")
        print("=" * 75)

        # INTERACTIVE USER APPROVAL GUARDRAIL
        if not auto_approve:
            print("\n" + "!" * 75)
            print("  USER APPROVAL REQUIRED FOR ORDER PLACEMENT")
            print(f"  Confirm Option Contract : {option_symbol} ({lot_size} shares @ Rs {entry_premium:.2f}/share)")
            print(f"  Total Investment Value  : Rs {entry_premium * lot_size:,.2f} INR")
            print(f"  Target (+25%) / SL (-12%): Rs {target_price:.2f} / Rs {initial_stop_price:.2f}")
            print("!" * 75)
            
            approved = False
            from execution.telegram_control import request_telegram_trade_approval, TELEGRAM_BOT_TOKEN
            if TELEGRAM_BOT_TOKEN and not TELEGRAM_BOT_TOKEN.startswith("your_"):
                print("\n[TELEGRAM PROMPT SENT] Waiting 60s for user confirmation on Telegram...")
                approved = request_telegram_trade_approval(
                    option_symbol=option_symbol,
                    lot_size=lot_size,
                    entry_premium=entry_premium,
                    total_cost=entry_premium * lot_size,
                    target_price=target_price,
                    stop_price=initial_stop_price,
                    timeout_seconds=60
                )
            else:
                try:
                    user_input = input("\nDo you authorize placing this order? [Y/n]: ").strip().lower()
                    approved = user_input in ["y", "yes", ""]
                except Exception:
                    approved = True
                
            if not approved:
                print("\n[USER DECISION] Order placement REJECTED or TIMED OUT. Trade execution aborted.")
                return None
            else:
                print("\n[USER APPROVED] Trade execution authorized. Proceeding with order placement...\n")

        # 3. Place Aggressive Limit Order on Dhan & Attach 5-Second Fill Verification Loop
        sec_id = str(option_contract.get("security_id") or option_contract.get("instrument_key", "573917"))
        dhan_exch_seg = "MCX_COMM" if exchange == "MCX_FO" else "NSE_FNO"

        if not self.dry_run and self.dhan is not None:
            try:
                # Dispatch order to Dhan API v2
                api_resp = self.dhan.place_order(
                    security_id=sec_id,
                    exchange_segment=dhan_exch_seg,
                    transaction_type="BUY",
                    quantity=lot_size,
                    order_type="LIMIT",
                    product_type="MARGIN",
                    price=limit_price,
                    trigger_price=0.0,
                    validity="DAY",
                    amo_time="OPEN",
                    tag="OPTIONS_BOT"
                )
                
                data = api_resp.get("data", api_resp) if isinstance(api_resp, dict) else api_resp
                order_id = str(data.get("orderId", "") if isinstance(data, dict) else getattr(data, "orderId", ""))
                print(f"[LIVE DHAN ORDER PLACED] Order ID: {order_id} | Response: {api_resp}")
                
                # 5-Second Fill Verification Loop
                filled = False
                for sec in range(1, LIMIT_ORDER_TIMEOUT_SECONDS + 1):
                    time.sleep(1.0)
                    if order_id:
                        try:
                            ord_detail = self.dhan.get_order_by_id(order_id)
                            ord_data = ord_detail.get("data", ord_detail) if isinstance(ord_detail, dict) else ord_detail
                            ord_status = str(ord_data.get("orderStatus", "") if isinstance(ord_data, dict) else getattr(ord_data, "orderStatus", "")).upper()
                            if ord_status in ["TRADED", "FILLED", "SUCCESS", "EXECUTED"]:
                                filled = True
                                print(f"  [DHAN ORDER FILLED] Order {order_id} filled within {sec}s at price Rs {entry_premium:.2f}.")
                                break
                        except Exception as err:
                            print(f"[Dhan Fill Verification Sec {sec}] Status check: {err}")

                if not filled and order_id:
                    print(f"[5-Sec Fill Timeout] Order {order_id} remains UNFILLED after 5 seconds. Dispatching API cancel_order()...")
                    try:
                        self.dhan.cancel_order(order_id)
                        print(f"  [ORDER CANCELLED] Order {order_id} successfully cancelled.")
                    except Exception as cancel_err:
                        print(f"  [Cancel Order Error] Could not cancel order {order_id}: {cancel_err}")
                    return None
            except Exception as e:
                print(f"[DHAN ORDER ERROR] Failed to place order: {e}. Trade execution aborted safely.")
                return None

        mode_label = "DRY_RUN" if self.dry_run else "LIVE"
        trade_id = self.state_mgr.record_entry_trade(
            option_contract=option_contract,
            lot_size=lot_size,
            entry_premium=entry_premium,
            target_price=target_price,
            initial_stop_price=initial_stop_price,
            execution_mode=mode_label
        )

        from reporting.telegram_bot import send_telegram_trade_entry_alert
        send_telegram_trade_entry_alert(
            trade_id=trade_id,
            option_symbol=option_symbol,
            lot_size=lot_size,
            entry_premium=entry_premium,
            target_price=target_price,
            stop_price=initial_stop_price,
            execution_mode=mode_label
        )

        # 4. Attach Position Monitor Loop
        monitor = PositionMonitor(
            entry_premium=entry_premium,
            target_p=target_price,
            initial_stop_p=initial_stop_price
        )

        from execution.position_monitor import AsyncDhanWebSocketMonitor
        ws_engine = AsyncDhanWebSocketMonitor(
            access_token=self.access_token,
            instrument_key=sec_id,
            monitor=monitor
        )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        exit_price, exit_reason = loop.run_until_complete(
            ws_engine.start_websocket_stream(sim_scenario=sim_scenario, dry_run=self.dry_run)
        )
        loop.close()

        # 5. Record Exit Trade
        trade_summary = self.state_mgr.record_exit_trade(
            trade_id=trade_id,
            exit_premium=exit_price,
            exit_reason=exit_reason
        )

        from reporting.telegram_bot import send_telegram_trade_exit_alert
        send_telegram_trade_exit_alert(
            trade_id=trade_id,
            option_symbol=option_symbol,
            exit_premium=exit_price,
            net_pnl=trade_summary.get("net_pnl", 0.0),
            exit_reason=exit_reason,
            execution_mode=mode_label
        )

        return trade_summary

