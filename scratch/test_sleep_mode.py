import sys, io, os, time, threading, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import execution.telegram_control as tc
import execution.position_monitor as pm
import execution.fyers_trader as ft
import execution.state_manager as sm

print("== TEST 1: automated EOD square-off SKIPPED during sleep mode ==")
sq_calls = []
ft.square_off_active_position = lambda **k: sq_calls.append(k) or {"status": "TRADED", "exit_premium": 12.0}
tc.is_bot_disabled = lambda: True
res = main.execute_hard_eod_squareoff(session_tag="1515")
assert res["status"] == "skipped_halted", res
assert len(sq_calls) == 0, sq_calls
print("PASS: skipped while asleep:", res)

print("== TEST 2: automated EOD square-off RUNS when awake ==")
tc.is_bot_disabled = lambda: False
res = main.execute_hard_eod_squareoff(session_tag="1515")
assert res["status"] == "TRADED", res
assert len(sq_calls) == 1, sq_calls
print("PASS: ran while awake:", res["status"])

print("== TEST 3: manual /squareoff allowed even during sleep mode ==")
tc.is_bot_disabled = lambda: True
res = main.execute_hard_eod_squareoff(session_tag="1515", allow_when_halted=True)
assert res["status"] == "TRADED", res
assert len(sq_calls) == 2, sq_calls
print("PASS: manual square-off bypasses sleep mode")

print("== TEST 4: position monitor defers exits during sleep, resumes after wake ==")
exits = []
real_execute = pm._execute_exit
pm._execute_exit = lambda *a, **k: exits.append(a) or True
pm._fetch_ltp = lambda c, s: 85.0        # below base stop -> exit signal every poll
pm._build_fyers_client = lambda t: None

class StubSM:
    def __init__(self, *a, **k):
        self.state = {"active_position": {"trade_id": 42}}
    def record_exit_trade(self, *a, **k):
        pass
sm.StateManager = StubSM

# Deterministic clock: Thursday 10:00 IST, EOD cutoff far away.
pm._get_ist_now = lambda: datetime.datetime(2026, 8, 13, 10, 0)
pm._monitor_cutoff_time = lambda ex: datetime.time(23, 59)

tc.is_bot_disabled = lambda: True        # start asleep
t = threading.Thread(
    target=pm._run_position_monitor,
    kwargs={"symbol": "NSE:NIFTY2681826600CE", "quantity": 65, "trade_id": 42,
            "entry_price": 100.0, "target_price": 125.0, "initial_sl": 88.0,
            "tick_size": 0.05, "access_token": None, "dry_run": False,
            "exchange": "NSE_FO", "poll_interval": 0.01},
    daemon=True,
)
t.start()
time.sleep(0.3)
assert len(exits) == 0, exits            # sleeping -> NO automated exit
print("PASS: no exit placed while asleep")

tc.is_bot_disabled = lambda: False       # wake with /resume or /start
time.sleep(0.6)
assert len(exits) == 1, exits            # resumed -> exit placed
assert exits[0][7] == "STOP_LOSS_HIT_-12%", exits[0]
print("PASS: exit placed after wake:", exits[0][7])
t.join(timeout=2)
pm._execute_exit = real_execute

print("\nALL TESTS PASSED")
