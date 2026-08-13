"""
Automated 24/7 Multi-Session Scheduler (Fyers API v3)
======================================================
Runs as the single long-lived cloud process (Railway worker). It:

  * Starts the Telegram command listener & web report server at boot.
  * Auto-refreshes the Fyers access token before expiry (23h TOTP renew).
  * Triggers ONE stage-gated session per day per market:
      - Session 1: NSE Morning Session @ 09:15 IST (window 09:15 - 15:30)
      - Session 2: MCX Evening Session @ 17:00 IST (window 17:00 - 23:15)
  * Delegates the actual scan/preview/execution to session_runner.run_session_once(),
    which guarantees the engine never re-fires or spams Telegram. Manual
    /start and /resume commands (via the Telegram listener) force a fresh run.

Modes:
  --live       : real Fyers order placement (default for Railway)
  --dry-run    : full simulation, no real orders
  IS_DRY_RUN env overrides both flags (truthy = dry run).
"""

import os
import sys
import time
import argparse
import threading
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    MORNING_SCAN_TIME,
    EVENING_SCAN_TIME,
    MORNING_SESSION_WINDOW,
    EVENING_SESSION_WINDOW,
    ENABLE_AUTO_SCHEDULER,
)
from session_runner import run_session_once, get_ist_now, SchedulerState, SESSION_LABELS


def _safe_print(text: str):
    try:
        print(text)
    except Exception:
        try:
            print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))
        except Exception:
            pass


def resolve_is_dry_run(args) -> bool:
    """IS_DRY_RUN env overrides CLI flags; otherwise --dry-run or (not --live)."""
    env_dry = os.getenv("IS_DRY_RUN", "").strip().lower()
    if env_dry in ["false", "0", "no"]:
        return False
    if env_dry in ["true", "1", "yes"]:
        return True
    return args.dry_run or not args.live


def _within_session_window(now_ist: datetime.datetime) -> bool:
    """True if now falls inside an active trading session (token refresh deferred)."""
    t = now_ist.time()
    for start, end in (MORNING_SESSION_WINDOW, EVENING_SESSION_WINDOW):
        s = datetime.time(*[int(x) for x in start.split(":")])
        e = datetime.time(*[int(x) for x in end.split(":")])
        if s <= t <= e:
            return True
    return False


def check_token_expiry_prompt():
    """Refreshes the Fyers access token via headless TOTP at the 23-hour mark.

    Refresh is deferred while a trading session is live so auth never drops
    mid-position; it only proceeds during a session if the token has truly expired.
    """
    try:
        from config.settings import FYERS_TOKEN_FILE_PATH
        import json
        from execution.fyers_trader import auto_generate_fyers_token, get_live_wallet_balance

        need_refresh = True
        age_hours = 999.0
        if os.path.exists(FYERS_TOKEN_FILE_PATH):
            try:
                with open(FYERS_TOKEN_FILE_PATH, "r") as f:
                    tdata = json.load(f)
                saved_ts = float(tdata.get("saved_timestamp") or tdata.get("saved_at") or 0.0)
                age_hours = (time.time() - saved_ts) / 3600.0 if saved_ts > 0 else 999.0
                need_refresh = age_hours >= 23.0
            except Exception:
                need_refresh = True

        if need_refresh and age_hours < 24.0 and _within_session_window(get_ist_now()):
            _safe_print("[Token Expiry Checker] Token age >= 23h but a trading session is live. "
                        "Deferring refresh until the session ends to avoid dropping auth mid-position.")
            return

        if need_refresh:
            _safe_print("[Token Expiry Checker] 23-hour token age reached. Initiating Headless TOTP Auto-Login...")
            new_tok = auto_generate_fyers_token()
            if new_tok and not new_tok.startswith("MOCK") and not new_tok.startswith("your_"):
                try:
                    avail = get_live_wallet_balance(new_tok)
                except Exception:
                    avail = 0.0
                from reporting.telegram_bot import send_telegram_message
                send_telegram_message(
                    "✅ <b>[FYERS TOKEN AUTO-RENEWED]</b>\n"
                    "========================================\n"
                    "Fresh 24-hour Access Token generated via Headless TOTP Auto-Login!\n"
                    f"<b>Available Cash Balance:</b> <code>Rs {avail:,.2f} INR</code>\n"
                    "========================================\n"
                    "Zero manual intervention required. Automated trading continues seamlessly!"
                )
                _safe_print("[Token Expiry Checker] Token auto-renewed successfully via TOTP.")
    except Exception as ex:
        _safe_print(f"[Token Expiry Check Error] {ex}")


def _token_refresher_loop():
    """Background loop: checks token age every 30 minutes (23h auto-renew)."""
    while True:
        try:
            check_token_expiry_prompt()
        except Exception as ex:
            _safe_print(f"[Token Refresher Error] {ex}")
        time.sleep(1800)


def _within_window(time_str: str, start: str, end: str) -> bool:
    return start <= time_str <= end


def _fire_session(state: SchedulerState, session: str, date_str: str, dry_run: bool):
    """Runs one stage-gated session if not already done today."""
    if not ENABLE_AUTO_SCHEDULER:
        _safe_print("[AUTO-SCHEDULER] PAUSED: ENABLE_AUTO_SCHEDULER=False. "
                    "Automated entries disabled; sessions run only via manual /start or /resume.")
        return
    if state.is_session_done(session, date_str):
        return
    now_str = get_ist_now().strftime("%H:%M")
    _safe_print(f"\n[{now_str} IST] [AUTO-SCHEDULER] Triggering {SESSION_LABELS[session]} scan...")
    result = run_session_once(
        session=session,
        dry_run=dry_run,
        auto_approve=True,   # automated runs: no interactive confirmation
        override=False,      # once-per-session-per-day gate active
        micro_capital=True,  # spend the single best opportunity within budget cap
        trigger_source="auto_scheduler",
    )
    state.load()  # reload persisted state after the run
    _safe_print(f"[AUTO-SCHEDULER] {SESSION_LABELS[session]} -> {result.get('status')}: {result.get('message')}")


def start_automated_daemon(dry_run: bool = True):
    mode_tag = "DRY RUN (Simulation)" if dry_run else "LIVE (Fyers API v3)"
    _safe_print("=" * 80)
    _safe_print("     24/7 AUTOMATED QUANTITATIVE TRADING DAEMON INITIALIZED     ")
    _safe_print("=" * 80)
    _safe_print(f"Mode       : {mode_tag}")
    if ENABLE_AUTO_SCHEDULER:
        _safe_print("Schedules  :")
        _safe_print(f"  - Session 1 (NSE Equity & Options) : Mon-Fri @ {MORNING_SCAN_TIME} IST")
        _safe_print(f"  - Session 2 (MCX Crude Oil Options): Mon-Fri @ {EVENING_SCAN_TIME} IST")
        _safe_print("Once-per-session gating active. Manual /start & /resume force a fresh run.\n")
    else:
        _safe_print("Scheduler  : PAUSED (ENABLE_AUTO_SCHEDULER=False)")
        _safe_print("  - No automated session entries. Sessions start ONLY via manual /start or /resume.")
        _safe_print("  - Each live entry requires interactive Telegram approval before order placement.\n")

    # 1. Start Telegram command listener + web report server (non-blocking)
    try:
        from execution.telegram_control import start_telegram_listener_background
        start_telegram_listener_background()
    except Exception as e:
        _safe_print(f"[Telegram Listener Error] {e}")
    try:
        from web.server import start_web_server_background
        start_web_server_background()
    except Exception as e:
        _safe_print(f"[Web Server Error] {e}")

    # 2. Startup notification (single message)
    try:
        from reporting.telegram_bot import send_telegram_message
        send_telegram_message(
            f"🤖 <b>[24/7 AUTOMATION ONLINE]</b>\n"
            f"========================================\n"
            f"Broker Gateway    : Fyers API v3\n"
            f"Mode              : {mode_tag}\n"
            f"System Status     : FULLY AUTOMATED & STANDING BY\n"
            f"Session 1 Schedule: Mon-Fri @ {MORNING_SCAN_TIME} IST (NSE)\n"
            f"Session 2 Schedule: Mon-Fri @ {EVENING_SCAN_TIME} IST (MCX)\n"
            f"========================================\n"
            f"No manual intervention required. Trades will execute automatically!"
        )
    except Exception as e:
        _safe_print(f"[Startup Telegram Notice] {e}")

    # 3. Background token auto-refresh (23h TOTP renew)
    threading.Thread(target=_token_refresher_loop, daemon=True).start()

    # 4. Main trigger loop (30s poll, once-per-session gate)
    state = SchedulerState()
    state.load()
    daily_actions_done = set()  # in-memory: square-off/EOD fire once per day
    while True:
        try:
            now = get_ist_now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H:%M")

            if now.weekday() < 5:  # Mon-Fri only
                if _within_window(time_str, MORNING_SCAN_TIME, MORNING_SESSION_WINDOW[1]):
                    _fire_session(state, "nse", date_str, dry_run)
                if _within_window(time_str, EVENING_SCAN_TIME, EVENING_SESSION_WINDOW[1]):
                    _fire_session(state, "mcx", date_str, dry_run)

                # Hard EOD square-off enforcement (once per day each)
                if time_str == "15:15" and f"{date_str}_SO1" not in daily_actions_done:
                    daily_actions_done.add(f"{date_str}_SO1")
                    try:
                        from main import execute_hard_eod_squareoff
                        execute_hard_eod_squareoff(session_tag="1515", dry_run=dry_run)
                    except Exception as so_err:
                        _safe_print(f"[Square-Off Error (NSE)] {so_err}")
                if time_str == "23:00" and f"{date_str}_SO2" not in daily_actions_done:
                    daily_actions_done.add(f"{date_str}_SO2")
                    try:
                        from main import execute_hard_eod_squareoff
                        execute_hard_eod_squareoff(session_tag="2300", dry_run=dry_run)
                    except Exception as so_err:
                        _safe_print(f"[Square-Off Error (MCX)] {so_err}")

            time.sleep(30)
        except Exception as loop_err:
            _safe_print(f"[DAEMON LOOP ERROR] {loop_err}")
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="24/7 automated dual-session scheduler (Fyers API v3)")
    parser.add_argument("--live", action="store_true", help="Real Fyers order placement mode")
    parser.add_argument("--dry-run", action="store_true", help="Full simulation mode (no real orders)")
    args = parser.parse_args()
    start_automated_daemon(dry_run=resolve_is_dry_run(args))
