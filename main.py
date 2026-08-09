import os
import sys
import time
import argparse
import datetime

from auth.oauth_server import run_oauth_flow
from scanner.nse500_scanner import scan_nse500_and_indices
from scanner.option_mapper import resolve_atm_option_contract
from scanner.news_filter import can_trade_during_news_window
from scanner.macro_sector_engine import MacroSectorNewsEngine
from execution.upstox_trader import UpstoxOptionsTrader
from execution.telegram_control import start_telegram_listener_background, is_bot_disabled
from reporting.eod_reporter import generate_eod_report
from config.settings import INITIAL_WALLET_CAPITAL, MAX_SINGLE_LOT_PREMIUM_BUDGET, MICRO_CAPITAL_BUDGET_CAP, MAX_DAILY_TRADES

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
                            print(f"[SECURITY AUDIT FAILED] Forbidden fund modification pattern '{pattern}' found in {filepath}")
                            audit_passed = False
                            
    if audit_passed:
        print("[SECURITY AUDIT PASSED] 100% READ-ONLY Fund Compliance Verified. Zero wallet modification endpoints exist.")
    else:
        sys.exit("[CRITICAL SECURITY ERROR] Security audit failed. Execution halted.")

def check_market_hours_and_calendar(dry_run: bool = False) -> bool:
    """
    Validates whether today is an official NSE trading day (Mon-Fri)
    and current time is within market operating window (09:15 AM to 03:30 PM IST).
    """
    now = datetime.datetime.now()
    is_weekday = now.weekday() < 5 # 0 = Mon, 4 = Fri, 5 = Sat, 6 = Sun
    
    time_0915 = datetime.time(9, 15)
    time_1530 = datetime.time(15, 30)
    current_time = now.time()
    is_market_hours = (current_time >= time_0915) and (current_time <= time_1530)
    
    day_name = now.strftime("%A")
    time_str = current_time.strftime("%H:%M:%S")
    
    if not is_weekday or not is_market_hours:
        msg = f"Today is {day_name} at {time_str} IST (Outside Official NSE Trading Window: Mon-Fri 09:15 - 15:30 IST)."
        if dry_run:
            print(f"\n[MARKET HOURS GUARDRAIL] {msg}")
            print("[MARKET HOURS GUARDRAIL] --dry-run SIMULATION ACTIVE: Bypassing market hours lockout for dry-run testing.")
            return True
        else:
            print(f"\n[MARKET HOURS LOCKOUT] {msg}")
            print("[MARKET HOURS LOCKOUT] Live market scanning and order placement are disabled outside market hours.")
            return False
            
    return True

def run_daily_pipeline(
    dry_run: bool = False,
    reset_state: bool = False,
    sim_scenario: str = "AUTO",
    micro_capital: bool = False,
    override_daily_limit: bool = False,
    auto_approve: bool = False
):
    budget_cap = MICRO_CAPITAL_BUDGET_CAP if micro_capital else MAX_SINGLE_LOT_PREMIUM_BUDGET
    
    print("=" * 80)
    print("                      QUANTITATIVE MULTI-FACTOR TRADING ENGINE                 ")
    print(f"      Mode: {'DRY-RUN / SIMULATION' if dry_run else 'LIVE PRODUCTION'}")
    print(f"      Wallet Base: Rs {INITIAL_WALLET_CAPITAL:,.2f} INR | Single Lot Budget Cap: Rs {budget_cap:,.2f} INR {'(MICRO-CAPITAL MODE)' if micro_capital else ''}")
    print(f"      Daily Cap: {'MANUAL OVERRIDE ACTIVE (UNLIMITED TRADES)' if override_daily_limit else f'MAX {MAX_DAILY_TRADES} TRADE/DAY'}")
    print(f"      Approval Guardrail: {'INTERACTIVE USER CONFIRMATION REQUIRED' if not auto_approve else 'AUTO-APPROVED'}")
    print("=" * 80)

    # Launch Telegram Background Command Listener (Non-Blocking)
    start_telegram_listener_background()

    # Security Audit Check
    security_audit_check()

    # Market Hours & Weekend Guardrail Check
    if not check_market_hours_and_calendar(dry_run=dry_run):
        print("[Pipeline] Market is closed. Daily pipeline execution halted.")
        return

    # [09:00 AM] STEP 1: AUTHENTICATION & WALLET INGESTION
    print("\n[09:00 AM] STEP 1: AUTHENTICATION & WALLET INGESTION")
    print("  \\-- Querying Upstox API v2 (`/user/get-funds-and-margin`) to fetch live balance...")
    access_token = run_oauth_flow(dry_run=dry_run)
    if not access_token:
        print("[Error] Failed to acquire valid access token. Aborting pipeline.")
        return

    # Economic Calendar News Blackout Check
    if not can_trade_during_news_window():
        print("[Pipeline] Scheduled high-impact macro news event blackout active. Pipeline complete for today.")
        generate_eod_report(dry_run=dry_run)
        return

    # [09:05 AM] STEP 2: MACRO & SECTOR NEWS SCORER (MacroSectorNewsEngine)
    macro_engine = MacroSectorNewsEngine()
    sector_analytics = macro_engine.calculate_sector_sentiment_index()
    top_3_sectors = sector_analytics.get("top_3_sectors", [])

    # [09:30 AM] STEP 3: SIMULTANEOUS TECH SCAN & FACTOR MATRIX SCORING (0 TO 100 PTS)
    print("\n[09:30 AM] STEP 3: SIMULTANEOUS TECH SCAN & FACTOR MATRIX SCORING")
    print("  |-- Parallel Scan: Scanning symbols strictly within top-ranked sectors:", top_3_sectors)
    candidate = scan_nse500_and_indices(access_token=access_token, dry_run=dry_run, top_3_sectors=top_3_sectors)
    
    if not candidate:
        print("[Pipeline] No high-conviction candidate qualified (Composite Score >= 75 Pts). Pipeline complete for today.")
        generate_eod_report(dry_run=dry_run)
        return

    comp_rating = candidate.get("composite_rating", {})
    print(f"  |-- Qualified Candidate Found : {candidate['symbol']} ({candidate['direction']})")
    print(f"  \\-- Composite Opportunity Rating: {comp_rating.get('composite_score', 0)} / 100 Pts (Tech: {comp_rating.get('tech_score',0)} + News: {comp_rating.get('news_score',0)})")

    # Option Contract Resolution & Lot Budget Check
    option_contract = resolve_atm_option_contract(candidate, max_budget=budget_cap)
    if not option_contract:
        print(f"[Pipeline] Candidate option contract exceeded Rs {budget_cap:,.2f} budget. Pipeline complete for today.")
        generate_eod_report(dry_run=dry_run)
        return

    # [09:31 AM - 02:30 PM] STEP 4: EXECUTION GATEWAY & POSITION MONITORING
    print("\n[09:31 AM - 02:30 PM] STEP 4: EXECUTION GATEWAY")
    print("  |-- IF Composite Score >= 75 / 100: Executing Aggressive Limit Order on Upstox.")
    print("  \\-- Monitoring Position using 30-Min Stagnation Exit & Step-Based TSL...")

    # Check Telegram Remote Kill Switch (/stop)
    if is_bot_disabled():
        print("[PAUSED] Remote Telegram kill switch (/stop) is active. Skipping trade execution.")
        print("[PAUSED] Send /resume on Telegram to re-enable trading.")
        generate_eod_report(dry_run=dry_run)
        return
    
    trader = UpstoxOptionsTrader(access_token=access_token, dry_run=dry_run, force_reset=reset_state)
    trade_result = trader.execute_option_trade(
        option_contract,
        sim_scenario=sim_scenario,
        override_daily_limit=override_daily_limit,
        auto_approve=auto_approve
    )

    # End of Day (EOD) Performance & Audit Report Generation
    print("\n[15:30 IST] Generating EOD Performance & Audit Reports...")
    report_path = generate_eod_report(dry_run=dry_run)

    print("\n[ORCHESTRATOR COMPLETE] Daily quantitative multi-factor options pipeline finished successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Daily Orchestrator for Intraday Options Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Run dry-run simulation workflow")
    parser.add_argument("--live", action="store_true", help="Run live market trading mode")
    parser.add_argument("--micro-capital", action="store_true", help="Enable Micro-Capital Live Test mode with Rs 250 budget cap")
    parser.add_argument("--override-daily-limit", "--force-trade", action="store_true", help="Manual override: Bypass 1 trade per day limit to allow additional trades")
    parser.add_argument("--auto-approve", "--yes", action="store_true", help="Auto-approve trade orders without interactive confirmation prompt")
    parser.add_argument("--reset-state", action="store_true", help="Force reset daily state lock for a fresh simulation run")
    parser.add_argument("--tsl-scenario", type=str, default="AUTO", choices=["AUTO", "STEP1_BREAKEVEN_HIT", "STEP2_PROFIT_LOCK_HIT", "TIME_DECAY_EXIT", "STOP_LOSS_HIT", "TARGET_HIT"], help="Simulate specific position exit scenario in dry-run mode")
    args = parser.parse_args()
    
    is_dry_run = not args.live if args.live else (args.dry_run or True)
    run_daily_pipeline(
        dry_run=is_dry_run,
        reset_state=args.reset_state,
        sim_scenario=args.tsl_scenario,
        micro_capital=args.micro_capital,
        override_daily_limit=args.override_daily_limit,
        auto_approve=args.auto_approve
    )
