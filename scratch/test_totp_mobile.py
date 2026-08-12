import os
import sys
import json
import time
import datetime
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, ".")

print("==========================================================================")
print("   TESTING HEADLESS TOTP AUTO-LOGIN WITH MOBILE: 9699990215               ")
print("==========================================================================")
print(f"Username (Mobile) : {os.getenv('UPSTOX_USERNAME')}")
print(f"PIN Code          : {'*' * len(os.getenv('UPSTOX_PIN_CODE', ''))}")
print(f"TOTP Secret       : {'*' * len(os.getenv('UPSTOX_TOTP_SECRET', ''))}")

from execution.upstox_trader import auto_generate_upstox_token, get_live_wallet_balance
import upstox_client

token = auto_generate_upstox_token()
print(f"\n[Headless TOTP Result] Token Length: {len(token)} | Token Prefix: {token[:20]}...")

if token and not token.startswith("MOCK") and not token.startswith("your_"):
    configuration = upstox_client.Configuration()
    configuration.access_token = token
    api_client = upstox_client.ApiClient(configuration)
    user_api = upstox_client.UserApi(api_client)

    prof_res = user_api.get_profile(api_version="2.0")
    pdata = getattr(prof_res, "data", prof_res)
    profile_name = getattr(pdata, "user_name", "Unknown")
    user_id = getattr(pdata, "user_id", "Unknown")
    email = getattr(pdata, "email", "Unknown")
    
    print("\n[Profile Audit SUCCESS]")
    print(f"  User Name  : {profile_name}")
    print(f"  User ID    : {user_id}")
    print(f"  Email      : {email}")

    # Send Telegram Notification
    from reporting.telegram_bot import send_telegram_message
    msg = (
        f"🚀 <b>[HEADLESS TOTP AUTO-LOGIN VERIFIED]</b>\n"
        f"========================================\n"
        f"<b>User Name:</b> <code>{profile_name}</code>\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Mobile Reg:</b> <code>9699990215</code>\n"
        f"<b>Access Token:</b> <code>{token[:20]}...</code>\n"
        f"========================================\n"
        f"100% Unattended Headless TOTP 2FA Authentication is ONLINE and working!"
    )
    send_telegram_message(msg)
    print("\n[Telegram] Confirmation alert delivered to Telegram chat.")

print("==========================================================================")
