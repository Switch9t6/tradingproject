import sys, io, os, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution import position_monitor as pm
from execution.position_monitor import PositionMonitor

ENTRY = 100.0
TARGET = round(ENTRY * 1.25, 2)   # 125.0
STOP = round(ENTRY * 0.88, 2)     # 88.0
DEFAULT = datetime.datetime(2026, 8, 13, 10, 0)  # Thursday 10:00 IST (mid-session)
LATE = datetime.time(23, 59)                       # never reached -> avoids EOD flakiness

pm._get_ist_now = lambda: DEFAULT
pm._monitor_cutoff_time = lambda ex: LATE

def new_mon():
    return PositionMonitor(entry_price=ENTRY, target_price=TARGET, initial_sl=STOP)

def tick(m, price, elapsed=10):
    return m.evaluate_tick(price, elapsed)

print("== TEST 1: Step-1 TSL moves SL to breakeven at +8% ==")
m = new_mon()
assert not tick(m, ENTRY * 1.09)[0]
assert m.current_sl == ENTRY, m.current_sl
print("PASS: stop moved to breakeven:", m.current_sl)

print("== TEST 2: retrace below breakeven -> TSL_STEP1_BREAKEVEN_HIT ==")
m = new_mon()
tick(m, ENTRY * 1.09)
hit, px, reason = tick(m, ENTRY * 0.99)
assert hit and reason == "TSL_STEP1_BREAKEVEN_HIT", (hit, px, reason)
print("PASS:", reason)

print("== TEST 3: Step-2 TSL locks +10% at +15% peak ==")
m = new_mon()
tick(m, ENTRY * 1.09)               # step-1: stop -> breakeven
tick(m, ENTRY * 1.16)               # step-2: stop -> lock +10%
assert abs(m.current_sl - round(ENTRY * 1.10, 2)) < 1e-9, m.current_sl
hit, px, reason = tick(m, ENTRY * 1.09)
assert hit and reason == "TSL_STEP2_PROFIT_LOCK_HIT", (hit, px, reason)
print("PASS:", reason)

print("== TEST 4: Target +25% triggers hard exit ==")
m = new_mon()
hit, px, reason = tick(m, TARGET)
assert hit and reason == "TARGET_HIT_+25%" and px == TARGET, (hit, px, reason)
print("PASS:", reason)

print("== TEST 5: Base stop loss -12% ==")
m = new_mon()
hit, px, reason = tick(m, ENTRY * 0.85)
assert hit and reason == "STOP_LOSS_HIT_-12%", (hit, px, reason)
print("PASS:", reason)

print("== TEST 6: Time-decay stagnation exit >20min below +5% ==")
m = new_mon()
hit, px, reason = tick(m, ENTRY * 1.02, elapsed=1805)
assert hit and reason == "TIME_EXIT_30MIN_STAGNANT", (hit, px, reason)
print("PASS:", reason)

print("== TEST 7: bullish winner above target still held until target (no spurious exit) ==")
m = new_mon()
hit, px, reason = tick(m, ENTRY * 1.24)
assert not hit, (hit, px, reason)
hit, px, reason = tick(m, TARGET)
assert hit and reason == "TARGET_HIT_+25%", (hit, px, reason)
print("PASS: held at +24%, exited at +25%")

print("== TEST 7b: state flags set on TSL steps ==")
m = new_mon()
assert not m.step1_breakeven_active and not m.step2_profit_lock_active
tick(m, ENTRY * 1.09)
assert m.step1_breakeven_active and not m.step2_profit_lock_active
tick(m, ENTRY * 1.16)
assert m.step2_profit_lock_active and m.peak_price == ENTRY * 1.16
print("PASS: step1_breakeven_active=True, step2_profit_lock_active=True, peak=", m.peak_price)

print("== TEST 7c: invalid ticks (zero/negative) ignored - no peak update, no exit ==")
m = new_mon()
assert not tick(m, 0.0)[0]
assert not tick(m, -5.0)[0]
assert m.peak_price == ENTRY and m.current_sl == STOP
print("PASS: invalid ticks ignored, state unchanged")

print("== TEST 7d: trailing SL is monotonic (max, never downgraded) ==")
m = new_mon()
tick(m, ENTRY * 1.16)               # step-1 + step-2 fire
step2_sl = m.current_sl
tick(m, ENTRY * 1.17)               # new peak, still only step-2 active
assert m.current_sl == step2_sl, m.current_sl
assert m.current_sl >= ENTRY * 1.10
print("PASS: SL stays at locked level:", m.current_sl)

print("== TEST 8: _run_position_monitor exits via triggered target ==")
import execution.state_manager as sm
class StubStateWithPosition:
    def __init__(self, *a, **k):
        self.state = {"active_position": {"trade_id": 999}}
    def record_exit_trade(self, *a, **k):
        pass
sm.StateManager = StubStateWithPosition
calls = []
real_execute = pm._execute_exit
pm._execute_exit = lambda *a, **k: calls.append(a) or True
pm._fetch_ltp = lambda c, s: TARGET + 1.0   # above target on first poll
pm._build_fyers_client = lambda t: None     # no real API
pm._run_position_monitor("NSE:NIFTY2681826600CE", 65, 999, ENTRY, TARGET, STOP, 0.05, None, False, "NSE_FO", 0.01)
pm._execute_exit = real_execute
assert len(calls) == 1, calls
args = calls[0]
assert args[0] == "NSE:NIFTY2681826600CE" and args[7] == "TARGET_HIT_+25%", args
print("PASS: monitor placed exit with reason:", args[7])

print("== TEST 9: _run_position_monitor stops when position no longer tracked ==")
class StubStateMismatch:
    def __init__(self, *a, **k):
        self.state = {"active_position": {"trade_id": 111}}
sm.StateManager = StubStateMismatch
pm._fetch_ltp = lambda c, s: 90.0   # below stop, but position already gone
start = time.time()
pm._run_position_monitor("MCX:CRUDEOILM26AUG6350CE", 10, 555, ENTRY, TARGET, STOP, 0.05, None, False, "MCX_FO", 0.01)
assert time.time() - start < 5
print("PASS: monitor exited without placing order")

print("== TEST 10: _execute_exit records closed trade on TRADED ==")
import execution.fyers_trader as ft
recorded = []
class StubSM:
    def __init__(self, *a, **k):
        self.state = {"active_position": {"trade_id": 777}}
    def record_exit_trade(self, trade_id, exit_premium, exit_reason="EXIT", friction_fees=0.0, **kwargs):
        recorded.append((trade_id, exit_premium, exit_reason))
sm.StateManager = StubSM
ft.place_aggressive_limit_order = lambda **k: {"status": "TRADED", "filled_price": 108.0, "order_id": "ORD_TSL"}
pm._fetch_ltp = lambda c, s: TARGET + 1.0
pm._build_fyers_client = lambda t: None
pm._run_position_monitor("MCX:CRUDEOILM26AUG6350CE", 10, 777, ENTRY, TARGET, STOP, 0.05, None, False, "MCX_FO", 0.01)
assert len(recorded) == 1 and recorded[0][0] == 777 and recorded[0][2] == "TARGET_HIT_+25%", recorded
print("PASS:", recorded)

print("== TEST 11: exit alert contains entry, exit, realized P&L and exact reason ==")
import reporting.telegram_bot as tb
sent = []
tb.send_telegram_message = lambda msg: sent.append(msg)
pm._send_exit_alert("MCX:CRUDEOILM26AUG6350CE", 10, 100.0, 125.0, 250.0, "TARGET_HIT_+25%", "ORD_X")
assert len(sent) == 1
for token in ("POSITION EXIT", "MCX:CRUDEOILM26AUG6350CE", "100.00", "125.00", "250.00", "TARGET_HIT_+25%", "ORD_X"):
    assert token in sent[0], (token, sent[0])
print("PASS: alert formatted with entry/exit/P&L/reason/order")

print("\nALL TESTS PASSED")
