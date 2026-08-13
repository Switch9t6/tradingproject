"""
Session Runner (Stage-Gated)
============================
The single entrypoint used by BOTH the automated 24/7 scheduler
(auto_scheduler.py) and the manual Telegram triggers (/start, /resume).

Guarantees (per session per calendar day):
  * The engine scans and executes at most ONCE per session unless the run
    is explicitly overridden via /start or /resume.
  * Every stage sends at most ONE Telegram message (no spam loops).
  * Any hard error aborts the session cleanly and is reported exactly once.
  * Live trades require an interactive Telegram confirmation unless the run
    is auto-approved (scheduler mode).

Sessions:
  - nse : NSE Morning Session (Equity & Index Options)   09:15 - 15:30 IST
  - mcx : MCX Evening Session (Crude Oil Options)        17:00 - 23:15 IST
"""

import os
import sys
import json
import threading
import datetime
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    LOGS_DIR,
    MAX_DAILY_TRADES,
    MICRO_CAPITAL_BUDGET_CAP,
    QUALIFICATION_SCORE_THRESHOLD,
    TAKE_PROFIT_PCT,
    STOP_LOSS_PCT,
    MORNING_SCAN_TIME,
    EVENING_SCAN_TIME,
    MORNING_SESSION_WINDOW,
    EVENING_SESSION_WINDOW,
)

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SCHEDULER_STATE_PATH = os.path.join(LOGS_DIR, "scheduler_state.json")

SESSION_LABELS = {
    "nse": "NSE Morning Session (Session 1)",
    "mcx": "MCX Evening Session (Session 2)",
}
SESSION_EXCHANGES = {"nse": "NSE_FO", "mcx": "MCX_FO"}
SESSION_WINDOWS = {"nse": MORNING_SESSION_WINDOW, "mcx": EVENING_SESSION_WINDOW}
SESSION_TRIGGERS = {"nse": MORNING_SCAN_TIME, "mcx": EVENING_SCAN_TIME}
SESSION_SCAN_OVERRIDE = {"nse": "nse", "mcx": "mcx"}


def get_ist_now() -> datetime.datetime:
    """Current IST timestamp."""
    return datetime.datetime.now(IST_TZ)


def _parse_hhmm(value: str) -> datetime.time:
    hour, minute = str(value).split(":")
    return datetime.time(int(hour), int(minute))


def detect_current_session(now: datetime.datetime = None):
    """Returns the active session ('nse' or 'mcx') or None when closed/weekend."""
    now = now or get_ist_now()
    if now.weekday() >= 5:
        return None
    current_time = now.time()
    for session, (start, end) in SESSION_WINDOWS.items():
        if _parse_hhmm(start) <= current_time <= _parse_hhmm(end):
            return session
    return None


# ---------------------------------------------------------------------------
# SchedulerState: persistent once-per-session-per-day guard
# ---------------------------------------------------------------------------
class SchedulerState:
    def __init__(self, path: str = SCHEDULER_STATE_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data = {"executed_sessions": {}}

    def load(self):
        with self._lock:
            try:
                if os.path.exists(self.path):
                    with open(self.path, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                self._data.setdefault("executed_sessions", {})
            except Exception as e:
                print(f"  [SchedulerState] Failed to load state ({e}). Starting fresh.")
                self._data = {"executed_sessions": {}}

    def save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2)
                os.replace(tmp, self.path)
            except Exception as e:
                print(f"  [SchedulerState] Failed to persist state: {e}")

    def is_session_done(self, session: str, day: str) -> bool:
        with self._lock:
            return bool(self._data["executed_sessions"].get(session, {}).get(day, False))

    def mark_session_done(self, session: str, day: str):
        with self._lock:
            self._data["executed_sessions"].setdefault(session, {})[day] = True
            self.save()

    def sessions_done_today(self, day: str) -> list:
        with self._lock:
            return [s for s in ("nse", "mcx") if self._data["executed_sessions"].get(s, {}).get(day, False)]


# ---------------------------------------------------------------------------
# Once-only Telegram helper (no spam loops)
# ---------------------------------------------------------------------------
_SENT_FLAGS = {}
_DEDUPE_LOCK = threading.Lock()


def _send_once(flag_key: str, text: str):
    with _DEDUPE_LOCK:
        if _SENT_FLAGS.get(flag_key):
            return False
        _SENT_FLAGS[flag_key] = True
    try:
        from reporting.telegram_bot import send_telegram_message
        send_telegram_message(text)
    except Exception as e:
        print(f"  [Telegram Send Failed][{flag_key}] {e}")
    return True


# ---------------------------------------------------------------------------
# Blockers: inspection & authorization
# ---------------------------------------------------------------------------
def _inspect_blockers(sm, exchange: str, dry_run: bool, override: bool) -> list:
    """Lists active safety blockers for the current session."""
    blockers = []
    from execution.telegram_control import is_bot_disabled
    if is_bot_disabled():
        blockers.append("Sleep mode (/stop) is active - send /resume or /start to wake")
    if sm.is_drawdown_limit_exceeded():
        blockers.append("Maximum daily drawdown limit exceeded")
    if not override and not sm.is_trade_allowed_today(exchange=exchange, override_daily_limit=False, dry_run=dry_run):
        cap = "5 trades/session (dry-run)" if dry_run else f"{MAX_DAILY_TRADES} trade/day (live)"
        blockers.append(f"Session trade cap reached ({cap}) - use /resume to force")
    for b in blockers:
        print(f"  [BLOCKER] {b}")
    return blockers


def _authorize_blockers(sm, exchange: str, override: bool):
    """Clears session locks for manual override runs (mirrors /resume behaviour)."""
    if override:
        sm.state["is_nse_locked_today"] = False
        sm.state["is_mcx_locked_today"] = False
        sm._save_state(sm.state)
        print(f"  [MANUAL OVERRIDE] Session locks cleared for {exchange}.")
    return True


def _verify_live_wallet(dry_run: bool):
    """Pre-flight wallet verification gate. Returns (ok, balance, error)."""
    if dry_run:
        from execution.state_manager import StateManager
        bal = StateManager().get_current_wallet_balance()
        print(f"[Pre-flight Dry-Run] Simulation mode. Recorded wallet base: Rs {bal:,.2f} INR")
        return True, bal, None

    from execution.fyers_trader import verify_and_fetch_live_fyers_balance
    is_verified, bal, err_msg = verify_and_fetch_live_fyers_balance()
    if not is_verified or bal <= 0:
        print(f"[PRE-FLIGHT GATE FAILED] Live Fyers wallet balance COULD NOT BE VERIFIED: {err_msg}")
        return False, 0.0, err_msg

    from execution.state_manager import StateManager
    sm = StateManager()
    sm.state["current_wallet_balance"] = bal
    sm._save_state(sm.state)
    print(f"[Pre-flight VERIFIED] Fyers Live Wallet Balance: Rs {bal:,.2f} INR")
    return True, bal, None


def _build_preview_text(session: str, candidate: dict, option_contract: dict, budget_cap: float) -> str:
    """Single Telegram preview of the best budget-approved opportunity."""
    score = (candidate.get("composite_rating") or {}).get("composite_score", candidate.get("score", 0))
    entry = float(option_contract.get("estimated_premium") or option_contract.get("ask_price") or 0.0)
    symbol = option_contract.get("option_symbol", "N/A")
    return (
        "🎯 <b>[BEST OPPORTUNITY - APPROVED FOR EXECUTION]</b>\n"
        "========================================\n"
        f"Session         : {SESSION_LABELS[session]}\n"
        f"Underlying      : {option_contract.get('underlying_symbol', 'N/A')}\n"
        f"Contract        : {symbol}\n"
        f"Option Type     : {option_contract.get('option_type', 'N/A')}\n"
        f"Spot / Strike   : {option_contract.get('spot_price', 'N/A')} / {option_contract.get('strike_price', 'N/A')}\n"
        f"Composite Score : {float(score):.1f} / 100 Pts\n"
        f"Lot Size        : {option_contract.get('lot_size', 'N/A')}\n"
        f"Entry Premium   : Rs {entry:.2f} / share\n"
        f"Total Lot Cost  : <b>Rs {option_contract.get('total_lot_cost', 0):,.2f} INR</b>\n"
        f"Budget Cap      : Rs {budget_cap:,.2f} INR\n"
        f"Target (+{int(TAKE_PROFIT_PCT*100)}%)   : Rs {entry*(1+TAKE_PROFIT_PCT):.2f}\n"
        f"Stop Loss (-{int(STOP_LOSS_PCT*100)}%) : Rs {entry*(1-STOP_LOSS_PCT):.2f}\n"
        "========================================"
    )


# ---------------------------------------------------------------------------
# Session pre-flight gates (shared by all triggers)
# ---------------------------------------------------------------------------
def _gates_before_scan(session: str, sm, dry_run: bool, override: bool, flag_date: str):
    """Runs market-hours / news / cap / wallet gates. Returns error string or None."""
    from main import check_market_hours_and_calendar, _halt_engine_and_alert_telegram

    if not check_market_hours_and_calendar(session=session):
        _send_once(
            f"LOCK_{session}_{flag_date}",
            f"🔒 <b>[{SESSION_LABELS[session]}]</b> Outside trading window "
            f"({SESSION_WINDOWS[session][0]} - {SESSION_WINDOWS[session][1]} IST). Scan skipped.",
        )
        return "Market closed"

    from execution.telegram_control import is_bot_disabled
    if is_bot_disabled():
        _send_once(
            f"LOCK_{session}_{flag_date}",
            "🛑 <b>[SLEEP MODE ACTIVE]</b>\nEngine is asleep via /stop. "
            "Send <b>/resume</b> or <b>/start</b> on Telegram to wake it.",
        )
        return "Kill switch active"

    blockers = _inspect_blockers(sm, SESSION_EXCHANGES[session], dry_run=dry_run, override=override)
    if blockers and not override:
        _send_once(
            f"BLOCK_{session}_{flag_date}",
            "🚧 <b>[SESSION BLOCKED BY SAFETY GATES]</b>\n"
            + "\n".join(f"• {b}" for b in blockers)
            + "\n\nSend /resume to force a fresh scan & execution.",
        )
        return "Blocked by safety gates"

    ok_wallet, balance, err_wallet = _verify_live_wallet(dry_run=dry_run)
    if not ok_wallet:
        _halt_engine_and_alert_telegram(err_wallet or "Wallet verification failed")
        _send_once(
            f"HALT_{session}_{flag_date}",
            f"🚨 <b>[PRE-FLIGHT GATE FAILED - ENGINE HALTED]</b>\n"
            f"<b>Session :</b> {SESSION_LABELS[session]}\n"
            f"<b>Reason  :</b> Could not verify real-time Fyers wallet balance.\n"
            f"<code>{err_wallet or 'Unknown'}</code>\n"
            f"👉 Verify Fyers credentials in .env and send /resume.",
        )
        return "Wallet verification failed"

    return None


# ---------------------------------------------------------------------------
# Main stage-gated entry point
# ---------------------------------------------------------------------------
def _run_session_attempt(
    state: SchedulerState,
    session: str,
    dry_run: bool,
    auto_approve: bool,
    override: bool,
    micro_capital: bool,
    trigger_source: str,
    flag_date: str,
):
    """Internal staged execution. Returns a result dict; terminal outcomes mark session done."""
    exchange = SESSION_EXCHANGES[session]
    session_label = SESSION_LABELS[session]

    from execution.state_manager import StateManager
    sm = StateManager()
    sm.reconcile_state_with_db()

    # ---- Gate 0.5a: broker-side "trade already executed today" guard (LIVE only) ----
    # Broker ground truth (positions + tradebook) survives Railway redeploys, unlike
    # state.json / scheduler_state.json which live on the ephemeral container disk.
    if not dry_run:
        from execution.fyers_trader import check_broker_trade_executed_today, segment_disabled_today

        if segment_disabled_today(exchange):
            _send_once(
                f"SEGDIS_{session}_{flag_date}",
                f"🚫 <b>[{session_label}]</b> Segment <b>{exchange}</b> is marked disabled on your broker "
                f"(segment activation issue detected earlier). Skipping today. "
                f"Enable the segment on your broker and it will be re-evaluated tomorrow.",
            )
            state.mark_session_done(session, flag_date)
            return {"status": "segment_disabled", "message": f"{exchange} disabled on broker"}

        broker_state = check_broker_trade_executed_today()
        if broker_state.get("blocked"):
            details = "\n".join(f"• {d}" for d in broker_state.get("details", []))
            _send_once(
                f"ALREADY_EXEC_{session}_{flag_date}",
                "🚫 <b>[TRADE ALREADY EXECUTED TODAY - NEW ENTRIES BLOCKED]</b>\n"
                "========================================\n"
                f"<b>Segment   :</b> {exchange}\n"
                f"<b>Reason    :</b> {broker_state.get('reason')}\n"
                f"<b>Broker    :</b>\n{details}\n"
                "========================================\n"
                "The 1-trade/day cap is already consumed. No new order will be placed.\n"
                "Send <b>/status</b> to view the current position & health.",
            )
            state.mark_session_done(session, flag_date)
            return {
                "status": "already_executed",
                "message": broker_state.get("reason"),
                "broker_state": broker_state,
            }

    # ---- Gate 1: market hours / news / cap / wallet ----
    gate_error = _gates_before_scan(session, sm, dry_run=dry_run, override=override, flag_date=flag_date)
    if gate_error:
        return {"status": "blocked", "message": gate_error}

    _authorize_blockers(sm, exchange, override=override)
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else sm.get_current_wallet_balance()
    if budget_cap <= 0:
        budget_cap = MICRO_CAPITAL_BUDGET_CAP
    print(f"  [Budget] Single best opportunity capped at Rs {budget_cap:,.2f} INR")

    # ---- Gate 2: news blackout windows ----
    if session == "nse":
        from scanner.news_filter import can_trade_during_news_window
        if not can_trade_during_news_window():
            print("[Session Runner] High-impact macro news blackout active. Session complete.")
            _send_once(f"NEWS_{session}_{flag_date}",
                       f"📰 <b>[{session_label}]</b> High-impact macro news blackout window active. Scan skipped.")
            state.mark_session_done(session, flag_date)
            return {"status": "no_trade", "message": "News blackout active"}
    else:
        from scanner.crude_news_engine import is_crude_news_blackout_window
        is_blackout, reason = is_crude_news_blackout_window()
        if is_blackout:
            print(f"[Session Runner] {reason}")
            _send_once(f"NEWS_{session}_{flag_date}",
                       f"📰 <b>[{session_label}]</b> {reason}. Scan skipped.")
            state.mark_session_done(session, flag_date)
            return {"status": "no_trade", "message": reason}

    # ---- Gate 3: authentication ----
    from auth.oauth_server import run_oauth_flow
    access_token = run_oauth_flow(dry_run=dry_run)
    if not access_token:
        print("[Session Runner] Authentication failed. Aborting session.")
        if not dry_run:
            from execution.fyers_trader import handle_execution_issue_and_halt
            handle_execution_issue_and_halt("Authentication Failed",
                                            f"Failed to acquire valid Fyers Access Token for {session_label}.", session.upper())
        return {"status": "auth_failed", "message": "Authentication failed"}

    # ---- Gate 4: sector analytics (NSE only) ----
    top_3_sectors = []
    if session == "nse":
        try:
            from scanner.macro_sector_engine import MacroSectorNewsEngine
            sector_analytics = MacroSectorNewsEngine().calculate_sector_sentiment_index()
            top_3_sectors = sector_analytics.get("top_3_sectors", [])
        except Exception as sec_err:
            print(f"  [Sector Engine Warning] {sec_err}")

    # ---- Gate 5: single best-opportunity scan (100-Pt Matrix) ----
    print(f"\n[Session Runner] Scanning {session_label} (Composite Score >= {QUALIFICATION_SCORE_THRESHOLD:.0f} Pts threshold)...")
    from scanner.smart_scanner import scan_smart_opportunities
    candidate = scan_smart_opportunities(
        access_token=access_token,
        session_override=SESSION_SCAN_OVERRIDE[session],
        top_3_sectors=top_3_sectors or None,
        micro_capital=micro_capital,
        dry_run=dry_run,
    )
    if not candidate:
        print("[Session Runner] No high-conviction candidate qualified (Composite Score < threshold). No trade.")
        _send_once(
            f"SCAN_NONE_{session}_{flag_date}",
            f"🔍 <b>[{session_label}]</b> Market scan complete - NO candidate met the "
            f"{QUALIFICATION_SCORE_THRESHOLD:.0f}/100 composite score threshold. No trade placed. Safe hold.",
        )
        state.mark_session_done(session, flag_date)
        return {"status": "no_trade", "message": "No qualified candidate (score < 75)"}

    # ---- Gate 6: budget-approved contract resolution ----
    from scanner.option_mapper import resolve_atm_option_contract, last_mapping_error
    option_contract = resolve_atm_option_contract(candidate, max_budget=budget_cap, access_token=access_token)
    if not option_contract:
        map_err = last_mapping_error() or "NO_CONTRACT"
        score_disp = (candidate.get("composite_rating") or {}).get("composite_score", candidate.get("score", "?"))
        if map_err == "STALE_OR_MISSING_CACHE":
            print(f"[Session Runner] Contract resolution FAILED due to STALE/MISSING Fyers symbol master cache. "
                  f"This is a DATA problem, not a normal skip.")
            _send_once(
                f"CONTRACT_STALE_{session}_{flag_date}",
                f"⚠️ <b>[{session_label} - CONTRACT MAP STALE]</b>\n"
                f"Best candidate found (score {score_disp}) but the Fyers symbol master cache is "
                f"STALE or MISSING, so no valid contract could be resolved.\n"
                f"<i>This is a data problem - check the Fyers public master URL / server network. "
                f"Run /status to inspect cache freshness.</i>",
            )
        elif map_err == "STRIKE_OUT_OF_BOUNDS_OR_MISSING":
            print(f"[Session Runner] ATM strike for {candidate.get('symbol')} is MISSING/OUT OF BOUNDS in the "
                  f"Fyers master (likely stale cache or a new expiry). No trade - never trade a wrong strike.")
            _send_once(
                f"CONTRACT_BOUNDS_{session}_{flag_date}",
                f"🛑 <b>[{session_label} - STRIKE OUT OF BOUNDS]</b>\n"
                f"Best candidate found (score {score_disp}) but the ATM {candidate.get('option_type')} strike "
                f"is missing / deviates too far from the master cache (no in-range contract). No trade placed.\n"
                f"<i>Likely stale symbol master or new expiry rollover.</i>",
            )
        elif map_err == "INSUFFICIENT_WALLET_BALANCE":
            print(f"[Session Runner] Usable wallet budget (after {budget_cap:,.2f} cap) too small for any "
                  f"affordable {candidate.get('option_type')} strike within the OTM walk. No trade.")
            _send_once(
                f"CONTRACT_NONE_{session}_{flag_date}",
                f"💸 <b>[{session_label}]</b> Best candidate found (score {score_disp}) but the usable "
                f"wallet budget (max Rs {budget_cap:,.2f}) is insufficient for any {candidate.get('option_type')} "
                f"strike within the OTM range. No trade placed.",
            )
        elif map_err == "NO_CONTRACT":
            print(f"[Session Runner] No matching contract in Fyers master for the ATM strike. No trade.")
            _send_once(
                f"CONTRACT_NONE_{session}_{flag_date}",
                f"🔎 <b>[{session_label}]</b> Best candidate found (score {score_disp}) but no matching "
                f"option contract exists in the Fyers symbol master for the ATM strike. No trade placed.",
            )
        else:
            print(f"[Session Runner] Best candidate exceeded the Rs {budget_cap:,.2f} budget cap. No trade.")
            _send_once(
                f"CONTRACT_NONE_{session}_{flag_date}",
                f"💸 <b>[{session_label}]</b> Best candidate found (score {score_disp}) but its "
                f"lot cost exceeds the Rs {budget_cap:,.2f} budget cap. No trade placed.",
            )
        state.mark_session_done(session, flag_date)
        return {"status": "no_trade", "message": "Contract resolution failed", "mapping_error": map_err}

    # ---- Gate 7: single Telegram preview of the best opportunity ----
    preview_text = _build_preview_text(session, candidate, option_contract, budget_cap)
    _send_once(f"PREVIEW_{session}_{flag_date}", preview_text)
    print(preview_text)

    if not auto_approve and not dry_run:
        from execution.telegram_control import request_telegram_trade_approval
        entry = float(option_contract.get("estimated_premium") or option_contract.get("ask_price") or 0.0)
        approved = request_telegram_trade_approval(
            option_symbol=option_contract.get("option_symbol", "N/A"),
            lot_size=int(option_contract.get("lot_size") or 0),
            entry_premium=entry,
            total_cost=float(option_contract.get("total_lot_cost") or 0.0),
            target_price=entry * (1 + TAKE_PROFIT_PCT),
            stop_price=entry * (1 - STOP_LOSS_PCT),
            timeout_seconds=60,
        )
        if not approved:
            print("[Session Runner] Trade not approved by user. No order placed.")
            state.mark_session_done(session, flag_date)
            return {"status": "rejected", "message": "User did not approve the trade"}

    # ---- Gate 8: kill-switch re-check before order placement ----
    from execution.telegram_control import is_bot_disabled
    if is_bot_disabled():
        print("[Session Runner] Kill switch activated before order placement. Order aborted.")
        return {"status": "blocked", "message": "Kill switch active"}

    # ---- Gate 9: execute the single best opportunity ----
    from execution.fyers_trader import FyersTrader
    trader = FyersTrader(dry_run=dry_run, force_reset=False)
    trade_result = trader.execute_option_trade(
        option_contract,
        max_budget=budget_cap,
        session_name=session_label,
    )

    # ---- Final: EOD report + mark session done ----
    try:
        from reporting.eod_reporter import generate_eod_report
        generate_eod_report(dry_run=dry_run)
    except Exception as rpt_err:
        print(f"  [EOD Report Warning] {rpt_err}")

    state.mark_session_done(session, flag_date)
    print(f"\n[SESSION RUNNER COMPLETE] {session_label} finished. Session marked done for {flag_date}.")
    return {"status": "completed", "message": "Session completed", "trade_result": trade_result}


def run_session_once(
    session: str = "auto",
    dry_run: bool = False,
    auto_approve: bool = False,
    override: bool = False,
    micro_capital: bool = True,
    trigger_source: str = "scheduler",
):
    """
    Runs ONE full stage-gated session (scan -> preview -> execute -> report).
    Safe to call repeatedly: non-overridden runs are skipped once per day.

    Any terminal outcome (success, no-trade, block, rejection or error) closes
    the once-per-session gate so the scheduler never re-fires. Only /start or
    /resume re-opens the session with override=True.
    """
    state = SchedulerState()
    state.load()

    if session == "auto":
        session = detect_current_session()
    if session not in SESSION_EXCHANGES:
        print("[Session Runner] No active session right now (market closed / between sessions).")
        return {"status": "no_session", "message": "No active session"}

    now = get_ist_now()
    flag_date = now.strftime("%Y-%m-%d")
    session_label = SESSION_LABELS[session]

    print("\n" + "=" * 80)
    print(f"  SESSION RUNNER | {session_label}")
    print(f"  Trigger       : {trigger_source}")
    print(f"  Mode          : {'DRY RUN (Simulation)' if dry_run else 'LIVE (Fyers API v3)'}")
    print(f"  Auto-approve  : {auto_approve}")
    print(f"  Override      : {override}")
    print("=" * 80)

    # ---- Gate 0: once-per-session-per-day (scheduler only) ----
    if not override and state.is_session_done(session, flag_date):
        print(f"[Session Runner] {session_label} already executed today at "
              f"{state._data['executed_sessions'].get(session, {}).get(flag_date)}. Skipping repeat scan.")
        _send_once(
            f"SKIP_{session}_{flag_date}",
            f"ℹ️ <b>[{session_label}]</b> Scan already completed today at {SESSION_TRIGGERS[session]} IST. "
            "No repeat scan. Use <b>/start</b> or <b>/resume</b> to force a fresh manual scan.",
        )
        return {"status": "skipped", "message": "Session already ran today"}

    try:
        result = _run_session_attempt(
            state=state,
            session=session,
            dry_run=dry_run,
            auto_approve=auto_approve,
            override=override,
            micro_capital=micro_capital,
            trigger_source=trigger_source,
            flag_date=flag_date,
        )
    except Exception as exc:
        traceback.print_exc()
        _send_once(
            f"ERROR_{session}_{flag_date}",
            f"❌ <b>[{session_label} - SESSION ERROR]</b>\n"
            f"<code>{str(exc)[:500]}</code>\n\n"
            f"Session aborted safely. Send /resume to retry.",
        )
        state.mark_session_done(session, flag_date)
        return {"status": "error", "message": str(exc)}

    # Any real attempt closes the once-per-day gate so the scheduler never re-fires.
    state.mark_session_done(session, flag_date)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage-gated single-session runner")
    parser.add_argument("--session", type=str, choices=["nse", "mcx", "auto"], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--override", action="store_true")
    args = parser.parse_args()

    is_dry = args.dry_run or not args.live
    result = run_session_once(
        session=args.session,
        dry_run=is_dry,
        auto_approve=args.auto_approve,
        override=args.override,
        trigger_source="cli",
    )
    print(f"\nRESULT: {result.get('status')} - {result.get('message')}")
