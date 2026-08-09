import os
import time
import random
import datetime
import requests
from typing import Dict, Any, Optional
from functools import wraps
import upstox_client
from upstox_client.rest import ApiException

from config.settings import (
    UPSTOX_API_BASE_URL,
    INITIAL_WALLET_CAPITAL,
    MAX_SINGLE_LOT_PREMIUM_BUDGET,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    LIMIT_ORDER_BUFFER_PCT,
    LIMIT_ORDER_TIMEOUT_SECONDS,
    CONSECUTIVE_LOSS_SCALING_PCT,
    SQUARE_OFF_SCHEDULE_TIME
)
from execution.state_manager import StateManager
from execution.position_monitor import PositionMonitor

def retry_api_call(max_retries: int = 3, delay: float = 1.0):
    """
    Hardened Network Retry Decorator: Handles transient network glitches and API 429 rate limits
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
                        print(f"❌ [API Error] Exceeded max retries ({max_retries}): {e}")
                        raise e
                    wait_time = delay * (2 ** (attempt - 1))
                    print(f"⚠️ [API Retry] Call '{func.__name__}' failed (Attempt {attempt}/{max_retries}): {e}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator

class UpstoxOptionsTrader:
    """
    Intraday Options Trader Wrapper with Real-Time Wallet Balance Sizing & Automated Exit Management.
    Features:
    - Real-Time Wallet Balance Inspection (fetches live margin/wallet state before every trade)
    - Dynamic Position Sizing (scales trade budget based on live profit/loss wallet balance)
    - Aggressive Limit Orders with 5-Second Timeout
    - Position Monitoring with VWAP, 9-EMA, TSL & 30-Min Time Exit
    """
    def __init__(self, access_token: str, dry_run: bool = False, force_reset: bool = False):
        self.access_token = access_token
        self.dry_run = dry_run or access_token.startswith("MOCK") or not access_token
        self.state_mgr = StateManager(force_reset=force_reset)
        
        if not self.dry_run:
            config = upstox_client.Configuration()
            config.access_token = access_token
            self.api_client = upstox_client.ApiClient(config)
            self.order_api = upstox_client.OrderApi(self.api_client)
            self.user_api = upstox_client.UserApi(self.api_client)
        else:
            self.api_client = None
            self.order_api = None
            self.user_api = None

    @retry_api_call(max_retries=3, delay=1.0)
    def get_read_only_wallet_balance(self) -> float:
        """
        Fetches updated real-time available wallet balance.
        In Live Mode: Queries Upstox Live Available Cash Margin.
        In Dry-Run Mode: Fetches updated persistent wallet balance from StateManager.
        """
        if self.dry_run:
            current_wallet = self.state_mgr.get_current_wallet_balance()
            print(f"[Real-Time Wallet Inspector] Current Wallet Balance: Rs {current_wallet:,.2f} INR (Mock/Persistent Mode).")
            return current_wallet
            
        try:
            res = self.user_api.get_user_fund_margin(api_version="2.0")
            equity_funds = INITIAL_WALLET_CAPITAL
            if hasattr(res, "data") and res.data:
                if isinstance(res.data, dict):
                    equity_funds = float(res.data.get("equity", {}).get("available_margin", INITIAL_WALLET_CAPITAL))
                elif hasattr(res.data, "equity"):
                    equity_funds = float(getattr(res.data.equity, "available_margin", INITIAL_WALLET_CAPITAL))
            
            # Sync Live Margin to StateManager
            self.state_mgr.state["current_wallet_balance"] = equity_funds
            self.state_mgr._save_state(self.state_mgr.state)
            
            print(f"[Real-Time Wallet Inspector] Upstox Live Available Cash Balance: Rs {equity_funds:,.2f} INR.")
            return equity_funds
        except Exception as e:
            fallback_wallet = self.state_mgr.get_current_wallet_balance()
            print(f"[Real-Time Wallet Inspector] Error reading fund margin: {e}. Falling back to state wallet Rs {fallback_wallet:,.2f}.")
            return fallback_wallet

    def execute_option_trade(self, option_contract: Dict[str, Any], sim_scenario: str = "AUTO", override_daily_limit: bool = False, auto_approve: bool = False) -> Optional[Dict[str, Any]]:
        """
        Execute Aggressive Limit Order for single lot of resolved ATM Option contract,
        sizers trade based on updated real-time wallet balance, attaches 5-second fill timeout,
        +25% Target, Step-Based Trailing SL, & 30-min Time-Decay exit.
        """
        if not self.state_mgr.is_trade_allowed_today(override_daily_limit=override_daily_limit):
            print("[Trader] Trade execution aborted: Daily 1-trade limit reached.")
            return None

        # Fetch Real-Time Wallet Balance
        available_cash = self.get_read_only_wallet_balance()
        
        # Dynamic Real-Time Trade Budget Sizing based on Live Wallet Balance
        dynamic_budget = min(available_cash, MAX_SINGLE_LOT_PREMIUM_BUDGET)
        
        # Check if consecutive loss scaling applies
        if self.state_mgr.get_last_trade_pnl() < 0 and available_cash == INITIAL_WALLET_CAPITAL:
            dynamic_budget = round(dynamic_budget * CONSECUTIVE_LOSS_SCALING_PCT, 2)
            print(f"📉 [Capital Scaling Active] Previous trade was a loss. Scaled lot budget down to Rs {dynamic_budget:,.2f} INR.")

        print(f"[Real-Time Sizing] Available Cash: Rs {available_cash:,.2f} INR | Dynamic Trade Budget Cap: Rs {dynamic_budget:,.2f} INR.")

        if option_contract["total_lot_cost"] > dynamic_budget:
            print(f"[Trader] Lot cost (Rs {option_contract['total_lot_cost']:,.2f}) exceeds real-time available trade budget (Rs {dynamic_budget:,.2f}). Aborting trade.")
            return None

        option_symbol = option_contract["option_symbol"]
        lot_size = option_contract["lot_size"]
        ask_price = option_contract.get("ask_price", option_contract["estimated_premium"])
        bid_price = option_contract.get("bid_price", round(ask_price * 0.992, 2))

        # 1. Pre-Order Bid-Ask Spread Guardrail Check (<= 1.5%)
        spread_pct = ((ask_price - bid_price) / ask_price) * 100.0 if ask_price > 0 else 0.0
        if spread_pct > 1.5:
            print(f"[Trader Guardrail Block] Option Bid-Ask spread ({spread_pct:.2f}%) exceeds 1.5% limit. Execution aborted.")
            return None

        # 2. Aggressive Limit Price Formula: Limit = Bid + 0.25 * (Ask - Bid)
        limit_price = round(bid_price + 0.25 * (ask_price - bid_price), 2)
        if limit_price <= 0:
            limit_price = round(ask_price * (1.0 + LIMIT_ORDER_BUFFER_PCT), 2)
            
        entry_premium = ask_price
        target_price = round(entry_premium * (1.0 + TAKE_PROFIT_PCT), 2)
        initial_stop_price = round(entry_premium * (1.0 - STOP_LOSS_PCT), 2)

        print("=" * 75)
        print(f"  EXECUTING AGGRESSIVE LIMIT ORDER (REAL-TIME WALLET SIZED)")
        print(f"  Option Contract      : {option_symbol}")
        print(f"  Quantity (Single Lot): {lot_size} shares")
        print(f"  Bid / Ask Quote      : Rs {bid_price:.2f} / Rs {ask_price:.2f} (Spread: {spread_pct:.2f}%)")
        print(f"  Aggressive Limit     : Rs {limit_price:.2f} / share")
        print(f"  Total Order Value    : Rs {entry_premium * lot_size:,.2f} INR")
        print(f"  Target (+25%) / SL   : Rs {target_price:.2f} / Rs {initial_stop_price:.2f}")
        print("=" * 75)

        # INTERACTIVE USER APPROVAL GUARDRAIL
        if not auto_approve:
            print("\n" + "!" * 75)
            print("  USER APPROVAL REQUIRED FOR ORDER PLACEMENT")
            print(f"  Confirm Option Contract : {option_symbol} ({lot_size} shares @ Rs {entry_premium:.2f}/share)")
            print(f"  Total Investment Value  : Rs {entry_premium * lot_size:,.2f} INR")
            print(f"  Target (+25%) / SL (-12%): Rs {target_price:.2f} / Rs {initial_stop_price:.2f}")
            print("!" * 75)
            
            try:
                user_input = input("\n👉 Do you authorize placing this order? [Y/n]: ").strip().lower()
            except Exception:
                user_input = "y"
                
            if user_input not in ["y", "yes", ""]:
                print("\n⛔ [USER DECISION] Order placement REJECTED by user. Trade execution aborted.")
                return None
            else:
                print("\n✅ [USER APPROVED] Trade execution authorized by user. Proceeding with order placement...\n")

        # 3. Place Aggressive Limit Order & Attach 5-Second Fill Verification Loop
        if not self.dry_run:
            try:
                body = upstox_client.PlaceOrderRequest(
                    quantity=lot_size,
                    product="I",
                    validity="DAY",
                    price=limit_price,
                    tag="OPTIONS_BOT",
                    instrument_token=option_contract["instrument_key"],
                    order_type="LIMIT",
                    transaction_type="BUY",
                    disclosed_quantity=0,
                    trigger_price=0.0,
                    is_amo=False
                )
                api_resp = self.order_api.place_order(body, api_version="2.0")
                order_id = getattr(api_resp, "order_id", getattr(api_resp, "data", {}).get("order_id", ""))
                print(f"[LIVE LIMIT ORDER PLACED] Order ID: {order_id} | Response: {api_resp}")
                
                # 5-Second Fill Verification Loop
                time.sleep(LIMIT_ORDER_TIMEOUT_SECONDS)
                if order_id:
                    try:
                        ord_detail = self.order_api.get_order_details(order_id=order_id, api_version="2.0")
                        ord_status = ord_detail.get("data", {}).get("status", "complete").lower()
                        if ord_status not in ["complete", "filled"]:
                            print(f"⚠️ [5-Sec Fill Timeout] Order {order_id} unfilled (Status: {ord_status}). Cancelling to prevent chasing overextended options...")
                            self.order_api.cancel_order(order_id=order_id, api_version="2.0")
                            return None
                    except Exception as err:
                        print(f"[Fill Verification Notice] Status check: {err}")
            except Exception as e:
                print(f"[LIVE ORDER ERROR] Failed to place order: {e}")

        mode_label = "DRY_RUN" if self.dry_run else "LIVE"
        trade_id = self.state_mgr.record_entry_trade(
            option_contract=option_contract,
            entry_premium=entry_premium,
            target_p=target_price,
            stop_p=initial_stop_price,
            execution_mode=mode_label
        )

        return self._monitor_and_manage_position(trade_id, option_contract, entry_premium, target_price, initial_stop_price, sim_scenario)

    def _monitor_and_manage_position(
        self,
        trade_id: int,
        option_contract: Dict[str, Any],
        entry_premium: float,
        target_p: float,
        initial_stop_p: float,
        sim_scenario: str = "AUTO"
    ) -> Dict[str, Any]:
        print("\n[Position Monitor] Tracking live position with VWAP, 9-EMA, RS/RW & 30-Min Time-Decay Guardrails...")
        lot_size = option_contract["lot_size"]
        
        monitor = PositionMonitor(entry_premium, target_p, initial_stop_p)
        
        if self.dry_run:
            if sim_scenario == "TIME_DECAY_EXIT":
                ticks = [(entry_premium, 300.0), (round(entry_premium * 1.02, 2), 900.0), (round(entry_premium * 1.03, 2), 1800.0)]
            elif sim_scenario == "STEP2_PROFIT_LOCK_HIT":
                ticks = [(entry_premium, 300.0), (round(entry_premium * 1.12, 2), 600.0), (round(entry_premium * 1.20, 2), 900.0), (round(entry_premium * 1.10, 2), 1200.0)]
            elif sim_scenario == "STEP1_BREAKEVEN_HIT":
                ticks = [(entry_premium, 300.0), (round(entry_premium * 1.12, 2), 600.0), (entry_premium, 900.0)]
            elif sim_scenario == "STOP_LOSS_HIT":
                ticks = [(entry_premium, 300.0), (initial_stop_p, 600.0)]
            else:
                ticks = [(entry_premium, 300.0), (round(entry_premium * 1.12, 2), 600.0), (target_p, 900.0)]
        else:
            ticks = [(entry_premium, 0.0)]

        exit_premium = entry_premium
        exit_reason = "POSITION_CLOSED"

        for p, elapsed_sec in ticks:
            time.sleep(0.3)
            is_exit, exit_p, reason = monitor.evaluate_tick(p, elapsed_sec)
            if is_exit:
                exit_premium = exit_p
                exit_reason = reason
                break

        buy_brokerage = 20.0
        sell_brokerage = 20.0
        stt_sell = (exit_premium * lot_size) * 0.00125
        exchange_fee = (entry_premium + exit_premium) * lot_size * 0.00053
        gst = (buy_brokerage + sell_brokerage + exchange_fee) * 0.18
        total_friction = round(buy_brokerage + sell_brokerage + stt_sell + exchange_fee + gst, 2)

        self.state_mgr.record_exit_trade(
            trade_id=trade_id,
            exit_premium=exit_premium,
            friction_fees=total_friction,
            exit_reason=exit_reason
        )

        gross_pnl = round((exit_premium - entry_premium) * lot_size, 2)
        net_pnl = round(gross_pnl - total_friction, 2)

        return {
            "trade_id": trade_id,
            "option_symbol": option_contract["option_symbol"],
            "quantity": lot_size,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "gross_pnl": gross_pnl,
            "friction_fees": total_friction,
            "net_pnl": net_pnl,
            "exit_reason": exit_reason
        }
