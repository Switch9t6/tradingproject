import os
import sys
import time
import argparse
import datetime

from auth.oauth_server import run_oauth_flow
from scanner.nse500_scanner import scan_nse500_and_indices
from scanner.option_mapper import resolve_atm_option_contract, get_mcx_crude_option_contract
from scanner.news_filter import can_trade_during_news_window
from scanner.macro_sector_engine import MacroSectorNewsEngine
from scanner.crude_scanner import scan_mcx_crude_oil
from scanner.crude_news_engine import is_crude_news_blackout_window
from scanner.smart_scanner import scan_smart_opportunities
from execution.dhan_trader import DhanTrader, renew_dhan_access_token
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
    live_wallet = state_mgr.get_current_wallet_balance()
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet

    print("=" * 80)
    print("             SESSION 2: MCX CRUDE OIL COMMODITY OPTIONS ENGINE             ")
    print(f"      Mode: REAL LIVE PRODUCTION (DHAN API v2 / MCX_FO)")
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
    trader = DhanTrader(access_token=access_token, dry_run=dry_run, force_reset=reset_state, micro_capital=micro_capital)
    trade_result = trader.execute_option_trade(
        option_contract,
        override_daily_limit=override_daily_limit,
        auto_approve=auto_approve
    )

    generate_eod_report(dry_run=dry_run)
    print("\n[MCX SESSION COMPLETE] Crude Oil quantitative pipeline finished successfully.")

def run_daily_pipeline(
    reset_state: bool = False,
    micro_capital: bool = False,
    override_daily_limit: bool = False,
    auto_approve: bool = False,
    session: str = "auto",
    dry_run: bool = False
):
    """
    Master Orchestrator supporting Dual-Session Execution & Automated 24-Hour Token Renewal:
    - session='nse': Runs Session 1 NSE Equity Options Pipeline.
    - session='mcx': Runs Session 2 MCX Crude Oil Options Pipeline.
    - session='auto': Automatically detects active session based on current IST time.
    """
    # 08:50 AM IST Daily Pre-flight Token Renewal
    if not dry_run:
        renew_dhan_access_token()

    now_ist = get_ist_now()
    current_time = now_ist.time()

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
    live_wallet = state_mgr.get_current_wallet_balance()

    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    
    print("=" * 80)
    print("             SESSION 1: NSE EQUITY & INDEX OPTIONS ENGINE                 ")
    print(f"      Mode: REAL LIVE PRODUCTION (DHAN API v2 / NSE_FO)")
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
        return

    trader = DhanTrader(access_token=access_token, dry_run=dry_run, force_reset=reset_state, micro_capital=micro_capital)
    live_wallet = trader.get_read_only_wallet_balance()
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    print(f"  [Wallet Ingestion Verified] Dhan Live Available Cash: Rs {live_wallet:,.2f} INR")

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
        override_daily_limit=override_daily_limit,
        auto_approve=auto_approve
    )

    generate_eod_report(dry_run=dry_run)
    print("\n[ORCHESTRATOR COMPLETE] Session 1 NSE quantitative options pipeline finished successfully.")

def execute_hard_eod_squareoff(access_token: str = None, dry_run: bool = False, session_tag: str = "1515"):
    """
    Hard EOD Square-Off Enforcement:
    - Session 1 (NSE): Runs at 15:15 IST
    - Session 2 (MCX): Runs at 23:00 IST
    Forcibly closes any active intraday MIS option position before market close.
    """
    import json
    from config.settings import TOKEN_FILE_PATH
    from execution.state_manager import StateManager

    print(f"\n[{session_tag} IST] HARD EOD SQUARE-OFF ENFORCEMENT RUNNING...")
    sm = StateManager()
    active_pos = sm.state.get("active_position")

    if not active_pos:
        print(f"  [EOD Square-off] 0 open positions. All positions squared off cleanly.")
        return None

    trader = DhanTrader(access_token=access_token or "", dry_run=dry_run)
    return trader.execute_exit_sell_order(
        trade_id=active_pos.get("trade_id", 1),
        instrument_key=active_pos.get("instrument_key", ""),
        option_symbol=active_pos.get("option_symbol", "ACTIVE_OPTION"),
        quantity=active_pos.get("quantity", 65),
        entry_premium=active_pos.get("entry_premium", 10.0),
        exit_reason=f"HARD_EOD_SQUAREOFF_{session_tag}"
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Dual-Session Orchestrator for NSE Equity & MCX Crude Oil Options (DhanHQ API v2)")
    parser.add_argument("--live", action="store_true", help="Run live market trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Run simulation execution mode (no live order placement)")
    parser.add_argument("--session", type=str, choices=["nse", "mcx", "auto"], default="auto", help="Specify trading session: 'nse' (09:00-15:30), 'mcx' (17:00-23:15), or 'auto'")
    parser.add_argument("--crude-only", action="store_true", help="Shortcut to run Session 2 MCX Crude Oil Options pipeline")
    parser.add_argument("--micro-capital", action="store_true", help="Enable Micro-Capital Live Test mode with Rs 250 budget cap")
    parser.add_argument("--override-daily-limit", "--force-trade", action="store_true", help="Manual override: Bypass 1 trade per day limit to allow additional trades")
    parser.add_argument("--auto-approve", "--yes", action="store_true", help="Auto-approve trade orders without interactive confirmation prompt")
    parser.add_argument("--reset-state", action="store_true", help="Force reset daily state lock for a fresh session run")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous 24/7 cloud daemon mode for Railway deployment")
    args = parser.parse_args()
    session_target = "mcx" if args.crude_only else args.session
    is_cloud_env = args.daemon or bool(os.path.exists("/data")) or bool(os.getenv("RAILWAY_ENVIRONMENT")) or bool(os.getenv("RAILWAY_PROJECT_ID")) or bool(os.getenv("PORT"))
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
        dry_run=is_dry_run
    )

    # 3. Railway / Cloud 24/7 Server Daemon Loop with Dual-Session Continuous Scanner
    if is_cloud_env:
        print("\n" + "=" * 80)
        print("  [CLOUD DAEMON DUAL-SESSION ACTIVE] Engine running 24/7 continuous market scanner on Railway.")
        print("  [SESSION 1 (NSE EQUITY)] Auto-scans 09:30-11:15 AM & 13:30-14:30 PM IST | Square-off: 15:15 IST.")
        print("  [SESSION 2 (MCX COMMODITY)] Auto-scans 17:00-23:00 PM IST | Square-off: 23:00 IST.")
        print("=" * 80 + "\n")
        
        while True:
            try:
                time.sleep(300) # Scan loop interval: 5 minutes
                now_ist = get_ist_now()
                c_time = now_ist.time()
                is_weekday = now_ist.weekday() < 5
                
                if is_weekday:
                    # Pre-flight token renewal at 08:50 AM IST
                    if datetime.time(8, 50) <= c_time <= datetime.time(8, 55):
                        renew_dhan_access_token()

                    # Session 1: NSE Options Window
                    if (datetime.time(9, 30) <= c_time <= datetime.time(11, 15)) or (datetime.time(13, 30) <= c_time <= datetime.time(14, 30)):
                        print(f"[{c_time.strftime('%H:%M:%S')} IST] [DAEMON] Triggering Session 1 NSE Market Scan...")
                        run_daily_pipeline(session="nse", auto_approve=True, micro_capital=args.micro_capital, dry_run=is_dry_run)
                        
                    # Session 1 Hard Square-Off
                    elif datetime.time(15, 15) <= c_time <= datetime.time(15, 20):
                        execute_hard_eod_squareoff(session_tag="1515", dry_run=is_dry_run)

                    # Session 2: MCX Crude Oil Options Window
                    if datetime.time(17, 0) <= c_time <= datetime.time(23, 0):
                        print(f"[{c_time.strftime('%H:%M:%S')} IST] [DAEMON] Triggering Session 2 MCX Crude Oil Market Scan...")
                        run_daily_pipeline(session="mcx", auto_approve=True, micro_capital=args.micro_capital, dry_run=is_dry_run)

                    # Session 2 Hard Square-Off
                    elif datetime.time(23, 0) <= c_time <= datetime.time(23, 10):
                        execute_hard_eod_squareoff(session_tag="2300", dry_run=is_dry_run)

            except Exception as daemon_err:
                print(f"[DAEMON LOOP ERROR] {daemon_err}")
