import os
import sys
import json
import time
import datetime
import requests

sys.path.insert(0, ".")

url = "https://api.upstox.com/v2/login/authorization/token"
headers = {
    "accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded"
}
data = {
    "code": "aeNDgt",
    "client_id": "9fc1a4ba-0abf-4e11-b7c7-5227d3075d19",
    "client_secret": "001d4siui2",
    "redirect_uri": "https://localhost",
    "grant_type": "authorization_code"
}

print("=== EXCHANGING UPSTOX OAUTH CODE FOR ACCESS TOKEN ===")
try:
    r = requests.post(url, headers=headers, data=data, timeout=10)
    print("HTTP Status Code:", r.status_code)
    print("Response Text:", r.text)

    if r.status_code == 200:
        res_data = r.json()
        access_token = res_data.get("access_token", "")
        user_name = res_data.get("user_name", "")
        user_id = res_data.get("user_id", "")
        print(f"\n✅ TOKEN EXCHANGE SUCCESSFUL!")
        print(f"User: {user_name} ({user_id})")
        print(f"Token: {access_token[:15]}...")

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
        print("✅ Saved to .env and logs/access_token.json!")
        
        # Test live wallet balance with new token
        from execution.upstox_trader import get_live_wallet_balance
        bal = get_live_wallet_balance(access_token)
        print(f"💰 Live Wallet Balance: Rs {bal:,.2f} INR")

        # Send Telegram Notification
        from reporting.telegram_bot import send_telegram_message
        msg = (
            f"✅ <b>[UPSTOX ACCESS TOKEN AUTHENTICATED]</b>\n"
            f"========================================\n"
            f"<b>User Name:</b> <code>{user_name}</code>\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n"
            f"<b>Live Cash Balance:</b> <code>Rs {bal:,.2f} INR</code>\n"
            f"========================================\n"
            f"Upstox API v2 gateway is active and ready for automated trading!"
        )
        send_telegram_message(msg)

    else:
        print(f"❌ Token exchange failed. Status: {r.status_code}")
except Exception as ex:
    print(f"❌ Exception: {ex}")
