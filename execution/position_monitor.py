import os
import sys
import time
import asyncio
import threading
import datetime
import requests
from typing import Tuple, Dict, Any, Optional

from config.settings import (
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    USE_STEP_TRAILING_STOP_LOSS,
    TSL_STEP1_TRIGGER_PCT,
    TSL_STEP1_LOCK_PCT,
    TSL_STEP2_TRIGGER_PCT,
    TSL_STEP2_LOCK_PCT,
    MAX_HOLD_SECONDS,
    MIN_GAIN_REQUIRED_AT_30M
)

class PositionMonitor:
    """
    Monitors active option position in real-time enforcing:
    1. Target Hit (+25% Gain Exit).
    2. Step-Based Trailing Stop Loss (Breakeven @ +10%, Lock +10% @ +18%).
    3. Base Stop Loss (-12% Loss Exit).
    4. 30-Minute Time-Decay Stagnation Exit.
    """
    def __init__(self, entry_premium: float, target_p: float, initial_stop_p: float):
        self.entry_premium = entry_premium
        self.target_p = target_p
        self.initial_stop_p = initial_stop_p
        self.current_stop_p = initial_stop_p
        self.highest_price_seen = entry_premium
        self.entry_time = time.time()

    def evaluate_tick(self, current_price: float, elapsed_seconds: float) -> Tuple[bool, float, str]:
        """
        Evaluates current option price tick and trade duration.
        Returns (is_exit_triggered, exit_price, exit_reason).
        """
        try:
            if current_price is None or current_price <= 0:
                print("⚠️ [Position Monitor Warning] Received invalid tick price. Retaining previous state.")
                return False, self.highest_price_seen, ""

            # -------------------------------------------------------------
            # A. Step-Based Trailing Stop Loss
            # -------------------------------------------------------------
            if current_price > self.highest_price_seen:
                self.highest_price_seen = current_price
                
                # Step 1: Move to Breakeven once gain reaches +10%
                if self.highest_price_seen >= self.entry_premium * (1.0 + TSL_STEP1_TRIGGER_PCT) and self.current_stop_p < self.entry_premium:
                    prev_sl = self.current_stop_p
                    self.current_stop_p = self.entry_premium
                    print(f"  [TSL ADJUSTED] Gain hit +10% (Rs {current_price:.2f}). SL moved to BREAKEVEN (Rs {self.current_stop_p:.2f}).")
                    
                # Step 2: Lock +10% Profit once gain reaches +18%
                elif self.highest_price_seen >= self.entry_premium * (1.0 + TSL_STEP2_TRIGGER_PCT) and self.current_stop_p < self.entry_premium * (1.0 + TSL_STEP2_LOCK_PCT):
                    prev_sl = self.current_stop_p
                    self.current_stop_p = self.entry_premium * (1.0 + TSL_STEP2_LOCK_PCT)
                    print(f"  [TSL ADJUSTED] Gain hit +18% (Rs {current_price:.2f}). SL moved to LOCK PROFIT @ +10% (Rs {self.current_stop_p:.2f}).")

            # -------------------------------------------------------------
            # B. Exit Decision Logic
            # -------------------------------------------------------------

            # Exit 1: Target Hit (+25%)
            if current_price >= self.target_p:
                print(f"  [TARGET HIT] Exiting at Rs {current_price:.2f} (+25.00% Gain).")
                return True, self.target_p, "TARGET_HIT_+25%"

            # Exit 2: Stop Loss Hit (Base -12% or Trailing SL)
            if current_price <= self.current_stop_p:
                sl_gain_pct = ((self.current_stop_p - self.entry_premium) / self.entry_premium) * 100.0
                if self.current_stop_p > self.initial_stop_p:
                    if sl_gain_pct > 0:
                        reason = "TSL_STEP2_PROFIT_LOCK_HIT"
                        print(f"  [STEP-2 TSL HIT] Exiting at Rs {current_price:.2f} (+10% Profit Locked).")
                    else:
                        reason = "TSL_STEP1_BREAKEVEN_HIT"
                        print(f"  [STEP-1 TSL HIT] Exiting at Rs {current_price:.2f} (Breakeven Protected).")
                else:
                    reason = "STOP_LOSS_HIT_-12%"
                    print(f"  [STOP LOSS HIT] Exiting at Rs {current_price:.2f} (SL Level: Rs {self.current_stop_p:.2f}).")
                return True, self.current_stop_p, reason

            # Exit 3: 30-MINUTE TIME-DECAY STAGNATION EXIT
            if elapsed_seconds >= MAX_HOLD_SECONDS:
                gain_ratio = current_price / self.entry_premium
                if gain_ratio < MIN_GAIN_REQUIRED_AT_30M:
                    net_change_pct = (gain_ratio - 1.0) * 100
                    print(f"  [30-MIN TIME EXIT] Trade stagnant ({net_change_pct:+.2f}% in 30 mins < +5%). Exiting to prevent Theta Decay!")
                    return True, current_price, "TIME_EXIT_30MIN_STAGNANT"

            return False, current_price, ""
        except Exception as e:
            print(f"⚠️ [Position Monitor Error] Error evaluating tick: {e}. Retaining active position.")
            return False, self.highest_price_seen, ""

class AsyncUpstoxMarketFeedMonitor:
    """
    ASYNC UPSTOX MARKET FEED TICK STREAMING ENGINE:
    Streams live tick data for active option contracts via Upstox Market Quote API
    with exponential backoff auto-reconnect handling.
    """
    def __init__(self, access_token: str, instrument_key: str, monitor: PositionMonitor):
        self.access_token = access_token
        self.instrument_key = instrument_key
        self.monitor = monitor
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnects = 5

    async def start_websocket_stream(self, sim_scenario: str = "AUTO", dry_run: bool = False) -> Tuple[float, str]:
        """
        Connects to Upstox Market Feed / Quote Polling Stream, evaluates ticks asynchronously,
        and reconnects automatically with exponential backoff if network drops.
        """
        print(f"\n[Upstox Feed Engine] Launching Async Live Feed Stream for Instrument Key '{self.instrument_key}'...")
        self.is_connected = True
        start_time = time.time()

        while self.is_connected:
            try:
                # Simulation / Fallback Async Tick Stream Loop
                if dry_run or not self.access_token or self.access_token.startswith("MOCK") or self.access_token.startswith("your_"):
                    await asyncio.sleep(0.5)
                    elapsed = time.time() - start_time
                    
                    if sim_scenario == "STEP1_BREAKEVEN_HIT":
                        mock_ltp = self.monitor.entry_premium * (1.11 if elapsed < 3 else 0.99)
                    elif sim_scenario == "STEP2_PROFIT_LOCK_HIT":
                        mock_ltp = self.monitor.entry_premium * (1.19 if elapsed < 3 else 1.08)
                    elif sim_scenario == "STOP_LOSS_HIT":
                        mock_ltp = self.monitor.entry_premium * 0.86
                    elif sim_scenario == "TIME_DECAY_EXIT":
                        mock_ltp = self.monitor.entry_premium * 1.02
                        elapsed = 1805 # > 30 minutes
                    else: # TARGET_HIT default
                        mock_ltp = self.monitor.entry_premium * (1.10 if elapsed < 1.5 else (1.18 if elapsed < 3.0 else 1.25))
                    
                    is_exit, exit_p, reason = self.monitor.evaluate_tick(mock_ltp, elapsed)
                    if is_exit:
                        self.is_connected = False
                        return exit_p, reason
                else:
                    # Live Upstox Market Quote Polling Loop (2-Second LTP Intervals)
                    await asyncio.sleep(2.0)
                    elapsed = time.time() - start_time
                    
                    current_ltp = self.monitor.highest_price_seen
                    try:
                        import upstox_client
                        configuration = upstox_client.Configuration()
                        configuration.access_token = self.access_token
                        mq_api = upstox_client.MarketQuoteApi(upstox_client.ApiClient(configuration))
                        res = mq_api.ltp(symbol=self.instrument_key, api_version="2.0")
                        data = getattr(res, "data", res)
                        if isinstance(data, dict):
                            item = data.get(self.instrument_key, {})
                            current_ltp = float(item.get("last_price") or item.get("ltp") or self.monitor.highest_price_seen)
                    except Exception as poll_err:
                        print(f"  [Upstox LTP Feed Query Notice] {poll_err}")
                    
                    is_exit, exit_p, reason = self.monitor.evaluate_tick(current_ltp, elapsed)
                    if is_exit:
                        self.is_connected = False
                        return exit_p, reason

            except Exception as e:
                self.reconnect_attempts += 1
                backoff_delay = min(2.0 ** self.reconnect_attempts, 30.0)
                print(f"[Upstox Feed Warning] Stream connection dropped ({e}). Attempting Exponential Backoff Reconnect {self.reconnect_attempts}/{self.max_reconnects} in {backoff_delay:.1f}s...")
                if self.reconnect_attempts > self.max_reconnects:
                    print("❌ [Upstox Feed Error] Max reconnect attempts exceeded. Falling back to emergency exit level.")
                    return self.monitor.current_stop_p, "WEBSOCKET_DISCONNECT_EMERGENCY_EXIT"
                await asyncio.sleep(backoff_delay)

        return self.monitor.entry_premium, "MONITOR_STOPPED"


# ---------------------------------------------------------------------------
# LIVE FYERS POSITION MONITOR
# ---------------------------------------------------------------------------
IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_POLL_INTERVAL_SECONDS = 5.0
# Hand the position back to the EOD square-off once these times are reached.
_MONITOR_CUTOFFS = {
    "NSE_FO": datetime.time(15, 15),
    "MCX_FO": datetime.time(23, 0),
}


def _monitor_cutoff_time(exchange: str) -> datetime.time:
    return _MONITOR_CUTOFFS.get(str(exchange).upper(), datetime.time(23, 0))


def _get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(IST_TZ)


def _build_fyers_client(access_token: str):
    """Creates a lightweight Fyers client for LTP polling (or None in dry/MOCK mode)."""
    if not access_token or access_token.startswith("MOCK"):
        return None
    try:
        from fyers_apiv3 import fyersModel
        from config.settings import FYERS_APP_ID, TOKEN_FILE_PATH
        app_id = (FYERS_APP_ID or "").strip()
        if not app_id:
            return None
        return fyersModel.FyersModel(
            client_id=app_id,
            token=access_token,
            is_async=False,
            log_path=os.path.dirname(TOKEN_FILE_PATH),
        )
    except Exception as e:
        print(f"  [Position Monitor Client Notice] {e}")
        return None


def _fetch_ltp(fyers_client, symbol: str) -> float:
    """Polls live LTP for the held symbol. Returns 0.0 on failure (treated as invalid tick)."""
    if fyers_client is None:
        return 0.0
    try:
        q = fyers_client.quotes(data={"symbols": symbol})
        if isinstance(q, dict) and q.get("s") == "ok" and q.get("d"):
            return float(q["d"][0].get("v", {}).get("lp", 0) or 0)
    except Exception as e:
        print(f"  [Position Monitor LTP Notice] {e}")
    return 0.0


def _execute_exit(symbol: str, quantity: int, trade_id: str, tick_size: float,
                  access_token: str, dry_run: bool, exit_price: float, reason: str) -> bool:
    """
    Places the SELL exit for a triggered TSL/target/stop, records the closed trade,
    and notifies Telegram. Retries once if the first exit order stays PENDING.
    """
    from execution.fyers_trader import place_aggressive_limit_order
    from execution.state_manager import StateManager

    res = None
    for attempt in range(1, 3):
        res = place_aggressive_limit_order(
            symbol=symbol,
            quantity=quantity,
            transaction_type="SELL",
            product_type="INTRADAY",
            limit_price=exit_price,
            dry_run=dry_run,
            access_token=access_token,
            tick_size=tick_size,
            fill_timeout_seconds=10,
        )
        if res.get("status") == "TRADED":
            filled = float(res.get("filled_price") or exit_price)
            StateManager().record_exit_trade(trade_id=trade_id, exit_premium=filled, exit_reason=reason)
            print(f"  [Position Monitor] EXITED #{trade_id}: {reason} @ Rs {filled:.2f} "
                  f"(order {res.get('order_id')})")
            try:
                from reporting.telegram_bot import send_telegram_message
                send_telegram_message(
                    f"💰 <b>[POSITION EXIT - {reason}]</b>\n"
                    f"<b>Symbol :</b> <code>{symbol}</code>\n"
                    f"<b>Qty    :</b> {quantity}\n"
                    f"<b>Exit   :</b> Rs {filled:.2f}\n"
                    f"<b>Order  :</b> <code>{res.get('order_id')}</code>"
                )
            except Exception as notify_err:
                print(f"  [Position Monitor Notify Notice] {notify_err}")
            return True

        if res.get("status") == "PENDING":
            print(f"  [Position Monitor] Exit order pending (attempt {attempt}). Retrying in 5s...")
            time.sleep(5)
            continue

        # REJECTED
        print(f"  [Position Monitor] Exit order rejected: {res.get('status')} -> {res.get('remarks')}")
        break

    try:
        from reporting.telegram_bot import send_telegram_message
        send_telegram_message(
            f"⚠️ <b>[POSITION EXIT FAILED]</b>\n"
            f"<b>Symbol :</b> <code>{symbol}</code>\n"
            f"<b>Reason :</b> {reason}\n"
            f"<b>Status :</b> {res.get('status') if res else 'N/A'} -> "
            f"{res.get('remarks') if res else 'unknown'}\n"
            "Position remains OPEN. EOD square-off will retry at the close."
        )
    except Exception as notify_err:
        print(f"  [Position Monitor Notify Notice] {notify_err}")
    return False


def start_position_monitor(symbol: str, quantity: int, trade_id: str, entry_premium: float,
                           target_p: float, stop_p: float, tick_size: float = 0.05,
                           access_token: str = None, dry_run: bool = False,
                           exchange: str = "NSE_FO",
                           poll_interval: float = _POLL_INTERVAL_SECONDS) -> Optional[threading.Thread]:
    """
    Spawns a daemon thread that polls the held option's live LTP and enforces
    target / trailing stop-loss / base stop-loss / time-decay exits for this trade.
    """
    thread = threading.Thread(
        target=_run_position_monitor,
        kwargs={
            "symbol": symbol,
            "quantity": quantity,
            "trade_id": trade_id,
            "entry_premium": entry_premium,
            "target_p": target_p,
            "stop_p": stop_p,
            "tick_size": tick_size,
            "access_token": access_token,
            "dry_run": dry_run,
            "exchange": exchange,
            "poll_interval": poll_interval,
        },
        daemon=True,
        name=f"posmon-{trade_id}",
    )
    thread.start()
    print(f"[Position Monitor] Monitoring {symbol} (trade #{trade_id}) every {poll_interval:.0f}s. "
          f"Target Rs {target_p:.2f} | Stop Rs {stop_p:.2f}")
    return thread


def _run_position_monitor(symbol: str, quantity: int, trade_id: str, entry_premium: float,
                          target_p: float, stop_p: float, tick_size: float,
                          access_token: str, dry_run: bool, exchange: str,
                          poll_interval: float) -> None:
    monitor = PositionMonitor(entry_premium=entry_premium, target_p=target_p, initial_stop_p=stop_p)
    cutoff = _monitor_cutoff_time(exchange)
    fyers_client = _build_fyers_client(access_token)

    while True:
        try:
            now = _get_ist_now()
            if now.weekday() >= 5:
                print(f"  [Position Monitor #{trade_id}] Weekend detected. Monitoring stopped (EOD handoff).")
                break
            if now.time() >= cutoff:
                print(f"  [Position Monitor #{trade_id}] Reached EOD handoff ({cutoff:%H:%M} IST). "
                      "EOD square-off will close the position.")
                break

            # Stop if the position is no longer tracked (closed via /squareoff, EOD, or externally).
            try:
                from execution.state_manager import StateManager
                ap = StateManager().state.get("active_position") or {}
                if not ap or ap.get("trade_id") != trade_id:
                    print(f"  [Position Monitor #{trade_id}] Position no longer active. Monitoring stopped.")
                    break
            except Exception:
                pass

            try:
                from execution.telegram_control import is_bot_disabled
                halted = is_bot_disabled()
            except Exception:
                halted = False

            ltp = _fetch_ltp(fyers_client, symbol)
            elapsed = time.time() - monitor.entry_time
            is_exit, exit_price, reason = monitor.evaluate_tick(ltp, elapsed)
            if is_exit:
                if halted:
                    print(f"  [Position Monitor #{trade_id}] Exit signal ({reason}) but kill switch active. "
                          "Deferring placement to EOD square-off.")
                    break
                _execute_exit(symbol, quantity, trade_id, tick_size, access_token, dry_run, exit_price, reason)
                break
        except Exception as e:
            print(f"  [Position Monitor #{trade_id} Error] {e}")
        time.sleep(poll_interval)
