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
from execution.upstox_trader import UpstoxOptionsTrader
from execution.telegram_control import start_telegram_listener_background, is_bot_disabled
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
    auto_approve: bool = False
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
    print(f"      Mode: REAL LIVE PRODUCTION (MCX_FO)")
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

    # 3. Authentication & Access Token
    access_token = run_oauth_flow(dry_run=False)
    if not access_token:
        print("[Error] Failed to acquire valid access token for MCX Session.")
        return

    # 4. Scan MCX Crude Oil Multi-Factor 100-Point Matrix via Smart Scanner
    candidate = scan_smart_opportunities(
        access_token=access_token,
        session_override="mcx",
        dry_run=False
    )
    if not candidate:
        print("[MCX Pipeline] No qualified trend signal for MCX Crude Oil (Score < 75 Pts). Scan complete.")
        return

    # 5. Map MCX Option Contract (Standard 100 lot or Mini 10 lot)
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

    # 6. Check Telegram Kill Switch (/stop)
    if is_bot_disabled():
        print("[PAUSED] Remote Telegram kill switch (/stop) is active. Skipping MCX trade execution.")
        return

    # 7. Execute Order Gateway & Position Monitor
    trader = UpstoxOptionsTrader(access_token=access_token, dry_run=False, force_reset=reset_state)
    trade_result = trader.execute_option_trade(
        option_contract,
        override_daily_limit=override_daily_limit,
        auto_approve=auto_approve
    )

    generate_eod_report(dry_run=False)
    print("\n[MCX SESSION COMPLETE] Crude Oil quantitative pipeline finished successfully.")

def run_daily_pipeline(
    reset_state: bool = False,
    micro_capital: bool = False,
    override_daily_limit: bool = False,
    auto_approve: bool = False,
    session: str = "auto"
):
    """
    Master Orchestrator supporting Dual-Session Execution:
    - session='nse': Runs Session 1 NSE Equity Options Pipeline.
    - session='mcx': Runs Session 2 MCX Crude Oil Options Pipeline.
    - session='auto': Automatically detects active session based on current IST time.
    """
    now_ist = get_ist_now()
    current_time = now_ist.time()

    if session == "mcx" or (session == "auto" and current_time >= datetime.time(16, 30)):
        run_mcx_crude_pipeline(
            reset_state=reset_state,
            micro_capital=micro_capital,
            override_daily_limit=override_daily_limit,
            auto_approve=auto_approve
        )
        return

    from execution.state_manager import StateManager
    state_mgr = StateManager()
    live_wallet = state_mgr.get_current_wallet_balance()

    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    
    print("=" * 80)
    print("             SESSION 1: NSE EQUITY & INDEX OPTIONS ENGINE                 ")
    print(f"      Mode: 100% REAL LIVE PRODUCTION (NSE_FO)")
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
    access_token = run_oauth_flow(dry_run=False)
    if not access_token:
        print("[Error] Failed to acquire valid access token. Aborting pipeline.")
        return

    trader = UpstoxOptionsTrader(access_token=access_token, dry_run=False, force_reset=reset_state)
    live_wallet = trader.get_read_only_wallet_balance()
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else live_wallet
    print(f"  [Wallet Ingestion Verified] Upstox Live Available Cash: Rs {live_wallet:,.2f} INR")

    # News Blackout Check
    if not can_trade_during_news_window():
        print("[Pipeline] Scheduled high-impact macro news event blackout active. Pipeline complete.")
        generate_eod_report(dry_run=False)
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
        dry_run=False
    )
    
    if not candidate:
        print("[Pipeline] No high-conviction candidate qualified (Composite Score >= 75 Pts). Pipeline complete.")
        generate_eod_report(dry_run=False)
        return

    # Option Contract Resolution & Lot Budget Check
    option_contract = resolve_atm_option_contract(candidate, max_budget=budget_cap)
    if not option_contract:
        print(f"[Pipeline] Candidate option contract exceeded Rs {budget_cap:,.2f} budget. Pipeline complete.")
        generate_eod_report(dry_run=False)
        return

    # STEP 4: EXECUTION GATEWAY & POSITION MONITORING
    if is_bot_disabled():
        print("[PAUSED] Remote Telegram kill switch (/stop) is active. Skipping trade execution.")
        generate_eod_report(dry_run=False)
        return
    
    trade_result = trader.execute_option_trade(
        option_contract,
        override_daily_limit=override_daily_limit,
        auto_approve=auto_approve
    )

    generate_eod_report(dry_run=False)
    print("\n[ORCHESTRATOR COMPLETE] Session 1 NSE quantitative options pipeline finished successfully.")

def execute_hard_eod_squareoff(access_token: str = None, dry_run: bool = False, session_tag: str = "1515"):
    """
    Hard EOD Square-Off Enforcement:
    - Session 1 (NSE): Runs at 15:15 IST
    - Session 2 (MCX): Runs at 23:00 IST
    Forcibly closes any active intraday MIS option position before market close.
    """
    import json
    from config.settings import SQUARE_OFF_SCHEDULE_TIME, TOKEN_FILE_PATH
    from execution.state_manager import StateManager

    print(f"\n[{session_tag} IST] HARD EOD SQUARE-OFF ENFORCEMENT RUNNING...")
    sm = StateManager()
    active_pos = sm.state.get("active_position")

    if not access_token:
        token_file = "access_token.json" if os.path.exists("access_token.json") else TOKEN_FILE_PATH
        if os.path.exists(token_file):
            with open(token_file, "r") as f:
                access_token = json.load(f).get("access_token", "")

    if not active_pos and access_token and not access_token.startswith("MOCK"):
        try:
            import upstox_client
            config = upstox_client.Configuration()
            config.access_token = access_token
            p_api = upstox_client.PortfolioApi(upstox_client.ApiClient(config))
            res = p_api.get_positions(api_version="2.0")
            p_data = getattr(res, "data", res)
            positions = p_data if isinstance(p_data, list) else []
            open_fno = [p for p in positions if int(getattr(p, "quantity", 0) if not isinstance(p, dict) else p.get("quantity", 0)) != 0]
            if open_fno:
                p_item = open_fno[0]
                active_pos = {
                    "trade_id": 1,
                    "instrument_key": getattr(p_item, "instrument_token", "") if not isinstance(p_item, dict) else p_item.get("instrument_token", ""),
                    "option_symbol": getattr(p_item, "trading_symbol", "ACTIVE_OPTION") if not isinstance(p_item, dict) else p_item.get("trading_symbol", "ACTIVE_OPTION"),
                    "quantity": abs(int(getattr(p_item, "quantity", 65) if not isinstance(p_item, dict) else p_item.get("quantity", 65))),
                    "entry_premium": float(getattr(p_item, "buy_price", 10.0) if not isinstance(p_item, dict) else p_item.get("buy_price", 10.0))
                }
        except Exception as p_err:
            print(f"[EOD Portfolio Query Notice] {p_err}")

    if not active_pos:
        print(f"  [EOD Square-off] 0 open positions. All positions squared off cleanly.")
        return None

    trader = UpstoxOptionsTrader(access_token=access_token, dry_run=dry_run)
    return trader.execute_exit_sell_order(
        trade_id=active_pos.get("trade_id", 1),
        instrument_key=active_pos.get("instrument_key", ""),
        option_symbol=active_pos.get("option_symbol", "ACTIVE_OPTION"),
        quantity=active_pos.get("quantity", 65),
        entry_premium=active_pos.get("entry_premium", 10.0),
        exit_reason=f"HARD_EOD_SQUAREOFF_{session_tag}"
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Dual-Session Orchestrator for NSE Equity & MCX Crude Oil Options")
    parser.add_argument("--live", action="store_true", help="Run live market trading mode (Default)")
    parser.add_argument("--session", type=str, choices=["nse", "mcx", "auto"], default="auto", help="Specify trading session: 'nse' (09:00-15:30), 'mcx' (17:00-23:15), or 'auto'")
    parser.add_argument("--crude-only", action="store_true", help="Shortcut to run Session 2 MCX Crude Oil Options pipeline")
    parser.add_argument("--micro-capital", action="store_true", help="Enable Micro-Capital Live Test mode with Rs 250 budget cap")
    parser.add_argument("--override-daily-limit", "--force-trade", action="store_true", help="Manual override: Bypass 1 trade per day limit to allow additional trades")
    parser.add_argument("--auto-approve", "--yes", action="store_true", help="Auto-approve trade orders without interactive confirmation prompt")
    parser.add_argument("--reset-state", action="store_true", help="Force reset daily state lock for a fresh session run")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous 24/7 cloud daemon mode for Railway deployment")
    args = parser.parse_args()

    session_target = "mcx" if args.crude_only else args.session
    
    # 1. Start Non-Blocking Telegram Control Listener
    start_telegram_listener_background()

    # 2. Run Main Trading Pipeline for specified session
    run_daily_pipeline(
        reset_state=args.reset_state,
        micro_capital=args.micro_capital,
        override_daily_limit=args.override_daily_limit,
        auto_approve=args.auto_approve,
        session=session_target
    )

    # 3. Railway / Cloud 24/7 Server Daemon Loop with Dual-Session Continuous Scanner
    is_cloud_env = args.daemon or bool(os.path.exists("/data")) or bool(os.getenv("RAILWAY_ENVIRONMENT")) or bool(os.getenv("RAILWAY_PROJECT_ID")) or bool(os.getenv("PORT"))
    if is_cloud_env:
        print("\n" + "=" * 80)
        print("  [CLOUD DAEMON DUAL-SESSION ACTIVE] Engine running 24/7 continuous market scanner on Railway.")
        print("  [SESSION 1 (NSE EQUITY)] Auto-scans 09:30-11:15 AM & 13:30-14:30 PM IST | Square-off: 15:15 IST.")
        print("  [SESSION 2 (MCX CRUDE OIL)] Auto-scans 17:00-23:00 PM IST | Square-off: 23:00 IST.")
        print("  [TELEGRAM CONTROL] Online 24/7 for /start, /status, /report, /trades, /squareoff, /stop, /resume.")
        print("=" * 80)
        try:
            last_scan_minute = -1
            nse_squared_off = False
            mcx_squared_off = False
            while True:
                time.sleep(15)
                try:
                    now_ist = get_ist_now()
                    current_time = now_ist.time()
                    minute = now_ist.minute
                    weekday = now_ist.weekday() # 0 = Mon, 4 = Fri

                    if weekday < 5:
                        # Session 1 NSE 15:15 IST Square-off Check
                        if datetime.time(15, 15) <= current_time <= datetime.time(15, 25) and not nse_squared_off:
                            nse_squared_off = True
                            execute_hard_eod_squareoff(dry_run=False, session_tag="1515_NSE")

                        # Session 2 MCX 23:00 IST Square-off Check
                        if datetime.time(23, 0) <= current_time <= datetime.time(23, 10) and not mcx_squared_off:
                            mcx_squared_off = True
                            execute_hard_eod_squareoff(dry_run=False, session_tag="2300_MCX")

                        if current_time < datetime.time(15, 0):
                            nse_squared_off = False
                            mcx_squared_off = False

                        # Session Window Time Ranges
                        in_nse_w1 = (datetime.time(9, 30) <= current_time <= datetime.time(11, 15))
                        in_nse_w2 = (datetime.time(13, 30) <= current_time <= datetime.time(14, 30))
                        in_mcx_w = (datetime.time(17, 0) <= current_time <= datetime.time(23, 0))

                        if (in_nse_w1 or in_nse_w2 or in_mcx_w) and (minute % 5 == 0) and (minute != last_scan_minute):
                            from execution.state_manager import StateManager
                            sm = StateManager()
                            target_session = "mcx" if in_mcx_w else "nse"
                            ex_segment = "MCX_FO" if target_session == "mcx" else "NSE_FO"
                            if sm.is_trade_allowed_today(exchange=ex_segment, override_daily_limit=args.override_daily_limit) and not is_bot_disabled():
                                last_scan_minute = minute
                                print(f"\n⏰ [5-MIN SCAN TRIGGER] Auto-scanning {target_session.upper()} market at {now_ist.strftime('%H:%M:%S')} IST...")
                                run_daily_pipeline(
                                    reset_state=False,
                                    micro_capital=args.micro_capital,
                                    override_daily_limit=args.override_daily_limit,
                                    auto_approve=args.auto_approve,
                                    session=target_session
                                )
                except Exception as loop_err:
                    import traceback
                    tb = traceback.format_exc()
                    print(f"[Daemon Loop Error] {loop_err}\n{tb}")
                    from reporting.telegram_bot import send_telegram_error_alert
                    send_telegram_error_alert("Daemon Loop Exception", str(loop_err), tb)
        except KeyboardInterrupt:
            print("\n[Cloud Daemon] Stopping worker process...")
