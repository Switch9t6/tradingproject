import os
import sys
import json
import dotenv
import requests

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

from execution.fyers_trader import verify_and_fetch_live_fyers_balance, FyersTrader

print("==========================================================================")
print("             FYERS API V3 CONNECTIVITY & BALANCE AUDIT                    ")
print("==========================================================================")

app_id = os.getenv("FYERS_APP_ID", "")
tok = os.getenv("FYERS_ACCESS_TOKEN", "")

print(f"FYERS_APP_ID       : {app_id if app_id else 'NOT SET'}")
print(f"FYERS_ACCESS_TOKEN : {tok[:15] + '...' if tok else 'NOT SET'}")

is_verified, bal, status_msg = verify_and_fetch_live_fyers_balance()

print(f"\nFyers Verification Results:")
print(f"  Verified : {is_verified}")
print(f"  Balance  : Rs {bal:,.2f} INR")
print(f"  Status   : {status_msg}")
print("==========================================================================")
