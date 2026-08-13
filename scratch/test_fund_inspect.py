import os
import sys
import dotenv
import upstox_client

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

from execution.upstox_trader import get_active_upstox_token, get_upstox_api_client

tok = get_active_upstox_token()
api_client = get_upstox_api_client(tok)
user_api = upstox_client.UserApi(api_client)

res = user_api.get_user_fund_margin(api_version="2.0")
print("res.data:", res.data)
