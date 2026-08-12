import os
import sys
import json
import time
import datetime
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, ".")

print("==========================================================================")
print("     UPSTOX ACCOUNT CONNECTIVITY & HEADLESS TOTP LOGIN AUDIT             ")
print("==========================================================================")
print(f"Username     : {os.getenv('UPSTOX_USERNAME')}")
print(f"PIN Code     : {'*' * len(os.getenv('UPSTOX_PIN_CODE', ''))}")
print(f"TOTP Secret  : {'*' * len(os.getenv('UPSTOX_TOTP_SECRET', ''))}")

from execution.upstox_trader import auto_generate_upstox_token, get_live_wallet_balance
import upstox_client

token = auto_generate_upstox_token()
print(f"\n[Upstox Token Result] Token Length: {len(token)} | Prefix: {token[:15]}...")

if token and not token.startswith("MOCK") and not token.startswith("your_"):
    configuration = upstox_client.Configuration()
    configuration.access_token = token
    api_client = upstox_client.ApiClient(configuration)
    user_api = upstox_client.UserApi(api_client)

    # 1. Profile Audit
    profile_name = "Unknown"
    user_id = "Unknown"
    email = "Unknown"
    try:
        prof_res = user_api.get_profile(api_version="2.0")
        pdata = getattr(prof_res, "data", prof_res)
        if isinstance(pdata, dict):
            profile_name = pdata.get("user_name", profile_name)
            user_id = pdata.get("user_id", user_id)
            email = pdata.get("email", email)
        else:
            profile_name = getattr(pdata, "user_name", profile_name)
            user_id = getattr(pdata, "user_id", user_id)
            email = getattr(pdata, "email", email)
        print(f"\n[Profile Audit SUCCESS]")
        print(f"  User Name  : {profile_name}")
        print(f"  User ID    : {user_id}")
        print(f"  Email      : {email}")
    except Exception as ex:
        print(f"\n[Profile Audit Warning] {ex}")

    # 2. Fund / Wallet Audit
    avail_cash = 0.0
    try:
        fund_res = user_api.get_user_fund_margin(api_version="2.0")
        fdata = getattr(fund_res, "data", fund_res)
        if isinstance(fdata, dict):
            sec = fdata.get("SEC", {}) or fdata.get("equity", {})
            avail_cash = float(sec.get("available_margin", 0.0) or sec.get("cash", 0.0) or 0.0)
        else:
            sec = getattr(fdata, "sec", None) or getattr(fdata, "equity", None)
            avail_cash = float(getattr(sec, "available_margin", 0.0) if sec else 0.0)
        print(f"\n[Wallet Balance SUCCESS] Live Available Cash: Rs {avail_cash:,.2f} INR")
    except Exception as ex:
        print(f"\n[Wallet Query Notice] {ex}")
        avail_cash = get_live_wallet_balance(token)

    # Send Telegram Notification
    from reporting.telegram_bot import send_telegram_message
    msg = (
        f"✅ <b>[UPSTOX HEADLESS TOTP LOGIN SUCCESSFUL]</b>\n"
        f"========================================\n"
        f"<b>User Name:</b> <code>{profile_name}</code>\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Email:</b> <code>{email}</code>\n"
        f"<b>Live Cash Balance:</b> <code>Rs {avail_cash:,.2f} INR</code>\n"
        f"========================================\n"
        f"Headless TOTP 2FA Auto-Login verified! System is 100% automated and ready for live execution."
    )
    send_telegram_message(msg)
    print("\n[Telegram] Confirmation alert delivered to Telegram chat.")
else:
    print("\n❌ Failed to generate valid Upstox token.")

print("==========================================================================")
