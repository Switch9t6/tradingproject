import os
import sys
import time
import asyncio
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

class AsyncDhanWebSocketMonitor:
    """
    ASYNC DHAN MARKET FEED TICK STREAMING ENGINE:
    Streams live tick data for active option contracts via DhanHQ Feed API / Polling Stream
    with exponential backoff auto-reconnect handling.
    """
    def __init__(self, access_token: str, instrument_key: str, monitor: PositionMonitor):
        self.access_token = access_token
        self.security_id = str(instrument_key).replace("NSE_FO|", "").replace("MCX_FO|", "")
        self.instrument_key = instrument_key
        self.monitor = monitor
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnects = 5

    async def start_websocket_stream(self, sim_scenario: str = "AUTO", dry_run: bool = False) -> Tuple[float, str]:
        """
        Connects to Dhan Market Feed / Polling Stream, evaluates ticks asynchronously,
        and reconnects automatically with exponential backoff if network drops.
        """
        print(f"\n[Dhan Feed Engine] Launching Async Live Feed Stream for Security ID '{self.security_id}'...")
        self.is_connected = True
        start_time = time.time()

        while self.is_connected:
            try:
                # Simulation / Fallback Async Tick Stream Loop
                if dry_run or not self.access_token or self.access_token.startswith("MOCK"):
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
                    # Live Dhan Market Quote Polling Loop (2-Second LTP Intervals)
                    await asyncio.sleep(2.0)
                    elapsed = time.time() - start_time
                    
                    current_ltp = self.monitor.highest_price_seen
                    try:
                        client_id = os.getenv("DHAN_CLIENT_ID", "")
                        from dhanhq import dhanhq
                        dhan = dhanhq(client_id, self.access_token)
                        # Query LTP via Dhan API
                        q = dhan.get_market_quote_ltp(security_id=self.security_id)
                        data = q.get("data", q) if isinstance(q, dict) else q
                        if isinstance(data, dict):
                            current_ltp = float(data.get("last_price") or data.get("ltp") or self.monitor.highest_price_seen)
                    except Exception as poll_err:
                        print(f"  [Dhan LTP Feed Query Notice] {poll_err}")
                    
                    is_exit, exit_p, reason = self.monitor.evaluate_tick(current_ltp, elapsed)
                    if is_exit:
                        self.is_connected = False
                        return exit_p, reason

            except Exception as e:
                self.reconnect_attempts += 1
                backoff_delay = min(2.0 ** self.reconnect_attempts, 30.0)
                print(f"[Dhan Feed Warning] Stream connection dropped ({e}). Attempting Exponential Backoff Reconnect {self.reconnect_attempts}/{self.max_reconnects} in {backoff_delay:.1f}s...")
                if self.reconnect_attempts > self.max_reconnects:
                    print("❌ [Dhan Feed Error] Max reconnect attempts exceeded. Falling back to emergency exit level.")
                    return self.monitor.current_stop_p, "WEBSOCKET_DISCONNECT_EMERGENCY_EXIT"
                await asyncio.sleep(backoff_delay)

        return self.monitor.entry_premium, "MONITOR_STOPPED"


# Alias for backward compatibility
AsyncUpstoxWebSocketMonitor = AsyncDhanWebSocketMonitor
