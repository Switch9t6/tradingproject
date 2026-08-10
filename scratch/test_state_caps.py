import os
import sys
import json
import tempfile
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.state_manager import StateManager

def test_session_isolated_caps():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "state.json")
        db_file = os.path.join(tmpdir, "trades.db")

        sm = StateManager(db_path=db_file, state_path=state_file, force_reset=True)

        print("[Test 1] Fresh State Structure Verification")
        print("  Initial state:", sm.state)
        assert sm.state.get("NSE_FO_trades_today") == 0
        assert sm.state.get("MCX_FO_trades_today") == 0
        assert sm.state.get("is_nse_locked_today") is False
        assert sm.state.get("is_mcx_locked_today") is False
        assert sm.is_trade_allowed_today("NSE_FO") is True
        assert sm.is_trade_allowed_today("MCX_FO") is True
        print("  PASSED: Initial state clean.")

        print("\n[Test 2] Execute 1 NSE_FO Trade Entry & Exit")
        nse_contract = {
            "underlying_symbol": "BANKBARODA",
            "option_symbol": "BANKBARODA_250_CE",
            "option_type": "CE",
            "strike_price": 250.0,
            "lot_size": 2925
        }
        t_id = sm.record_entry_trade(nse_contract, entry_premium=10.0, target_p=12.5, stop_p=8.8, execution_mode="DRY_RUN", exchange="NSE_FO")
        print(f"  NSE Trade Entry recorded (ID: {t_id}). State:", sm.state)
        assert sm.state.get("NSE_FO_trades_today") == 1
        assert sm.state.get("MCX_FO_trades_today") == 0
        assert sm.is_trade_allowed_today("NSE_FO") is False, "NSE_FO should be BLOCKED after 1 trade"
        assert sm.is_trade_allowed_today("MCX_FO") is True, "MCX_FO MUST STILL BE ALLOWED (Session Isolated!)"
        print("  PASSED: NSE_FO blocked, MCX_FO still allowed!")

        sm.record_exit_trade(t_id, exit_premium=12.5, exit_reason="TARGET_HIT_25PCT")
        print("  NSE Trade Exit recorded. State:", sm.state)
        assert sm.state.get("is_nse_locked_today") is True
        assert sm.is_trade_allowed_today("NSE_FO") is False

        print("\n[Test 3] Execute 1 MCX_FO Trade Entry & Exit")
        mcx_contract = {
            "underlying_symbol": "CRUDEOIL",
            "option_symbol": "CRUDEOIL_6250_CE",
            "option_type": "CE",
            "strike_price": 6250.0,
            "lot_size": 100,
            "is_mcx": True
        }
        mcx_id = sm.record_entry_trade(mcx_contract, entry_premium=94.0, target_p=117.5, stop_p=82.7, execution_mode="DRY_RUN", exchange="MCX_FO")
        print(f"  MCX Trade Entry recorded (ID: {mcx_id}). State:", sm.state)
        assert sm.state.get("MCX_FO_trades_today") == 1
        assert sm.state.get("NSE_FO_trades_today") == 1
        assert sm.is_trade_allowed_today("MCX_FO") is False, "MCX_FO should now be BLOCKED after 1 trade"

        sm.record_exit_trade(mcx_id, exit_premium=117.5, exit_reason="TARGET_HIT_25PCT")
        print("  MCX Trade Exit recorded. State:", sm.state)
        assert sm.state.get("is_mcx_locked_today") is True

        print("\n[Test 4] Midnight IST Date Reset Verification")
        sm.state["date"] = "2026-08-01"
        sm.state["last_reset_date"] = "2026-08-01"
        sm._save_state(sm.state)

        sm._check_date_reset()
        print("  State after midnight reset simulation:", sm.state)
        assert sm.state.get("NSE_FO_trades_today") == 0
        assert sm.state.get("MCX_FO_trades_today") == 0
        assert sm.state.get("is_nse_locked_today") is False
        assert sm.state.get("is_mcx_locked_today") is False
        assert sm.is_trade_allowed_today("NSE_FO") is True
        assert sm.is_trade_allowed_today("MCX_FO") is True
        print("  PASSED: Midnight reset restored all session caps to 0!")

        print("\n" + "=" * 60)
        print("ALL SESSION ISOLATED TRADE CAP TESTS PASSED 100% PERFECTLY!")
        print("=" * 60)

if __name__ == "__main__":
    test_session_isolated_caps()
