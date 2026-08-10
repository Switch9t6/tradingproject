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
    MICRO_CAPITAL_BUDGET_CAP,
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
            data = getattr(res, "data", res)
            eq = data.get("equity", {}) if isinstance(data, dict) else getattr(data, "equity", {})
            if isinstance(eq, dict):
                margin = eq.get("available_margin")
            else:
                margin = getattr(eq, "available_margin", None)
            
            if margin is not None and float(margin) > 0:
                equity_funds = float(margin)
            
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
        exchange = option_contract.get("exchange", "NSE_FO")
        if (
            option_contract.get("is_mcx")
            or "MCX" in str(exchange).upper()
            or "CRUDE" in str(option_contract.get("underlying_symbol")).upper()
            or "CRUDE" in str(option_contract.get("option_symbol")).upper()
        ):
            exchange = "MCX_FO"

        if not self.state_mgr.is_trade_allowed_today(exchange=exchange, override_daily_limit=override_daily_limit):
            print(f"[Trader] Trade execution aborted: Session cap of 1 trade per day reached for {exchange}.")
            return None

        # Fetch Real-Time Wallet Balance
        available_cash = self.get_read_only_wallet_balance()
        
        # Dynamic Real-Time Trade Budget Sizing based 100% on Live Wallet Balance (or micro-capital cap)
        dynamic_budget = MICRO_CAPITAL_BUDGET_CAP if (locals().get("micro_capital") or getattr(self, "micro_capital", False)) else available_cash
        
        # Check if consecutive loss scaling applies
        if self.state_mgr.get_last_trade_pnl() < 0 and available_cash == INITIAL_WALLET_CAPITAL:
            dynamic_budget = round(dynamic_budget * CONSECUTIVE_LOSS_SCALING_PCT, 2)
            print(f"  [Capital Scaling Active] Previous trade was a loss. Scaled lot budget down to Rs {dynamic_budget:,.2f} INR.")

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

        # 2. Limit Price set to Ask Price for instant execution fill
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
            print(f"[Net Expectancy Guardrail Block] Expected Net Target PnL (Rs {expected_target_net_pnl:.2f}) < Min Friction Multiple (Rs {min_required_net_pnl:.2f}). Friction drag too high for lot size. Order aborted.")
            return None

        print("=" * 75)
        print(f"  EXECUTING AGGRESSIVE LIMIT ORDER (REAL-TIME WALLET SIZED)")
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

        # 3. Place Aggressive Limit Order & Attach 5-Second Fill Verification Loop
        if not self.dry_run:
            try:
                body = upstox_client.PlaceOrderRequest(
                    quantity=lot_size,
                    product="D",
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
                data = getattr(api_resp, "data", api_resp)
                if isinstance(data, dict):
                    order_id = str(data.get("order_id", ""))
                else:
                    order_id = str(getattr(data, "order_id", getattr(api_resp, "order_id", "")))
                print(f"[LIVE LIMIT ORDER PLACED] Order ID: {order_id} | Response: {api_resp}")
                
                # 5-Second Fill Verification Loop (polling status every 1 second for up to 5 seconds)
                filled = False
                for sec in range(1, LIMIT_ORDER_TIMEOUT_SECONDS + 1):
                    time.sleep(1.0)
                    if order_id:
                        try:
                            ord_detail = self.order_api.get_order_details(order_id=order_id, api_version="2.0")
                            data = ord_detail.data if hasattr(ord_detail, "data") else ord_detail
                            ord_status = str(data.get("status", "") if isinstance(data, dict) else getattr(data, "status", "")).lower()
                            if ord_status in ["complete", "filled"]:
                                filled = True
                                entry_premium = self.reconcile_exact_order_fill_price(order_id, fallback_price=entry_premium)
                                print(f"  [ORDER FILLED] Order {order_id} filled within {sec}s at exact price Rs {entry_premium:.2f}.")
                                break
                        except Exception as err:
                            print(f"[Fill Verification Sec {sec}] Status check: {err}")

                if not filled and order_id:
                    print(f"[5-Sec Fill Timeout] Order {order_id} remains UNFILLED after 5 seconds. Dispatching API cancel_order()...")
                    try:
                        self.order_api.cancel_order(order_id=order_id, api_version="2.0")
                        print(f"  [ORDER CANCELLED] Order {order_id} successfully cancelled. Preventing chasing overextended options.")
                    except Exception as cancel_err:
                        print(f"  [Cancel Order Error] Could not cancel order {order_id}: {cancel_err}")
                    return None
            except Exception as e:
                err_str = str(e)
                if "UDAPI1154" in err_str or "static IP" in err_str:
                    print("\n" + "!" * 80)
                    print("  [WARNING] UPSTOX API STATIC IP RESTRICTION (UDAPI1154)")
                    print("  Upstox server rejected order placement because local origin IP is not whitelisted.")
                    print("  Whitelisted Static IP : 110.226.176.243 (Railway Cloud Daemon)")
                    print("  Current Request IP    : 2401:4900:1c97:8e85:f5b3:1a91:c37e:a90c")
                    print("  Action Options        : 1. Deploy & run on Railway where origin IP matches 110.226.176.243.")
                    print("                          2. Or run without --live (Simulation Dry-Run Mode).")
                    print("!" * 80 + "\n")
                else:
                    print(f"[LIVE ORDER ERROR] Failed to place order: {e}. Trade execution aborted safely.")
                return None

        mode_label = "DRY_RUN" if self.dry_run else "LIVE"
        trade_id = self.state_mgr.record_entry_trade(
            option_contract=option_contract,
            entry_premium=entry_premium,
            target_p=target_price,
            stop_p=initial_stop_price,
            execution_mode=mode_label,
            exchange=exchange
        )

        # Trigger Telegram Trade Entry Alert
        from reporting.telegram_bot import send_trade_entry_alert, send_trade_exit_alert
        send_trade_entry_alert({
            "option_symbol": option_symbol,
            "lot_size": lot_size,
            "entry_premium": entry_premium,
            "target_price": target_price,
            "initial_stop_loss": initial_stop_price,
            "composite_score": option_contract.get("composite_rating", {}).get("composite_score", 80.0),
            "execution_mode": mode_label
        }, wallet_balance=available_cash)

        result = self._monitor_and_manage_position(trade_id, option_contract, entry_premium, target_price, initial_stop_price, sim_scenario)

        # Trigger Telegram Trade Exit Alert
        send_trade_exit_alert({
            "option_symbol": option_symbol,
            "entry_premium": entry_premium,
            "exit_premium": result.get("exit_premium", entry_premium),
            "net_pnl": result.get("net_pnl", 0.0),
            "exit_reason": result.get("exit_reason", "EXIT"),
            "execution_mode": mode_label
        }, wallet_balance=self.state_mgr.get_current_wallet_balance())

        return result

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

            for p, elapsed_sec in ticks:
                time.sleep(0.3)
                is_exit, exit_p, reason = monitor.evaluate_tick(p, elapsed_sec)
                if is_exit:
                    exit_premium = exit_p
                    exit_reason = reason
                    break
        else:
            # LIVE PRODUCTION REAL-TIME 2-SECOND POLLING LOOP
            print(f"[Live Position Monitor] Polling live market ticks for {option_contract['option_symbol']} every 2 seconds...")
            start_time = time.time()
            instrument_key = option_contract.get("instrument_key", "")
            quote_api = upstox_client.MarketQuoteApi(self.api_client)
            
            while True:
                time.sleep(2.0)
                elapsed_sec = time.time() - start_time
                current_ltp = entry_premium
                
                try:
                    res = quote_api.get_full_market_quote(symbol=instrument_key, api_version="2.0")
                    d_map = res.data if hasattr(res, "data") else {}
                    q_item = list(d_map.values())[0] if d_map else None
                    if q_item:
                        current_ltp = float(getattr(q_item, "last_price", entry_premium))
                except Exception as poll_err:
                    print(f"[Live Tick Poll Warning] Could not fetch live quote: {poll_err}")

                is_exit, exit_p, reason = monitor.evaluate_tick(current_ltp, elapsed_sec)
                print(f"  [Live Position Tick] LTP: Rs {current_ltp:.2f} | Elapsed: {int(elapsed_sec)}s | Trailing SL: Rs {monitor.current_stop_p:.2f}")

                if is_exit:
                    exit_premium = exit_p
                    exit_reason = reason
                    print(f"\n[LIVE EXIT SIGNAL TRIGGERED] Reason: {exit_reason} | Exit LTP: Rs {exit_premium:.2f}")
                    return self.execute_exit_sell_order(
                        trade_id=trade_id,
                        instrument_key=instrument_key,
                        option_symbol=option_contract["option_symbol"],
                        quantity=lot_size,
                        entry_premium=entry_premium,
                        exit_reason=exit_reason
                    )

        from reporting.friction_calculator import calculate_trade_friction
        f_res = calculate_trade_friction(lot_size, entry_premium, exit_premium)
        gross_pnl = f_res["gross_pnl"]
        total_friction = f_res["total_friction"]
        net_pnl = f_res["net_pnl"]

        self.state_mgr.record_exit_trade(
            trade_id=trade_id,
            exit_premium=exit_premium,
            friction_fees=total_friction,
            exit_reason=exit_reason
        )

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

    def execute_exit_sell_order(
        self,
        trade_id: int,
        instrument_key: str,
        option_symbol: str,
        quantity: int,
        entry_premium: float,
        exit_reason: str = "MANUAL"
    ) -> Dict[str, Any]:
        """
        Executes a real SELL MARKET exit order on Upstox API v2, verifies order status,
        calculates friction costs, updates StateManager & SQLite trades.db, and sends Telegram alert.
        """
        print(f"\n==========================================================================")
        print(f"  EXECUTING LIVE SELL MARKET ORDER FOR EXIT")
        print(f"  Contract Symbol  : {option_symbol}")
        print(f"  Instrument Key   : {instrument_key}")
        print(f"  Quantity         : {quantity} shares")
        print(f"  Exit Reason      : {exit_reason}")
        print(f"==========================================================================")

        exit_premium = entry_premium
        order_id = ""

        if not self.dry_run:
            try:
                try:
                    quote_api = upstox_client.MarketQuoteApi(self.api_client)
                    res = quote_api.get_full_market_quote(symbol=instrument_key, api_version="2.0")
                    d_map = res.data if hasattr(res, "data") else {}
                    q_item = list(d_map.values())[0] if d_map else None
                    if q_item:
                        exit_premium = float(getattr(q_item, "last_price", entry_premium))
                except Exception as q_err:
                    print(f"  [Exit Quote Notice] {q_err}")

                sell_body = upstox_client.PlaceOrderRequest(
                    quantity=quantity,
                    product="I",
                    validity="DAY",
                    price=0.0,
                    tag="OPTIONS_BOT",
                    instrument_token=instrument_key,
                    order_type="MARKET",
                    transaction_type="SELL",
                    disclosed_quantity=0,
                    trigger_price=0.0,
                    is_amo=False
                )
                api_resp = self.order_api.place_order(sell_body, api_version="2.0")
                resp_data = getattr(api_resp, "data", api_resp)
                if isinstance(resp_data, dict):
                    order_id = str(resp_data.get("order_id", ""))
                else:
                    order_id = str(getattr(resp_data, "order_id", getattr(api_resp, "order_id", "")))

                print(f"  [LIVE SELL ORDER DISPATCHED] Order ID: {order_id} | Response: {api_resp}")

                if order_id:
                    exit_premium = self.reconcile_exact_order_fill_price(order_id, fallback_price=exit_premium)
            except Exception as e:
                print(f"[LIVE SELL ORDER ERROR] {e}")

        from reporting.friction_calculator import calculate_trade_friction
        f_res = calculate_trade_friction(quantity, entry_premium, exit_premium)
        gross_pnl = f_res["gross_pnl"]
        total_friction = f_res["total_friction"]
        net_pnl = f_res["net_pnl"]

        if trade_id > 0:
            self.state_mgr.record_exit_trade(
                trade_id=trade_id,
                exit_premium=exit_premium,
                friction_fees=total_friction,
                exit_reason=exit_reason
            )

        mode_label = "DRY_RUN" if self.dry_run else "LIVE"
        from reporting.telegram_bot import send_trade_exit_alert
        send_trade_exit_alert({
            "option_symbol": option_symbol,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "net_pnl": net_pnl,
            "exit_reason": exit_reason,
            "execution_mode": mode_label
        }, wallet_balance=self.state_mgr.get_current_wallet_balance())

        return {
            "trade_id": trade_id,
            "order_id": order_id,
            "option_symbol": option_symbol,
            "quantity": quantity,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "gross_pnl": gross_pnl,
            "friction_fees": total_friction,
            "net_pnl": net_pnl,
            "exit_reason": exit_reason
        }

    def reconcile_exact_order_fill_price(self, order_id: str, fallback_price: float, max_attempts: int = 5) -> float:
        """
        Reconciles exact fill price from Upstox Trade Book (get_trades_by_order & get_order_details).
        Computes volume-weighted average fill price (VWAP) across all executed partial fills.
        Falls back to fallback_price (LTP estimate) if trade book has not yet populated.
        """
        if self.dry_run or not order_id:
            return fallback_price

        print(f"  [Trade Book Reconciliation] Polling exact fill price for Order ID: {order_id}...")
        for attempt in range(1, max_attempts + 1):
            time.sleep(1.0)
            
            # Attempt 1: Query exact trade fills for order ID via get_trades_by_order
            try:
                t_resp = self.order_api.get_trades_by_order(order_id=order_id, api_version="2.0")
                t_data = t_resp.data if hasattr(t_resp, "data") else t_resp
                trades_list = t_data if isinstance(t_data, list) else []
                if trades_list:
                    tot_qty = 0
                    tot_val = 0.0
                    for tr in trades_list:
                        q = int(tr.get("quantity", 0) if isinstance(tr, dict) else getattr(tr, "quantity", 0))
                        p = float(tr.get("average_price", 0.0) if isinstance(tr, dict) else getattr(tr, "average_price", 0.0))
                        if q > 0 and p > 0:
                            tot_qty += q
                            tot_val += (q * p)
                    if tot_qty > 0:
                        vwap_fill = round(tot_val / tot_qty, 2)
                        print(f"  ✅ [EXACT TRADE BOOK FILL CONFIRMED] Order {order_id} fill VWAP: Rs {vwap_fill:.2f} (Filled Qty: {tot_qty})")
                        return vwap_fill
            except Exception:
                pass

            # Attempt 2: Fallback query via get_order_details
            try:
                ord_detail = self.order_api.get_order_details(order_id=order_id, api_version="2.0")
                d = ord_detail.data if hasattr(ord_detail, "data") else ord_detail
                avg_p = float(d.get("average_price", 0.0) if isinstance(d, dict) else getattr(d, "average_price", 0.0))
                status = str(d.get("status", "") if isinstance(d, dict) else getattr(d, "status", "")).lower()
                if avg_p > 0 and status in ["complete", "filled"]:
                    print(f"  ✅ [ORDER DETAILS FILL CONFIRMED] Order {order_id} avg price: Rs {avg_p:.2f} (Status: {status})")
                    return round(avg_p, 2)
            except Exception:
                pass

        print(f"  ⚠️ [Trade Book Reconcile Warning] Could not fetch exact trade book fill after {max_attempts}s. Using fallback estimate: Rs {fallback_price:.2f}")
        return fallback_price
