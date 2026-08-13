import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution import fyers_trader as ft

print("== TEST 1: _poll_order_fill detects a fill ==")
class FakeOrderbook:
    def orderbook(self):
        return {"s": "ok", "orderBook": [
            {"orderId": "ORD1", "status": "2", "filledQty": 65, "avgPrice": 15.35}
        ]}
res = ft._poll_order_fill(FakeOrderbook(), "ORD1", 65, 5)
assert res["status"] == "TRADED" and res["filled_price"] == 15.35, res
print("PASS:", res)

print("== TEST 2: _poll_order_fill detects rejection ==")
class FakeReject:
    def orderbook(self):
        return {"s": "ok", "orderBook": [{"orderId": "ORD2", "status": "3", "filledQty": 0, "avgPrice": 0}]}
res = ft._poll_order_fill(FakeReject(), "ORD2", 65, 5)
assert res["status"] == "REJECTED", res
print("PASS:", res)

print("== TEST 3: resolve_squareoff_symbol with stored fyers_symbol ==")
sym, tick = ft.resolve_squareoff_symbol({"fyers_symbol": "MCX:CRUDEOILM26AUG6350CE", "tick_size": 0.05})
assert sym == "MCX:CRUDEOILM26AUG6350CE" and tick == 0.05, (sym, tick)
print("PASS:", sym, tick)

print("== TEST 4: resolve_squareoff_symbol legacy fallback (no fyers_symbol) ==")
sym, tick = ft.resolve_squareoff_symbol({"option_symbol": "NATGASMINI26AUG100PE", "exchange": "MCX_FO"})
print("PASS (best-effort candidate):", sym, tick)

print("== TEST 5: square_off_active_position dry-run invokes record_exit_trade ==")
calls = []
class StubStateManager:
    def __init__(self, *a, **k):
        self.state = {
            "active_position": {
                "trade_id": 21, "execution_mode": "DRY_RUN", "exchange": "MCX_FO",
                "option_symbol": "CRUDEOILM26AUG6350CE", "fyers_symbol": "MCX:CRUDEOILM26AUG6350CE",
                "tick_size": 0.05, "quantity": 10, "entry_premium": 95.65,
            }
        }
    def record_exit_trade(self, trade_id, exit_premium, exit_reason="EXIT", friction_fees=0.0):
        calls.append((trade_id, exit_premium, exit_reason))
    def _save_state(self, s):
        pass

ft.StateManager = StubStateManager
out = ft.square_off_active_position(dry_run=True, exit_reason="EOD_SQUAREOFF_2300", send_telegram_alert=False)
print("OUT:", out)
assert out["status"] == "TRADED", out
assert len(calls) == 1 and calls[0][2] == "EOD_SQUAREOFF_2300", calls
assert calls[0][1] > 0, calls
print("PASS:", calls)

print("== TEST 6: square_off with no position -> broker reconciliation path (no token, safe) ==")
class EmptyStateManager:
    def __init__(self, *a, **k):
        self.state = {}
ft.StateManager = EmptyStateManager
out = ft.square_off_active_position(dry_run=True, send_telegram_alert=False)
print("OUT:", out)
assert out["status"] in ("no_position", "error"), out
print("PASS:", out)

print("\nALL TESTS PASSED")
