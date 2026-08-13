import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution import fyers_trader as ft

print("== TEST 1: _is_sell_side_trade identifies SELL side ==")
assert ft._is_sell_side_trade({"side": -1}), "side=-1 should be SELL"
assert ft._is_sell_side_trade({"side": "SELL"}), "side='SELL' should be SELL"
assert ft._is_sell_side_trade({"side": "sell"}), "side='sell' should be SELL"
assert not ft._is_sell_side_trade({"side": 1}), "side=1 should be BUY"
assert not ft._is_sell_side_trade({"side": "BUY"}), "side='BUY' should be BUY"
print("PASS: sell/buy side parsing OK")

print("== TEST 2: _external_exit_price_from_tradebook picks most recent SELL fill ==")
class FakeTradebook:
    def __init__(self, entries):
        self._entries = entries
    def tradebook(self):
        return {"s": "ok", "tradeBook": self._entries}

entries = [
    {"symbol": "MCX:CRUDEOILM26AUG6350CE", "side": 1, "filledQty": 10, "tradePrice": 90.0, "tradeTime": 100},
    {"symbol": "MCX:CRUDEOILM26AUG6350CE", "side": -1, "filledQty": 10, "tradePrice": 92.5, "tradeTime": 200},
    {"symbol": "MCX:CRUDEOILM26AUG6350CE", "side": -1, "filledQty": 10, "tradePrice": 95.0, "tradeTime": 300},
    {"symbol": "OTHER:SYM", "side": -1, "filledQty": 10, "tradePrice": 1.0, "tradeTime": 999},
]
price = ft._external_exit_price_from_tradebook(FakeTradebook(entries), "MCX:CRUDEOILM26AUG6350CE")
assert price == 95.0, price
print("PASS: exit price =", price)

print("== TEST 3: no sell fill -> falls back to quotes LTP ==")
class FakeQuotes:
    def quotes(self, data=None):
        return {"s": "ok", "d": [{"v": {"lp": 87.75}}]}
price = ft._external_exit_price_from_tradebook(FakeQuotes(), "MCX:CRUDEOILM26AUG6350CE")
assert price == 87.75, price
print("PASS: LTP fallback =", price)

print("== TEST 4: no data at all -> 0.0 ==")
class FakeEmpty:
    def tradebook(self):
        return {"s": "error"}
    def quotes(self, data=None):
        return {"s": "error"}
price = ft._external_exit_price_from_tradebook(FakeEmpty(), "MCX:CRUDEOILM26AUG6350CE")
assert price == 0.0, price
print("PASS: empty -> 0.0")

print("== TEST 5: detect_manual_exit_and_record records MANUAL_EXIT when broker no longer holds ==")
class FakeBroker:
    def positions(self):
        return {"s": "ok", "netPositions": []}  # no position -> manual exit
    def tradebook(self):
        return {"s": "ok", "tradeBook": [
            {"symbol": "MCX:CRUDEOILM26AUG6350CE", "side": -1, "filledQty": 10,
             "tradePrice": 93.4, "tradeTime": 500},
        ]}

recorded = []
class StubSM:
    def __init__(self, *a, **k):
        self.state = {"active_position": {
            "trade_id": 77, "quantity": 10,
            "fyers_symbol": "MCX:CRUDEOILM26AUG6350CE", "tick_size": 0.05,
        }}
    def record_exit_trade(self, trade_id, exit_premium, exit_reason):
        recorded.append((trade_id, exit_premium, exit_reason))

orig_sm = ft.StateManager
orig_fyers = ft.fyersModel.FyersModel
orig_token = ft.get_active_fyers_token
orig_alert = None
import reporting.telegram_bot as tb
orig_alert = tb.send_telegram_message
sent = []
tb.send_telegram_message = lambda text, parse_mode=None: sent.append(text)
ft.StateManager = StubSM
ft.fyersModel.FyersModel = lambda *a, **k: FakeBroker()
ft.get_active_fyers_token = lambda: "FAKE_TOKEN"
try:
    ok = ft.detect_manual_exit_and_record(StubSM().state["active_position"],
                                          access_token=None, send_telegram_alert=True)
    assert ok, "should detect manual exit"
    assert recorded == [(77, 93.4, "MANUAL_EXIT")], recorded
    assert any("MANUAL EXIT" in m and "93.40" in m for m in sent), sent
    print("PASS: recorded:", recorded)
    print("PASS: alert sent:", sent[0].splitlines()[1].strip())
finally:
    ft.StateManager = orig_sm
    ft.fyersModel.FyersModel = orig_fyers
    ft.get_active_fyers_token = orig_token
    if orig_alert is not None:
        tb.send_telegram_message = orig_alert

print("== TEST 6: broker STILL holds position -> not a manual exit ==")
class FakeBrokerHeld:
    def positions(self):
        return {"s": "ok", "netPositions": [
            {"symbol": "MCX:CRUDEOILM26AUG6350CE", "netQty": 10},
        ]}
ft.StateManager = StubSM
ft.fyersModel.FyersModel = lambda *a, **k: FakeBrokerHeld()
ft.get_active_fyers_token = lambda: "FAKE_TOKEN"
try:
    ok = ft.detect_manual_exit_and_record(StubSM().state["active_position"],
                                          access_token=None, send_telegram_alert=False)
    assert ok is False, "broker still holds position -> must not record"
    assert recorded == [(77, 93.4, "MANUAL_EXIT")], "no new record expected"
    print("PASS: no record, returned False")
finally:
    ft.StateManager = orig_sm
    ft.fyersModel.FyersModel = orig_fyers
    ft.get_active_fyers_token = orig_token

print("== TEST 7: no active position -> False without broker call ==")
called = []
class FakeBroker2:
    def positions(self):
        called.append(1)
        return {"s": "ok", "netPositions": []}
ft.fyersModel.FyersModel = lambda *a, **k: FakeBroker2()
ft.get_active_fyers_token = lambda: "FAKE_TOKEN"
try:
    ok = ft.detect_manual_exit_and_record(None, access_token=None, send_telegram_alert=False)
    assert ok is False and called == [], (ok, called)
    print("PASS: empty position short-circuits before broker call")
finally:
    ft.fyersModel.FyersModel = orig_fyers
    ft.get_active_fyers_token = orig_token

print("\nALL TESTS PASSED")
