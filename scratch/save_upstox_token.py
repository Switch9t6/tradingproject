import os
import sys
import json
import time
import datetime

sys.path.insert(0, ".")

access_token = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI1VkMyVEEiLCJqdGkiOiI2YTdjY2NkYjVjZTUwZjM1OTU0Y2U3MzciLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzg2NTYzODAzLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3ODY1NzIwMDB9.IhrExPPcUEpPeNl3f2fZyFcJyCBeLRvW_eW_wxAhbu0"
user_name = "AMAN BIRENDRA PATHAK"
user_id = "5VC2TA"

print("[Upstox Auth] Saving authenticated token to .env and logs/access_token.json...")

# Update .env
env_path = ".env"
with open(env_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
keys_updated = {"UPSTOX_API_KEY": False, "UPSTOX_API_SECRET": False, "UPSTOX_REDIRECT_URI": False, "UPSTOX_ACCESS_TOKEN": False}

for l in lines:
    if l.startswith("UPSTOX_API_KEY="):
        new_lines.append('UPSTOX_API_KEY="9fc1a4ba-0abf-4e11-b7c7-5227d3075d19"\n')
        keys_updated["UPSTOX_API_KEY"] = True
    elif l.startswith("UPSTOX_API_SECRET="):
        new_lines.append('UPSTOX_API_SECRET="001d4siui2"\n')
        keys_updated["UPSTOX_API_SECRET"] = True
    elif l.startswith("UPSTOX_REDIRECT_URI="):
        new_lines.append('UPSTOX_REDIRECT_URI="https://localhost"\n')
        keys_updated["UPSTOX_REDIRECT_URI"] = True
    elif l.startswith("UPSTOX_ACCESS_TOKEN="):
        new_lines.append(f'UPSTOX_ACCESS_TOKEN="{access_token}"\n')
        keys_updated["UPSTOX_ACCESS_TOKEN"] = True
    else:
        new_lines.append(l)

for k, updated in keys_updated.items():
    if not updated:
        if k == "UPSTOX_API_KEY":
            new_lines.append('UPSTOX_API_KEY="9fc1a4ba-0abf-4e11-b7c7-5227d3075d19"\n')
        elif k == "UPSTOX_API_SECRET":
            new_lines.append('UPSTOX_API_SECRET="001d4siui2"\n')
        elif k == "UPSTOX_REDIRECT_URI":
            new_lines.append('UPSTOX_REDIRECT_URI="https://localhost"\n')
        elif k == "UPSTOX_ACCESS_TOKEN":
            new_lines.append(f'UPSTOX_ACCESS_TOKEN="{access_token}"\n')

with open(env_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

# Save to logs/access_token.json
token_payload = {
    "access_token": access_token,
    "user_name": user_name,
    "user_id": user_id,
    "updated_at": datetime.datetime.now().isoformat(),
    "saved_timestamp": time.time(),
    "expiry_prompt_sent": False
}
os.makedirs("logs", exist_ok=True)
with open("logs/access_token.json", "w") as f:
    json.dump(token_payload, f, indent=4)

os.environ["UPSTOX_ACCESS_TOKEN"] = access_token
print("[Upstox Auth] Token saved successfully!")

# Query Live Wallet Balance with new token
from execution.upstox_trader import get_live_wallet_balance
avail_cash = get_live_wallet_balance(access_token)
print(f"[Upstox Wallet] Live Available Cash: Rs {avail_cash:,.2f} INR")

# Update state.json
from execution.state_manager import StateManager
sm = StateManager()
sm.state["token_saved_at"] = time.time()
sm.state["expiry_prompt_sent"] = False
sm.state["current_wallet_balance"] = avail_cash
sm._save_state(sm.state)

# Send Telegram Notification
from reporting.telegram_bot import send_telegram_message
msg = (
    f"✅ <b>[UPSTOX ACCESS TOKEN AUTHENTICATED]</b>\n"
    f"========================================\n"
    f"<b>User Name:</b> <code>{user_name}</code>\n"
    f"<b>User ID:</b> <code>{user_id}</code>\n"
    f"<b>Live Cash Balance:</b> <code>Rs {avail_cash:,.2f} INR</code>\n"
    f"========================================\n"
    f"Upstox API v2 gateway is active and ready for automated trading!"
)
send_telegram_message(msg)
print("[Telegram] Confirmation alert delivered to Telegram chat.")
