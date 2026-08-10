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

class CleanCallbackHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global captured_code
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        if "code" in query_params:
            captured_code = query_params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:50px;'><h2>Upstox Authentication Successful!</h2><p>You can close this tab now.</p></body></html>")

def main():
    global captured_code
    print("[OAuth] Launching Upstox Authentication Flow...")
    auth_url = f"{UPSTOX_AUTH_URL}?client_id={UPSTOX_API_KEY}&redirect_uri={REDIRECT_URI}&response_type=code"
    webbrowser.open(auth_url)
    
    port = 5000
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), CleanCallbackHandler) as httpd:
        print(f"[OAuth] Waiting for browser callback on http://127.0.0.1:{port}/callback ...")
        httpd.handle_request()

    if captured_code:
        print(f"[OAuth] Code captured: {captured_code[:10]}...")
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
            print("[SUCCESS] Real Upstox Access Token Saved to access_token.json!")
            return access_token
        else:
            print(f"[ERROR] Token Exchange Failed (HTTP {res.status_code}): {res.text}")
    return None

if __name__ == "__main__":
    main()
