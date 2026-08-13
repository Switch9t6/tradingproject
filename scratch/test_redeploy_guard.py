import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution import fyers_trader as ft

print("== TEST 1: is_segment_activation_rejection keyword detection ==")
assert ft.is_segment_activation_rejection("REJECTED", "Not permitted to trade in this segment") is True
assert ft.is_segment_activation_rejection("REJECTED", "Insufficient margin") is False
assert ft.is_segment_activation_rejection("PENDING", "segment") is False
print("PASS")

print("== TEST 2: mark/clear segment disabled (isolated, no real state touched) ==")
import tempfile
tmpdir = tempfile.mkdtemp()
import config.settings as settings
settings.SEGMENT_DISABLED_FLAG_PATTERN = os.path.join(tmpdir, "segment_disabled_{segment}.flag")
import execution.state_manager as sm_mod
real_init = sm_mod.StateManager.__init__
sm_mod.StateManager.__init__ = lambda self, *a, **k: None
sm_mod.StateManager.state = {"disabled_segments": {}}
sm_mod.StateManager._save_state = lambda self, s: None
ft.StateManager = sm_mod.StateManager
ft.mark_segment_disabled("MCX_FO", "REJECTED: not permitted")
assert ft.segment_disabled_today("MCX_FO") is True
assert ft.segment_disabled_today("NSE_FO") is False
sm_mod.StateManager.__init__ = real_init
print("PASS")

print("== TEST 3: LIVE broker check (read-only, real token) ==")
state = ft.check_broker_trade_executed_today()
print("checked:", state.get("checked"), "| blocked:", state.get("blocked"), "| reason:", state.get("reason"))
print("details:", state.get("details"))
# Expect: checked=True, and currently blocked=False (no trades/positions on the account)
assert state.get("checked") is True, "broker check did not complete against live API"
print("PASS (live read-only positions+tradebook parsed OK)")

print("\nALL TESTS PASSED")
