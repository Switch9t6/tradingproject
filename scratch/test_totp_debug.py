import os
import sys
import traceback
import dotenv
from urllib.parse import urlparse, parse_qs
import requests

dotenv.load_dotenv(override=True)

from upstox_totp import UpstoxTOTP

username = os.getenv("UPSTOX_USERNAME", "9699990215").strip()
pin = os.getenv("UPSTOX_PIN_CODE", "").strip()
secret = os.getenv("UPSTOX_TOTP_SECRET", "").strip()
cid = os.getenv("UPSTOX_CLIENT_ID", "").strip() or os.getenv("UPSTOX_API_KEY", "").strip()
csec = os.getenv("UPSTOX_CLIENT_SECRET", "").strip() or os.getenv("UPSTOX_API_SECRET", "").strip()
ruri = os.getenv("UPSTOX_REDIRECT_URI", "https://localhost").strip()

upx = UpstoxTOTP(
    username=username,
    pin_code=pin,
    totp_secret=secret,
    client_id=cid,
    client_secret=csec,
    redirect_uri=ruri
)

print("Testing Method 2: oauth_authorization()...")
try:
    oauth_res = upx.app_token.oauth_authorization()
    print("OAuth Res type:", type(oauth_res))
    print("OAuth Res:", oauth_res)
    if oauth_res and oauth_res.data and oauth_res.data.redirectUri:
        redirect_uri = oauth_res.data.redirectUri
        print("Redirect URI from OAuth:", redirect_uri)
        parsed = urlparse(redirect_uri)
        params = parse_qs(parsed.query)
        code_list = params.get("code")
        print("Code List:", code_list)
        if code_list:
            code = code_list[0]
            token_url = "https://api.upstox.com/v2/login/authorization/token"
            headers = {"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
            post_data = {
                "code": code,
                "client_id": cid,
                "client_secret": csec,
                "redirect_uri": ruri,
                "grant_type": "authorization_code"
            }
            print("Posting to token_url with code...")
            tok_resp = requests.post(token_url, headers=headers, data=post_data, timeout=10)
            print("Token resp status:", tok_resp.status_code)
            print("Token resp body:", tok_resp.text)
except Exception as e:
    print("\nEXCEPTION IN METHOD 2:")
    traceback.print_exc()
