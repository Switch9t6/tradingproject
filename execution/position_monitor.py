"""
Live Fyers Trailing Stop-Loss (TSL) & Position Monitoring Engine.

Tracks an open option position in real-time by polling Fyers LTP every 5 seconds
and enforcing the following rule ladder:

    1. Base Stop Loss   : current_sl starts at entry_price * 0.88 (-12%).
    2. TSL Step 1       : peak_price >= entry_price * 1.08 (+8%)
                          -> current_sl = max(current_sl, entry_price * 1.00)  (BREAKEVEN)
    3. TSL Step 2       : peak_price >= entry_price * 1.15 (+15%)
                          -> current_sl = max(current_sl, entry_price * 1.10)  (LOCK +10%)
    4. Hard Target      : LTP >= entry_price * 1.25 (+25%) -> exit TARGET_HIT_+25%
    5. Time Stagnation  : held >= 20 min AND gain < +5% -> exit TIME_EXIT_30MIN_STAGNANT

Exits are marketable, tick-aligned SELLs routed through the shared aggressive
limit-order path. P&L is settled via StateManager.record_exit_trade()
(SQLite logs/trades.db) and every exit dispatches a Telegram alert containing
the entry price, exit price, realized P&L (INR) and the exact exit reason.

The monitor defers to the EOD auto-square-off (15:15 IST for NSE_FO,
23:00 IST for MCX_FO) and on weekends. The /stop command puts the engine into
SLEEP MODE: it squares off any open position immediately, and no automated
orders (entries, TSL exits, or EOD square-off) run until /resume or /start is
sent manually. While asleep, the monitor keeps polling read-only and resumes
exits once the engine is woken.
"""

import os
import time
import threading
import datetime
from typing import Tuple, Optional

from config.settings import (
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    USE_STEP_TRAILING_STOP_LOSS,
    TSL_STEP1_TRIGGER_PCT,
    TSL_STEP2_TRIGGER_PCT,
    TSL_STEP2_LOCK_PCT,
    MAX_HOLD_SECONDS,
    MIN_GAIN_REQUIRED_AT_30M,
)

# ---------------------------------------------------------------------------
# Strategy threshold constants (multiples of the entry price)
# ---------------------------------------------------------------------------
INITIAL_SL_MULTIPLIER: float = 1.0 - STOP_LOSS_PCT             # 0.88  -> -12%
TARGET_PRICE_MULTIPLIER: float = 1.0 + TAKE_PROFIT_PCT         # 1.25  -> +25%
TSL_STEP1_TRIGGER_MULTIPLIER: float = 1.0 + TSL_STEP1_TRIGGER_PCT   # 1.08
TSL_STEP1_SL_MULTIPLIER: float = 1.00                          # breakeven
TSL_STEP2_TRIGGER_MULTIPLIER: float = 1.0 + TSL_STEP2_TRIGGER_PCT   # 1.15
TSL_STEP2_SL_MULTIPLIER: float = 1.0 + TSL_STEP2_LOCK_PCT      # 1.10  -> lock +10%
TIME_EXIT_GAIN_RATIO: float = MIN_GAIN_REQUIRED_AT_30M         # 1.05  -> +5%

# Exact exit reason strings (persisted to DB and dispatched to Telegram).
EXIT_REASON_TARGET: str = "TARGET_HIT_+25%"
EXIT_REASON_BASE_STOP: str = "STOP_LOSS_HIT_-12%"
EXIT_REASON_STEP1: str = "TSL_STEP1_BREAKEVEN_HIT"
EXIT_REASON_STEP2: str = "TSL_STEP2_PROFIT_LOCK_HIT"
EXIT_REASON_TIME: str = "TIME_EXIT_30MIN_STAGNANT"

IST_TZ: datetime.tzinfo = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_POLL_INTERVAL_SECONDS: float = 5.0

# EOD boundaries at which the monitor hands the position to the auto-square-off.
_MONITOR_CUTOFFS: dict = {
    "NSE_FO": datetime.time(15, 15),
    "MCX_FO": datetime.time(23, 0),
}


class PositionMonitor:
    """
    Per-position TSL state machine.

    Fields (per architecture specification):
        entry_price             : initial fill price
        peak_price              : highest LTP seen since entry
        current_sl              : active stop-loss price level
        step1_breakeven_active  : step-1 breakeven flag
        step2_profit_lock_active: step-2 profit-lock flag
    """

    def __init__(self, entry_price: float, target_price: float, initial_sl: float) -> None:
        self.entry_price: float = entry_price
        self.target_price: float = target_price
        self.initial_sl: float = initial_sl
        self.peak_price: float = entry_price
        self.current_sl: float = initial_sl
        self.step1_breakeven_active: bool = False
        self.step2_profit_lock_active: bool = False
        self.entry_time: float = time.time()

    def evaluate_tick(self, current_price: float, elapsed_seconds: float) -> Tuple[bool, float, str]:
        """
        Evaluates one live tick and returns (is_exit_triggered, exit_price, exit_reason).

        Zero, negative or failed ticks are ignored without mutating peak / SL state.
        """
        if current_price is None or current_price <= 0.0:
            return False, self.peak_price, ""

        if current_price > self.peak_price:
            self.peak_price = current_price
            self._update_trailing_stop()

        # Exit 1: Hard target (+25%).
        if current_price >= self.target_price:
            return True, self.target_price, EXIT_REASON_TARGET

        # Exit 2: Base stop or trailing stop retracement.
        if current_price <= self.current_sl:
            if self.current_sl > self.initial_sl:
                if self.step2_profit_lock_active:
                    reason = EXIT_REASON_STEP2
                else:
                    reason = EXIT_REASON_STEP1
            else:
                reason = EXIT_REASON_BASE_STOP
            return True, self.current_sl, reason

        # Exit 3: Time-decay stagnation (held >= 20 min AND gain < +5%).
        if elapsed_seconds >= MAX_HOLD_SECONDS:
            gain_ratio = current_price / self.entry_price
            if gain_ratio < TIME_EXIT_GAIN_RATIO:
                return True, current_price, EXIT_REASON_TIME

        return False, current_price, ""

    def _update_trailing_stop(self) -> None:
        """Ratchets current_sl up monotonically as peak_price climbs."""
        if not USE_STEP_TRAILING_STOP_LOSS:
            return

        if not self.step1_breakeven_active and self.peak_price >= self.entry_price * TSL_STEP1_TRIGGER_MULTIPLIER:
            self.current_sl = max(self.current_sl, self.entry_price * TSL_STEP1_SL_MULTIPLIER)
            self.step1_breakeven_active = True
            print(f"  [TSL ADJUSTED] Gain hit +{TSL_STEP1_TRIGGER_PCT * 100:.0f}% "
                  f"(Rs {self.peak_price:.2f}). SL moved to BREAKEVEN (Rs {self.current_sl:.2f}).")

        if not self.step2_profit_lock_active and self.peak_price >= self.entry_price * TSL_STEP2_TRIGGER_MULTIPLIER:
            self.current_sl = max(self.current_sl, self.entry_price * TSL_STEP2_SL_MULTIPLIER)
            self.step2_profit_lock_active = True
            print(f"  [TSL ADJUSTED] Gain hit +{TSL_STEP2_TRIGGER_PCT * 100:.0f}% "
                  f"(Rs {self.peak_price:.2f}). SL moved to LOCK PROFIT @ +{TSL_STEP2_LOCK_PCT * 100:.0f}% "
                  f"(Rs {self.current_sl:.2f}).")


# ---------------------------------------------------------------------------
# Live polling helpers
# ---------------------------------------------------------------------------
def _monitor_cutoff_time(exchange: str) -> datetime.time:
    """EOD boundary at which the monitor must hand off to the auto-square-off."""
    return _MONITOR_CUTOFFS.get(str(exchange).upper(), datetime.time(23, 0))


def _get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(IST_TZ)


def _build_fyers_client(access_token: str) -> Optional[object]:
    """Creates a lightweight Fyers client for LTP polling (None in dry/MOCK mode)."""
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


def _fetch_ltp(fyers_client: Optional[object], symbol: str) -> float:
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


def _send_exit_alert(symbol: str, quantity: int, entry_price: float, exit_price: float,
                     net_pnl: Optional[float], reason: str, order_id: Optional[str] = None) -> None:
    """Dispatches a formatted Telegram alert with entry, exit, realized P&L and exact reason."""
    try:
        from reporting.telegram_bot import send_telegram_message
    except Exception as notify_err:
        print(f"  [Position Monitor Notify Notice] {notify_err}")
        return

    if net_pnl is None:
        net_pnl = (exit_price - entry_price) * quantity
    pnl_sign = "+" if net_pnl >= 0 else "-"
    message = (
        f"💰 <b>[POSITION EXIT]</b>\n"
        f"========================================\n"
        f"<b>Symbol        :</b> <code>{symbol}</code>\n"
        f"<b>Qty           :</b> {quantity}\n"
        f"<b>Entry Price   :</b> Rs {entry_price:.2f}\n"
        f"<b>Exit Price    :</b> Rs {exit_price:.2f}\n"
        f"<b>Realized P&L  :</b> {pnl_sign}Rs {abs(net_pnl):,.2f} INR\n"
        f"<b>Exit Reason   :</b> <code>{reason}</code>\n"
        f"========================================\n"
    )
    if order_id:
        message += f"<b>Order ID      :</b> <code>{order_id}</code>\n"
    try:
        send_telegram_message(message)
    except Exception as notify_err:
        print(f"  [Position Monitor Notify Notice] {notify_err}")


def _send_exit_failed_alert(symbol: str, reason: str, res: Optional[dict]) -> None:
    try:
        from reporting.telegram_bot import send_telegram_message
    except Exception as notify_err:
        print(f"  [Position Monitor Notify Notice] {notify_err}")
        return
    try:
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


def _execute_exit(symbol: str, quantity: int, trade_id: str, tick_size: float,
                  access_token: str, dry_run: bool, exit_price: float, reason: str) -> bool:
    """
    Submits the marketable, tick-aligned SELL exit for a triggered TSL / target /
    stop / time exit, settles P&L via record_exit_trade() and alerts Telegram.
    Retries once if the first exit order remains PENDING.
    """
    from execution.fyers_trader import place_aggressive_limit_order
    from execution.state_manager import StateManager

    sm = StateManager()
    active_position = sm.state.get("active_position") or {}
    entry_price = float(active_position.get("entry_premium") or 0.0)
    qty = int(active_position.get("quantity") or quantity)

    # If the user already closed the position manually on Fyers, settle it as
    # MANUAL_EXIT instead of placing a phantom SELL the broker will reject.
    if not dry_run:
        try:
            from execution.fyers_trader import detect_manual_exit_and_record
            if detect_manual_exit_and_record(active_position, access_token=access_token,
                                             send_telegram_alert=True):
                print(f"  [Position Monitor] {symbol} already closed externally. Recorded as MANUAL_EXIT.")
                return True
        except Exception as man_exit_err:
            print(f"  [Position Monitor Manual Exit Notice] {man_exit_err}")

    res = None
    closed_qty = 0
    last_fill = float(exit_price)
    remaining_qty = qty
    for attempt in range(1, 4):
        res = place_aggressive_limit_order(
            symbol=symbol,
            quantity=remaining_qty,
            transaction_type="SELL",
            product_type="INTRADAY",
            limit_price=exit_price,
            dry_run=dry_run,
            access_token=access_token,
            tick_size=tick_size,
            fill_timeout_seconds=10,
        )
        status = res.get("status")
        if status == "TRADED":
            closed_qty += remaining_qty
            last_fill = float(res.get("filled_price") or exit_price)
            break

        if status == "PARTIALLY_FILLED":
            filled_qty = int(res.get("filled_qty", 0) or 0)
            last_fill = float(res.get("filled_price") or exit_price)
            closed_qty += filled_qty
            remaining_qty -= filled_qty
            print(f"  [Position Monitor] Partial exit fill {filled_qty} qty @ Rs {last_fill:.2f}; "
                  f"retrying remainder ({remaining_qty}).")
            if remaining_qty <= 0:
                break
            time.sleep(5)
            continue

        if status == "PENDING":
            print(f"  [Position Monitor] Exit order pending (attempt {attempt}). Retrying in 5s...")
            time.sleep(5)
            continue

        # REJECTED
        print(f"  [Position Monitor] Exit order rejected: {status} -> {res.get('remarks')}")
        break

    if closed_qty > 0:
        # Settle P&L on the ACTUAL closed quantity (partial exits included) so the
        # trade ledger always reflects what really hit the broker.
        try:
            net_pnl = sm.record_exit_trade(trade_id=trade_id, exit_premium=last_fill, exit_reason=reason,
                                           settled_quantity=closed_qty)
        except Exception as settle_err:
            print(f"  [Position Monitor Exit Settle Notice] {settle_err}")
            net_pnl = None
        print(f"  [Position Monitor] EXITED #{trade_id}: {reason} @ Rs {last_fill:.2f} "
              f"(qty {closed_qty}/{qty}, order {res.get('order_id') if res else 'n/a'}) | "
              f"Net P&L: {net_pnl if net_pnl is not None else 'N/A'}")
        _send_exit_alert(symbol, closed_qty, entry_price, last_fill, net_pnl, reason,
                         res.get("order_id") if res else None)
        if closed_qty < qty:
            print(f"  [Position Monitor WARNING] Only {closed_qty}/{qty} closed. "
                  f"Remainder will be reconciled by EOD square-off.")
        return True

    _send_exit_failed_alert(symbol, reason, res)
    return False


def start_position_monitor(symbol: str, quantity: int, trade_id: str, entry_price: float,
                           target_price: float, initial_sl: float, tick_size: float = 0.05,
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
            "entry_price": entry_price,
            "target_price": target_price,
            "initial_sl": initial_sl,
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
          f"Target Rs {target_price:.2f} | Stop Rs {initial_sl:.2f}")
    return thread


def _run_position_monitor(symbol: str, quantity: int, trade_id: str, entry_price: float,
                          target_price: float, initial_sl: float, tick_size: float,
                          access_token: str, dry_run: bool, exchange: str,
                          poll_interval: float) -> None:
    """Long-running per-position monitoring loop (runs on a daemon thread)."""
    monitor = PositionMonitor(entry_price=entry_price, target_price=target_price, initial_sl=initial_sl)
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

            # Yield to external commands: stop if the position is no longer tracked
            # (closed externally, via /squareoff, or by EOD square-off).
            try:
                from execution.state_manager import StateManager
                ap = StateManager().state.get("active_position") or {}
                if not ap or ap.get("trade_id") != trade_id:
                    print(f"  [Position Monitor #{trade_id}] Position no longer active. Monitoring stopped.")
                    break
            except Exception:
                pass

            # System kill switch: sleep mode means no automated orders. Keep polling
            # (read-only) so that after /resume or /start the monitor resumes exits.
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
                    print(f"  [Position Monitor #{trade_id}] Exit signal ({reason}) but SLEEP MODE active "
                          "(/stop). No automated orders while asleep; position stays open until "
                          "/resume, /start, or manual /squareoff.")
                    time.sleep(poll_interval)
                    continue
                _execute_exit(symbol, quantity, trade_id, tick_size, access_token, dry_run, exit_price, reason)
                break
        except Exception as e:
            print(f"  [Position Monitor #{trade_id} Error] {e}")
        time.sleep(poll_interval)
