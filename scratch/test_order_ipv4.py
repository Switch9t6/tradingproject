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

tok = get_active_upstox_token()
api_client = get_upstox_api_client(tok)
order_api = upstox_client.OrderApi(api_client)

# Check outgoing IP
ip4 = requests.get("https://api.ipify.org", timeout=5).text
print(f"Current Outgoing IPv4: {ip4}")

# Attempt place order request with test params
body = upstox_client.PlaceOrderRequest(
    quantity=60,
    product="I",
    validity="DAY",
    price=19.75,
    tag="TEST",
    instrument_token="NSE_FO|59926",
    order_type="LIMIT",
    transaction_type="BUY",
    disclosed_quantity=0,
    trigger_price=0.0,
    is_amo=False
)

try:
    print("Testing place_order API call via IPv4...")
    resp = order_api.place_order(body, api_version="2.0")
    print("Order Placement SUCCESS:", resp)
except upstox_client.rest.ApiException as ae:
    print(f"ApiException Status: {ae.status}")
    print(f"ApiException Body  : {ae.body}")
except Exception as ex:
    print(f"Exception: {ex}")
