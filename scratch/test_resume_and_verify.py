import os
import sys
import dotenv

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

from execution.state_manager import StateManager
from execution.upstox_trader import verify_and_fetch_live_upstox_balance
from reporting.telegram_bot import send_telegram_message

# 1. Reset state lock
sm = StateManager()
sm.state["is_nse_locked_today"] = False
sm.state["is_mcx_locked_today"] = False
sm._save_state(sm.state)

# 2. Verify live balance
is_ver, bal, msg = verify_and_fetch_live_upstox_balance()

print(f"Verified : {is_ver}")
print(f"Balance  : Rs {bal:,.2f} INR")
print(f"Status   : {msg}")

if is_ver and bal > 0:
    sm.state["current_wallet_balance"] = bal
    sm._save_state(sm.state)
    
    t_msg = (
        "✅ <b>[PRE-FLIGHT GATE RESOLVED & RESUMED]</b>\n"
        "========================================\n"
        "<b>Broker Status     :</b> Upstox API v2 Connected\n"
        "<b>Auth Mode         :</b> 100% Headless TOTP Auto-Login\n"
        f"<b>Live Cash Balance :</b> <code>Rs {bal:,.2f} INR</code>\n"
        "========================================\n"
        "🟢 Trading Engine is fully online and verified. Ready for live execution."
    )
    send_telegram_message(t_msg)
    print("Telegram confirmation alert sent successfully!")
