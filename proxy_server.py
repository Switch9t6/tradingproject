"""
Upstox Static IP Forward Proxy Server
=====================================
Lightweight, high-performance HTTPS tunneling & forward proxy server.
Run this script on the computer / network with static IP 110.226.176.243.
Railway (and cloud instances) will route all Upstox API calls through this proxy,
ensuring Upstox API receives all orders originating from your registered static IP.
"""

import os
import sys
import socket
import select
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

PORT = int(os.getenv("PROXY_PORT", "8888"))

class UpstoxProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[Proxy] {self.address_string()} - " + (format % args))

    def do_CONNECT(self):
        """HTTPS CONNECT tunneling method for secure SSL connections to api.upstox.com."""
        try:
            host_port = self.path.split(":")
            target_host = host_port[0]
            target_port = int(host_port[1]) if len(host_port) > 1 else 443
            
            target_sock = socket.create_connection((target_host, target_port), timeout=10)
            self.send_response(200, "Connection Established")
            self.end_headers()
            
            sockets = [self.connection, target_sock]
            while True:
                r_list, _, _ = select.select(sockets, [], [], 30)
                if not r_list:
                    break
                for s in r_list:
                    out = target_sock if s is self.connection else self.connection
                    data = s.recv(16384)
                    if not data:
                        return
                    out.sendall(data)
        except Exception as err:
            try:
                self.send_error(502, f"Bad Gateway: {err}")
            except Exception:
                pass

    def do_GET(self):
        self._proxy_http("GET")

    def do_POST(self):
        self._proxy_http("POST")

    def do_PUT(self):
        self._proxy_http("PUT")

    def do_DELETE(self):
        self._proxy_http("DELETE")

    def _proxy_http(self, method):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else None
            
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ["host", "proxy-connection"]}
            
            req = urllib.request.Request(self.path, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as err:
            try:
                self.send_error(502, f"Proxy Error: {err}")
            except Exception:
                pass


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

    server = HTTPServer(("0.0.0.0", PORT), UpstoxProxyHandler)
    print("==========================================================================")
    print(f"  UPSTOX STATIC IP PROXY SERVER ONLINE (PORT {PORT})                      ")
    print("  Listening on 0.0.0.0 for Railway / Cloud Requests                        ")
    print("==========================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy server shut down cleanly.")

if __name__ == "__main__":
    main()
