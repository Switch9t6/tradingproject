"""
Upstox Trader Proxy Module (Deprecation & Migration Proxy)
Delegates all execution and fund inspection calls directly to DhanTrader (dhanhq SDK v2).
"""
from execution.dhan_trader import DhanTrader, renew_dhan_access_token

# Backward compatibility alias
UpstoxOptionsTrader = DhanTrader
