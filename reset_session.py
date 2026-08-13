import sys
import os
import json
import sqlite3
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import STATE_FILE_PATH, DB_FILE_PATH, INITIAL_WALLET_CAPITAL

today = datetime.date.today().isoformat()

# Try querying live Fyers wallet balance if available
live_wallet = INITIAL_WALLET_CAPITAL
try:
    from execution.fyers_trader import get_live_wallet_balance
    wb = get_live_wallet_balance()
    if wb > 0:
        live_wallet = wb
except Exception:
    pass

# 1. Reset state.json completely
clean_state = {
    "date": today,
    "NSE_FO_trades_today": 0,
    "MCX_FO_trades_today": 0,
    "is_nse_locked_today": False,
    "is_mcx_locked_today": False,
    "active_trade_id": None,
    "active_position": None,
    "current_wallet_balance": live_wallet,
    "session_cap_alerted": {}
}
os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
with open(STATE_FILE_PATH, "w") as f:
    json.dump(clean_state, f, indent=4)
print("[RESET] state.json cleared for", today, "- all session locks removed")

# 2. Delete all DRY_RUN / test trades from DB for today
conn = sqlite3.connect(DB_FILE_PATH)
cursor = conn.cursor()
cursor.execute("DELETE FROM trades WHERE trade_date=? AND execution_mode!='LIVE'", (today,))
deleted = cursor.rowcount
conn.commit()

# 3. Count remaining LIVE trades for today
cursor.execute("SELECT COUNT(*) FROM trades WHERE trade_date=? AND execution_mode='LIVE'", (today,))
live_count = cursor.fetchone()[0]
conn.close()

print("[RESET] Deleted", deleted, "DRY_RUN/test records from trades.db for", today)
print("[STATUS] Remaining LIVE trades today:", live_count)
print("[READY] Session state reset complete - system is clear for live trading")
