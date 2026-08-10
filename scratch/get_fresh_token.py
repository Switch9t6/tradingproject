import os
import sys
import json
import time
import datetime
import requests
import http.server
import socketserver
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import UPSTOX_API_KEY, UPSTOX_API_SECRET, REDIRECT_URI, UPSTOX_AUTH_URL, UPSTOX_TOKEN_URL, TOKEN_FILE_PATH

captured_code = None

class CallbackHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global captured_code
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        if "code" in query_params:
            captured_code = query_params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authentication Successful! You can close this window.</h2></body></html>")

def get_token():
    global captured_code
    auth_url = f"{UPSTOX_AUTH_URL}?client_id={UPSTOX_API_KEY}&redirect_uri={REDIRECT_URI}&response_type=code"
    print("\n==========================================================================")
    print("  PLEASE OPEN THIS URL IN YOUR BROWSER TO GENERATE A FRESH UPSTOX TOKEN:")
    print("==========================================================================")
    print(auth_url)
    print("==========================================================================\n")
    
    webbrowser.open(auth_url)
    
    port = 5000
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), CallbackHandler) as httpd:
        print(f"Waiting for login callback on http://127.0.0.1:{port}/callback ...")
        httpd.handle_request()
        
    if captured_code:
        print(f"\n[Captured Code] {captured_code[:10]}...")
        headers = {
            "accept": "application/json",
            "Api-Version": "2.0",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        payload = {
            "code": captured_code.strip(),
            "client_id": UPSTOX_API_KEY.strip(),
            "client_secret": UPSTOX_API_SECRET.strip(),
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code"
        }
        res = requests.post(UPSTOX_TOKEN_URL, headers=headers, data=payload, timeout=15)
        if res.status_code == 200:
            token_data = res.json()
            access_token = token_data.get("access_token")
            token_info = {
                "date": datetime.date.today().isoformat(),
                "access_token": access_token,
                "token_type": "Bearer",
                "created_at": time.time()
            }
            with open(TOKEN_FILE_PATH, "w") as f:
                json.dump(token_info, f, indent=4)
            with open("access_token.txt", "w") as f:
                f.write(access_token or "")
            print("\n[SUCCESS] Fresh Upstox Access Token Saved!")
            print(f"Token: {access_token[:15]}...")
            return access_token
        else:
            print(f"[Token Exchange Error] ({res.status_code}): {res.text}")
    return None

if __name__ == "__main__":
    get_token()
