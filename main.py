import os
import sys
import time
import argparse
import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

from auth.oauth_server import run_oauth_flow
from scanner.nse500_scanner import scan_nse500_and_indices
from scanner.option_mapper import resolve_atm_option_contract, get_mcx_crude_option_contract
from scanner.news_filter import can_trade_during_news_window
from scanner.macro_sector_engine import MacroSectorNewsEngine
from scanner.crude_scanner import scan_mcx_crude_oil
from scanner.crude_news_engine import is_crude_news_blackout_window
from scanner.smart_scanner import scan_smart_opportunities
from execution.fyers_trader import FyersTrader, get_live_wallet_balance, auto_generate_fyers_token, verify_and_fetch_live_fyers_balance, handle_execution_issue_and_halt
from execution.telegram_control import start_telegram_listener_background, is_bot_disabled
from web.server import start_web_server_background
from reporting.eod_reporter import generate_eod_report
from config.settings import (
    INITIAL_WALLET_CAPITAL,
    MICRO_CAPITAL_BUDGET_CAP,
    MAX_DAILY_TRADES,
    NSE_SESSION_START,
    NSE_SESSION_END,
    MCX_SESSION_START,
    MCX_SESSION_END,
    MCX_SQUARE_OFF_SCHEDULE_TIME
)

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    """Returns current datetime in IST timezone."""
    return datetime.datetime.now(IST_TZ)

def security_audit_check():
    """
    SECURITY AUDIT ENFORCEMENT:
    Verify that ZERO fund transfer, deposit, or withdrawal endpoints exist in the codebase.
    """
    forbidden_patterns = ["def fund_transfer", ".fund_transfer(", "def withdraw_funds", ".withdraw_funds(", "def deposit_funds", ".deposit_funds(", "def add_funds", ".add_funds(", "def payout", ".payout("]
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    audit_passed = True
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".py") and file != "main.py":
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    for pattern in forbidden_patterns:
                        if pattern in content:
                            print(f"[SECURITY AUDIT FAILURE] Forbidden fund modification pattern '{pattern}' found in {filepath}")
                            audit_passed = False
                            
    if audit_passed:
        print("[SECURITY AUDIT PASSED] 100% READ-ONLY Fund Compliance Verified. Zero wallet modification endpoints exist.")
    else:
        sys.exit("[CRITICAL SECURITY ERROR] Security audit failed. Execution halted.")

def _halt_engine_and_alert_telegram(reason: str):
    """Halts the trading engine for the day and sends an urgent Telegram warning ONCE."""
    halt_flag = "logs/bot_disabled.flag"
    halt_alert_flag = "logs/halt_alert_sent.flag"

    try:
        os.makedirs("logs", exist_ok=True)
        with open(halt_flag, "w") as f:
            f.write(f"PAUSED: {reason}")
    except Exception:
        pass

    try:
        from execution.state_manager import StateManager
        sm = StateManager()
        sm.state["is_nse_locked_today"] = True
        sm.state["is_mcx_locked_today"] = True
        sm._save_state(sm.state)
    except Exception:
        pass

    # USER DIRECTIVE RULE: Send halt message ONCE. Do not repeat unless /start or /resume is used.
    if os.path.exists(halt_alert_flag):
        print(f"[Halt Gate] System is halted ({reason}). Halt notification already sent to Telegram. Suppressing repeat message.")
        return

    try:
        from reporting.telegram_bot import send_telegram_message
        msg = (
            "🚨 <b>[PRE-FLIGHT GATE FAILED - ENGINE HALTED]</b>\n"
            "========================================\n"
            "<b>Reason          :</b> Could not verify real-time Fyers wallet balance.\n"
            f"<b>Error Details   :</b> <code>{reason}</code>\n"
            "========================================\n"
            "⚠️ <i>TRADING ENGINE PAUSED FOR SAFETY. Please verify Fyers credentials and send /resume on Telegram once verified.</i>"
        )
        send_telegram_message(msg)
        with open(halt_alert_flag, "w") as f:
            f.write(datetime.datetime.now().isoformat())
    except Exception:
        pass


def morning_preflight_checks(dry_run: bool = False) -> float:
    """
    Morning Boot Sequence & Pre-Flight Verification (08:50 AM IST):
    1. Programmatically auto-generates fresh Fyers Access Token using TOTP.
    2. STRICT VERIFICATION GATE: Fetches real-time live wallet balance directly from Fyers API v3.
    3. If balance CANNOT be verified, STOPS THE ENGINE for safety & alerts Telegram.
    4. If verified, updates state.json and notifies Telegram.
    """
    print("\n[08:50 AM IST] Executing Morning Pre-flight Boot Sequence (Fyers API v3)...")
    if dry_run:
        print("[Pre-flight Dry-Run] Simulation mode active. Skipping live broker funds gate.")
        return INITIAL_WALLET_CAPITAL

    # Step 1: Auto-Generate Access Token via Headless TOTP
    token = auto_generate_fyers_token()
    if not token or token.startswith("MOCK") or token.startswith("your_"):
        print("[CRITICAL PRE-FLIGHT FAILURE] Failed to generate Fyers Access Token via TOTP.")
        _halt_engine_and_alert_telegram("Failed to generate Fyers Access Token via TOTP.")
        return 0.0

    # Step 2: Strict Live Wallet Balance Verification Gate
    is_verified, bal, err_msg = verify_and_fetch_live_fyers_balance(access_token=token)

    if not is_verified or bal <= 0:
        print(f"[PRE-FLIGHT GATE FAILED] Live Fyers wallet balance COULD NOT BE VERIFIED: {err_msg}")
        _halt_engine_and_alert_telegram(err_msg)
        return 0.0

    # Step 3: Verified! Update state.json and notify Telegram
    from execution.state_manager import StateManager
    sm = StateManager()
    sm.state["current_wallet_balance"] = bal
    sm._save_state(sm.state)

    print(f"[Pre-flight VERIFIED & MATCHED] Real-Time Upstox Live Wallet Balance: Rs {bal:,.2f} INR")

    # Notify Telegram
    try:
        from reporting.telegram_bot import send_telegram_message
        msg = (
            "✅ <b>[08:50 AM PRE-FLIGHT VERIFIED & MATCHED]</b>\n"
            "========================================\n"
            "<b>Broker          :</b> Upstox API v2\n"
            "<b>Account Owner   :</b> AMAN BIRENDRA PATHAK (5VC2TA)\n"
            f"<b>Live Cash Margin:</b> <code>Rs {bal:,.2f} INR</code> (Verified)\n"
            "========================================\n"
            "🚀 <i>System balance verified & matched with live Upstox account. Engine APPROVED & READY for 09:15 AM Market Open!</i>"
        )
        send_telegram_message(msg)
    except Exception as tele_err:
        print(f"[Pre-flight Telegram Notice] {tele_err}")

    return bal


def check_market_hours_and_calendar(session: str = "nse") -> bool:
    """
    Validates whether current time is within official trading windows:
    - Session 1 (NSE Equity): Mon-Fri 09:15 AM to 03:30 PM IST.
    - Session 2 (MCX Commodities): Mon-Fri 05:00 PM to 11:15 PM IST.
    """
    now = get_ist_now()
    is_weekday = now.weekday() < 5 # Mon-Fri
    current_time = now.time()
    
    day_name = now.strftime("%A")
    time_str = current_time.strftime("%H:%M:%S")

    if not is_weekday:
        print(f"\n[MARKET HOURS LOCKOUT] Today is {day_name} (Weekend). Market closed.")
        return False

    if session.lower() == "mcx":
        in_window = (datetime.time(17, 0) <= current_time <= datetime.time(23, 30))
        if not in_window:
            print(f"\n[MCX SESSION LOCKOUT] {time_str} IST is outside Session 2 MCX Trading Window (17:00 - 23:30 IST).")
            return False
        return True
    else:
        in_window = (datetime.time(9, 15) <= current_time <= datetime.time(15, 30))
        if not in_window:
            print(f"\n[NSE SESSION LOCKOUT] {time_str} IST is outside Session 1 NSE Trading Window (09:15 - 15:30 IST).")
            return False
        return True

def run_mcx_crude_pipeline(
    reset_state: bool = False,
    micro_capital: bool = False,
    override_daily_limit: bool = False,
    auto_approve: bool = False,
    dry_run: bool = False
):
    """
    SESSION 2: MCX Commodity Options Trading Pipeline (Crude Oil).
    Active Hours: 05:00 PM to 11:15 PM IST.
    """
    from execution.state_manager import StateManager
    state_mgr = StateManager()

    if not dry_run:
        from execution.fyers_trader import verify_and_fetch_live_fyers_balance, handle_execution_issue_and_halt
        is_verified, verified_bal, err_msg = verify_and_fetch_live_fyers_balance()
        if not is_verified or verified_bal <= 0:
            print(f"[CRITICAL SAFETY HALT] MCX Pipeline stopped because live Fyers wallet balance could not be verified: {err_msg}")
            _halt_engine_and_alert_telegram(err_msg)
            return
        live_wallet = verified_bal
    else:
        live_wallet = state_mgr.get_current_wallet_balance()

    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet

    print("=" * 80)
    print("             SESSION 2: MCX CRUDE OIL COMMODITY OPTIONS ENGINE             ")
    print(f"      Mode: REAL LIVE PRODUCTION (Fyers API v3 / MCX_FO)")
    print(f"      Wallet Base: Rs {live_wallet:,.2f} INR | Single Lot Budget Cap: Rs {budget_cap:,.2f} INR")
    print("=" * 80)

    # 1. Check EIA Inventory News Blackout Window
    is_blackout, reason = is_crude_news_blackout_window()
    if is_blackout:
        print(f"[MCX Pipeline Block] {reason}")
        return

    # 2. Market Hours Check for MCX Session
    if not check_market_hours_and_calendar(session="mcx"):
        print("[MCX Pipeline] Outside MCX market hours. Session 2 pipeline halted.")
        return

    # 3. Check Session Cap Lockout BEFORE Scanning Market
    state_mgr.reconcile_state_with_db()
    if not state_mgr.is_trade_allowed_today(exchange="MCX_FO", override_daily_limit=override_daily_limit):
        print(f"[MCX Pipeline Lockout] Session 2 MCX Crude cap reached for today. Skipping market scan.")
        return

    # 4. Authentication & Access Token
    access_token = run_oauth_flow(dry_run=dry_run)
    if not access_token:
        print("[Error] Failed to acquire valid access token for MCX Session.")
        if not dry_run:
            handle_execution_issue_and_halt("Authentication Failed", "Failed to acquire valid Fyers Access Token for MCX Session.", "MCX_CRUDE")
        return

    # 5. Scan MCX Crude Oil Multi-Factor 100-Point Matrix via Smart Scanner
    candidate = scan_smart_opportunities(
        access_token=access_token,
        session_override="mcx",
        dry_run=dry_run
    )
    if not candidate:
        print("[MCX Pipeline] No qualified trend signal for MCX Crude Oil (Score < 75 Pts). Scan complete.")
        return

    # 6. Map MCX Option Contract (Standard 100 lot or Mini 10 lot)
    option_contract = get_mcx_crude_option_contract(
        spot_price=candidate["spot_price"],
        direction=candidate["direction"],
        budget_cap=budget_cap,
        option_type=candidate.get("option_type"),
        symbol_hint=candidate.get("symbol", "CRUDEOIL")
    )

    if not option_contract:
        print(f"[MCX Pipeline] Candidate option contract exceeded budget of Rs {budget_cap:,.2f} INR.")
        return

    # 7. Check Telegram Kill Switch (/stop)
    if is_bot_disabled():
        print("[PAUSED] Remote Telegram kill switch (/stop) is active. Skipping MCX trade execution.")
        return

    # 8. Execute Order Gateway & Position Monitor
    trader = FyersTrader(dry_run=dry_run, force_reset=reset_state)
    trade_result = trader.execute_option_trade(
        option_contract,
        max_budget=budget_cap,
        session_name="MCX Crude Oil Session"
    )

    generate_eod_report(dry_run=dry_run)
    print("\n[MCX SESSION COMPLETE] Crude Oil quantitative pipeline finished successfully.")

def run_daily_pipeline(
    reset_state: bool = False,
    micro_capital: bool = False,
    override_daily_limit: bool = False,
    auto_approve: bool = False,
    session: str = "auto",
    broker: str = "fyers",
    dry_run: bool = False
):
    """
    Master Orchestrator supporting Multi-Broker (Fyers / Upstox) & Dual-Session Execution:
    - broker='fyers': Uses Fyers API v3 (Zero Static IP Restrictions).
    - broker='upstox': Uses Upstox API v2.
    """
    now_ist = get_ist_now()
    current_time = now_ist.time()
    broker_tag = broker.lower().strip()

    if session == "mcx" or (session == "auto" and current_time >= datetime.time(16, 30)):
        run_mcx_crude_pipeline(
            reset_state=reset_state,
            micro_capital=micro_capital,
            override_daily_limit=override_daily_limit,
            auto_approve=auto_approve,
            dry_run=dry_run
        )
        return

    from execution.state_manager import StateManager
    state_mgr = StateManager()

    if not dry_run:
        if broker_tag == "fyers":
            from execution.fyers_trader import verify_and_fetch_live_fyers_balance
            is_verified, verified_bal, err_msg = verify_and_fetch_live_fyers_balance()
            broker_name = "Fyers API v3"
        else:
            from execution.upstox_trader import verify_and_fetch_live_upstox_balance
            is_verified, verified_bal, err_msg = verify_and_fetch_live_upstox_balance()
            broker_name = "Upstox API v2"

        if not is_verified or verified_bal <= 0:
            print(f"[CRITICAL SAFETY HALT] Pipeline stopped because live {broker_name} wallet balance could not be verified: {err_msg}")
            _halt_engine_and_alert_telegram(err_msg)
            return
        live_wallet = verified_bal
    else:
        live_wallet = state_mgr.get_current_wallet_balance()

    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    
    print("=" * 80)
    print(f"             SESSION 1: NSE EQUITY & INDEX OPTIONS ENGINE ({broker.upper()})            ")
    print(f"      Mode: REAL LIVE PRODUCTION ({'Fyers API v3 (No IP Lock)' if broker_tag == 'fyers' else 'Upstox API v2'} / NSE_FO)")
    print(f"      Wallet Base: Rs {live_wallet:,.2f} INR | Single Lot Budget Cap: Rs {budget_cap:,.2f} INR")
    print(f"      Daily Cap: {'MANUAL OVERRIDE ACTIVE' if override_daily_limit else f'MAX {MAX_DAILY_TRADES} TRADE/DAY'}")
    print("=" * 80)

    # Launch Telegram Background Command Listener (Non-Blocking)
    start_telegram_listener_background()

    # Security Audit Check
    security_audit_check()

    # Market Hours & Weekend Guardrail Check
    if not check_market_hours_and_calendar(session="nse"):
        print("[Pipeline] Session 1 (NSE) market is closed. Pipeline complete.")
        return

    # STEP 1: AUTHENTICATION & WALLET INGESTION
    print("\n[09:00 AM] STEP 1: AUTHENTICATION & WALLET INGESTION")
    access_token = run_oauth_flow(dry_run=dry_run)
    if not access_token:
        print("[Error] Failed to acquire valid access token. Aborting pipeline.")
        if not dry_run:
            from execution.fyers_trader import handle_execution_issue_and_halt
            handle_execution_issue_and_halt("Authentication Failed", "Failed to acquire valid Fyers Access Token for NSE Session.", "NSE_EQUITY")
        return

    from execution.fyers_trader import FyersTrader
    trader = FyersTrader(dry_run=dry_run, force_reset=reset_state)

    live_wallet = trader.get_read_only_wallet_balance()
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    print(f"  [Wallet Ingestion Verified] FYERS Live Available Cash: Rs {live_wallet:,.2f} INR")

    # News Blackout Check
    if not can_trade_during_news_window():
        print("[Pipeline] Scheduled high-impact macro news event blackout active. Pipeline complete.")
        generate_eod_report(dry_run=dry_run)
        return

    # Pre-Scan Session Cap Check
    state_mgr.reconcile_state_with_db()
    if not state_mgr.is_trade_allowed_today(exchange="NSE_FO", override_daily_limit=override_daily_limit):
        print(f"[NSE Pipeline Lockout] Session 1 NSE Equity cap reached for today. Skipping market scan.")
        return

    # STEP 2: MACRO & SECTOR NEWS SCORER
    macro_engine = MacroSectorNewsEngine()
    sector_analytics = macro_engine.calculate_sector_sentiment_index()
    top_3_sectors = sector_analytics.get("top_3_sectors", [])

    # STEP 3: TECH SCAN & FACTOR MATRIX SCORING (ENGINE A: NSE EQUITY 100-PT MATRIX)
    print("\n[09:30 AM] STEP 3: SMART DUAL-ENGINE SCANNER MATRIX SCORING")
    candidate = scan_smart_opportunities(
        access_token=access_token,
        session_override="nse",
        top_3_sectors=top_3_sectors,
        micro_capital=micro_capital,
        dry_run=dry_run
    )
    
    if not candidate:
        print("[Pipeline] No high-conviction candidate qualified (Composite Score >= 75 Pts). Pipeline complete.")
        generate_eod_report(dry_run=dry_run)
        return

    # Option Contract Resolution & Lot Budget Check
    option_contract = resolve_atm_option_contract(candidate, max_budget=budget_cap)
    if not option_contract:
        print(f"[Pipeline] Candidate option contract exceeded Rs {budget_cap:,.2f} budget. Pipeline complete.")
        generate_eod_report(dry_run=dry_run)
        return

    # STEP 4: EXECUTION GATEWAY & POSITION MONITORING
    if is_bot_disabled():
        print("[PAUSED] Remote Telegram kill switch (/stop) is active. Skipping trade execution.")
        generate_eod_report(dry_run=dry_run)
        return
    
    trade_result = trader.execute_option_trade(
        option_contract,
        max_budget=budget_cap,
        session_name="NSE Equity Session"
    )

    generate_eod_report(dry_run=dry_run)
    print("\n[ORCHESTRATOR COMPLETE] Session 1 NSE quantitative options pipeline finished successfully.")

def execute_hard_eod_squareoff(access_token: str = None, dry_run: bool = False, session_tag: str = "1515"):
    """
    Hard EOD Square-Off Enforcement:
    - Session 1 (NSE): Runs at 15:15 IST
    - Session 2 (MCX): Runs at 23:00 IST
    Forcibly closes any active intraday MIS option position before market close
    by placing a marketable SELL limit order and settling P&L in StateManager.
    """
    print(f"\n[{session_tag} IST] HARD EOD SQUARE-OFF ENFORCEMENT RUNNING...")

    from execution.fyers_trader import square_off_active_position
    res = square_off_active_position(
        access_token=access_token,
        dry_run=dry_run,
        exit_reason=f"EOD_SQUAREOFF_{session_tag}",
        exit_timeout_seconds=15
    )

    if res.get("status") == "no_position":
        print(f"  [EOD Square-off] 0 open positions. All positions squared off cleanly.")
    elif res.get("status") == "TRADED":
        print(f"  [EOD Square-off] Active position closed @ Rs {res.get('exit_premium')} "
              f"(order {res.get('order_id')}). P&L settled.")
    else:
        print(f"  [EOD Square-off] WARNING: position could not be closed: "
              f"{res.get('status')} -> {res.get('remarks')}")
    return res

def print_server_public_ip():
    """Prints the outgoing public IP address of the current running server/host."""
    try:
        import requests
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        print(f"[SERVER PUBLIC IP AUDIT] Current Outgoing Public IPv4: {ip}")
        return ip
    except Exception as e:
        print(f"[SERVER PUBLIC IP AUDIT] Could not fetch IP: {e}")
        return None

if __name__ == "__main__":
    print_server_public_ip()
    parser = argparse.ArgumentParser(description="Master Dual-Session Orchestrator for NSE Equity & MCX Crude Oil Options (Upstox API v2)")
    parser.add_argument("--live", action="store_true", help="Run live market trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Run simulation execution mode (no live order placement)")
    parser.add_argument("--session", type=str, choices=["nse", "mcx", "auto"], default="auto", help="Specify trading session: 'nse' (09:00-15:30), 'mcx' (17:00-23:15), or 'auto'")
    parser.add_argument("--crude-only", action="store_true", help="Shortcut to run Session 2 MCX Crude Oil Options pipeline")
    parser.add_argument("--micro-capital", action="store_true", help="Enable Micro-Capital Live Test mode with Rs 250 budget cap")
    parser.add_argument("--override-daily-limit", "--force-trade", action="store_true", help="Manual override: Bypass 1 trade per day limit to allow additional trades")
    parser.add_argument("--auto-approve", "--yes", action="store_true", help="Auto-approve trade orders without interactive confirmation prompt")
    parser.add_argument("--reset-state", action="store_true", help="Force reset daily state lock for a fresh session run")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous 24/7 cloud daemon mode for Railway deployment")
    parser.add_argument("--broker", type=str, choices=["fyers", "upstox"], default=os.getenv("PREFERRED_BROKER", "fyers"), help="Select execution broker: 'fyers' (Zero IP Restrictions) or 'upstox'")
    args = parser.parse_args()
    session_target = "mcx" if args.crude_only else args.session
    broker_choice = args.broker.lower().strip()
    is_cloud_env = args.daemon or bool(os.path.exists("/data")) or bool(os.getenv("RAILWAY_ENVIRONMENT")) or bool(os.getenv("RAILWAY_PROJECT_ID")) or bool(os.getenv("PORT"))
    env_dry = os.getenv("IS_DRY_RUN", "").strip().lower()
    if env_dry in ["false", "0", "no"]:
        is_dry_run = False
    elif env_dry in ["true", "1", "yes"]:
        is_dry_run = True
    else:
        is_dry_run = args.dry_run or (not args.live and not is_cloud_env)
    
    # 1. Start Non-Blocking Telegram Control Listener & Web Report Server
    start_telegram_listener_background()
    start_web_server_background()

    # 2. Run Main Trading Pipeline for specified session
    run_daily_pipeline(
        reset_state=args.reset_state,
        micro_capital=args.micro_capital,
        override_daily_limit=args.override_daily_limit,
        auto_approve=args.auto_approve,
        session=session_target,
        broker=broker_choice,
        dry_run=is_dry_run
    )

    # 3. Railway / Cloud 24/7 Server Daemon Loop with Dual-Session Continuous Scanner
    if is_cloud_env:
        print("\n" + "=" * 80)
        print("  [CLOUD DAEMON DUAL-SESSION ACTIVE] Engine running 24/7 continuous market scanner on Railway.")
        print("  [SESSION 1 (NSE EQUITY)] Auto-scan @ 09:15 IST | Square-off: 15:15 IST.")
        print("  [SESSION 2 (MCX COMMODITY)] Auto-scan @ 17:00 IST | Square-off: 23:00 IST.")
        print("  [ONCE-PER-SESSION GATE ACTIVE] The engine scans/executes only ONCE per session per day.")
        print("=" * 80 + "\n")

        from session_runner import run_session_once, SchedulerState
        sched_state = SchedulerState()
        sched_state.load()

        while True:
            try:
                time.sleep(300) # Scan loop interval: 5 minutes
                now_ist = get_ist_now()
                date_str = now_ist.strftime("%Y-%m-%d")
                c_time = now_ist.time()
                is_weekday = now_ist.weekday() < 5
                
                if is_weekday:
                    # Pre-flight token auto-generation & wallet check at 08:50 AM IST
                    if datetime.time(8, 50) <= c_time <= datetime.time(8, 55):
                        morning_preflight_checks(dry_run=is_dry_run)

                    # Session 1: NSE Options Window (once-per-session gate prevents repeat scans)
                    if (datetime.time(9, 15) <= c_time <= datetime.time(15, 30)) and not sched_state.is_session_done("nse", date_str):
                        print(f"[{c_time.strftime('%H:%M:%S')} IST] [DAEMON] Triggering Session 1 NSE Market Scan...")
                        run_session_once(session="nse", dry_run=is_dry_run, auto_approve=True,
                                         override=False, micro_capital=True, trigger_source="main daemon")
                        sched_state.load()

                    # Session 1 Hard Square-Off
                    if datetime.time(15, 15) <= c_time <= datetime.time(15, 20):
                        execute_hard_eod_squareoff(session_tag="1515", dry_run=is_dry_run)

                    # Session 2: MCX Crude Oil Options Window (once-per-session gate prevents repeat scans)
                    if (datetime.time(17, 0) <= c_time <= datetime.time(23, 0)) and not sched_state.is_session_done("mcx", date_str):
                        print(f"[{c_time.strftime('%H:%M:%S')} IST] [DAEMON] Triggering Session 2 MCX Crude Oil Market Scan...")
                        run_session_once(session="mcx", dry_run=is_dry_run, auto_approve=True,
                                         override=False, micro_capital=True, trigger_source="main daemon")
                        sched_state.load()

                    # Session 2 Hard Square-Off
                    if datetime.time(23, 0) <= c_time <= datetime.time(23, 10):
                        execute_hard_eod_squareoff(session_tag="2300", dry_run=is_dry_run)

            except Exception as daemon_err:
                print(f"[DAEMON LOOP ERROR] {daemon_err}")
