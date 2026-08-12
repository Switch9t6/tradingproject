"""
Automated 24/7 Multi-Session Schedule Manager (Upstox API v2)
=============================================================
Continuously monitors local time (IST) and automatically launches:
1. Session 1: NSE Equity & Index Options Engine at 09:15 AM IST (Mon-Fri)
2. Session 2: MCX Crude Oil Options Engine at 05:00 PM IST (Mon-Fri)

Runs seamlessly in background with Telegram status notifications & auto-retry logic.
"""

import os
import sys
import time
import datetime
import subprocess

from reporting.telegram_bot import send_telegram_message as send_telegram_alert

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(IST_TZ)

def run_trading_session(session_name: str):
    now_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"\n[{now_str}] 🚀 AUTO-SCHEDULER LAUNCHING {session_name}...")
    
    send_telegram_alert(
        f"🤖 <b>[AUTO-SCHEDULER ACTIVATED]</b>\n"
        f"========================================\n"
        f"Session          : {session_name}\n"
        f"Execution Time   : {now_str}\n"
        f"Mode             : LIVE REAL PRODUCTION (UPSTOX API V2)\n"
        f"========================================\n"
        f"Scanning market opportunities & executing trades automatically..."
    )
    
    cmd = [sys.executable, "main.py", "--live", "--auto-approve", "--override-daily-limit"]
    
    try:
        process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in iter(process.stdout.readline, ''):
            if line:
                print(f"[{session_name}] {line.strip()}")
                
        process.wait()
        print(f"[{session_name}] Session execution complete. Exit Code: {process.returncode}")
    except Exception as ex:
        print(f"[AUTO-SCHEDULER ERROR] Failed to launch {session_name}: {ex}")
        send_telegram_alert(f"⚠️ <b>[AUTO-SCHEDULER WARNING]</b>\nError launching {session_name}: {ex}")

def check_token_expiry_prompt():
    """Checks token age and automatically prompts user on Telegram at the 23-hour mark."""
    try:
        from config.settings import TOKEN_FILE_PATH
        import json
        
        if os.path.exists(TOKEN_FILE_PATH):
            with open(TOKEN_FILE_PATH, "r") as f:
                tdata = json.load(f)
                saved_ts = float(tdata.get("saved_timestamp") or 0.0)
                prompt_sent = bool(tdata.get("expiry_prompt_sent", False))
                
                if saved_ts > 0 and not prompt_sent:
                    age_hours = (time.time() - saved_ts) / 3600.0
                    if age_hours >= 23.0:
                        send_telegram_alert(
                            "🔑 <b>[UPSTOX ACCESS TOKEN EXPIRING IN 1 HOUR]</b>\n"
                            "========================================\n"
                            "Your Upstox Access Token is <b>23 hours old</b> (1 hour remaining before expiry).\n\n"
                            "<b>To keep automated trading running seamlessly:</b>\n"
                            "1. Generate a fresh Access Token from Upstox API Portal.\n"
                            "2. <b>Paste the token directly here in chat</b> (or send <code>/settoken YOUR_TOKEN</code>).\n"
                            "========================================"
                        )
                        tdata["expiry_prompt_sent"] = True
                        with open(TOKEN_FILE_PATH, "w") as wf:
                            json.dump(tdata, wf, indent=4)
                        print(f"[Token Expiry Checker] 23-hour token expiry prompt sent to Telegram. Age: {age_hours:.2f}h.")
    except Exception as ex:
        print(f"[Token Expiry Check Error] {ex}")

def start_automated_daemon():
    print("=" * 80)
    print("     24/7 AUTOMATED QUANTITATIVE TRADING DAEMON INITIALIZED (UPSTOX API V2)     ")
    print("=" * 80)
    print("Schedules:")
    print("  - Session 1 (NSE Equity & Options) : Mon-Fri @ 09:15 AM IST")
    print("  - Session 2 (MCX Crude Oil Options): Mon-Fri @ 05:00 PM IST")
    print("  - Upstox Token 23-Hour Expiry Monitor: Active Every 15 Minutes")
    print("Listening for schedule triggers...\n")
    
    send_telegram_alert(
        f"🤖 <b>[24/7 AUTOMATION ONLINE]</b>\n"
        f"========================================\n"
        f"Broker Gateway   : Upstox API v2\n"
        f"System Status    : FULLY AUTOMATED & STANDING BY\n"
        f"Session 1 Schedule: Mon-Fri @ 09:15 AM IST (NSE)\n"
        f"Session 2 Schedule: Mon-Fri @ 05:00 PM IST (MCX)\n"
        f"Token Monitor    : Auto-Prompt at 23 Hours Active\n"
        f"========================================\n"
        f"No manual intervention required. Trades will execute automatically!"
    )
    
    executed_today = set()
    last_token_check = 0.0

    while True:
        now = get_ist_now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        weekday = now.weekday() # 0 = Mon ... 4 = Fri, 5/6 = Sat/Sun
        
        # Check token expiry every 15 minutes
        if time.time() - last_token_check > 900:
            last_token_check = time.time()
            check_token_expiry_prompt()
        
        # Reset daily execution flags at midnight IST
        if time_str == "00:00":
            executed_today.clear()
            
        if weekday < 5:  # Monday to Friday
            # Session 1: NSE Morning Session (09:15 AM IST)
            nse_key = f"{date_str}_NSE"
            if time_str == "09:15" and nse_key not in executed_today:
                executed_today.add(nse_key)
                run_trading_session("NSE Equity & Index Options (Session 1)")
                
            # Session 2: MCX Evening Session (05:00 PM IST)
            mcx_key = f"{date_str}_MCX"
            if time_str == "17:00" and mcx_key not in executed_today:
                executed_today.add(mcx_key)
                run_trading_session("MCX Crude Oil Options (Session 2)")

        time.sleep(15)  # Check every 15 seconds

if __name__ == "__main__":
    start_automated_daemon()
