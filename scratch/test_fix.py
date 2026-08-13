import os
import sys
import json
import time
import datetime
from urllib.parse import urlparse, parse_qs
import requests
import dotenv

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

from upstox_totp import UpstoxTOTP
import upstox_client
from execution.upstox_trader import verify_and_fetch_live_upstox_balance, auto_generate_upstox_token

print("==========================================================================")
print("             TESTING FIXED TOTP AUTO-LOGIN & PRE-FLIGHT GATE              ")
print("==========================================================================")

# Force a fresh token generation
token = auto_generate_upstox_token(force=True)
print(f"\nFresh Generated Token: {token[:20]}... (Len: {len(token)})")

is_verified, bal, msg = verify_and_fetch_live_upstox_balance(access_token=token)
print(f"\nPre-flight Verification Result:")
print(f"  Verified : {is_verified}")
print(f"  Balance  : Rs {bal:,.2f} INR")
print(f"  Status   : {msg}")
print("==========================================================================")
