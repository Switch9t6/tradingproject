import os
import sys
import socket
import urllib3.util.connection as urllib3_conn
import dotenv
import requests

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

# Force IPv4 socket resolution
def allowed_gai_family():
    return socket.AF_INET

urllib3_conn.allowed_gai_family = allowed_gai_family

from execution.upstox_trader import get_active_upstox_token, get_upstox_api_client
import upstox_client

print("Testing IPv4 forced socket connection to Upstox API...")

tok = get_active_upstox_token()
headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}

# Check outgoing IP via api.ipify.org
try:
    ip4 = requests.get("https://api.ipify.org", timeout=5).text
    print(f"Current Outgoing IPv4 Address: {ip4}")
except Exception as e:
    print(f"Error checking IPv4: {e}")

try:
    api_client = get_upstox_api_client(tok)
    user_api = upstox_client.UserApi(api_client)
    res = user_api.get_profile(api_version="2.0")
    print("Profile query via IPv4 SUCCESS!")
    pdata = getattr(res, "data", res)
    print("User Name:", getattr(pdata, "user_name", "N/A"))
    print("User ID  :", getattr(pdata, "user_id", "N/A"))
except Exception as ex:
    print(f"Profile query exception: {ex}")
