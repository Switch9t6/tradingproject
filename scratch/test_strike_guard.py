import os
import sys
import datetime
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import option_mapper as om
from scanner.option_mapper import (
    _snap_to_strike,
    _lookup_with_deviation_guard,
    _expiry_iso_from_parts,
    _dynamic_usable_budget,
    last_mapping_error,
    get_mcx_crude_option_contract,
    resolve_atm_option_contract,
)


def _entry(strike, underlying="TEST", lot=100, expiry="2099-12-31"):
    sym = f"NSE:{underlying}26DEC2099{int(strike)}{'CE'}"
    return {
        "instrument_key": sym,
        "tradingsymbol": f"{underlying}26DEC2099{int(strike)}CE",
        "fyers_symbol": sym,
        "underlying": underlying,
        "lot_size": lot,
        "tick_size": 0.05,
        "strike": float(strike),
        "expiry": expiry,
        "option_type": "CE",
    }


def _mcx_entry(strike, underlying="CRUDEOIL", lot=100, expiry="2099-12-31"):
    sym = f"MCX:{underlying}26AUG2099{int(strike)}CE"
    return {
        "instrument_key": sym,
        "tradingsymbol": f"{underlying}26AUG2099{int(strike)}CE",
        "fyers_symbol": sym,
        "underlying": underlying,
        "lot_size": lot,
        "tick_size": 0.05,
        "strike": float(strike),
        "expiry": expiry,
        "option_type": "CE",
    }


class TestStrikeGuard(unittest.TestCase):

    def setUp(self):
        om._clear_mapping_error()

    def _fake_nse_map(self, strikes, underlying="TEST", lot=100):
        return {f"{underlying}_{int(s)}_CE": _entry(s, underlying, lot) for s in strikes}

    def test_half_up_snap(self):
        # 1526.94 on a 5-step -> 1525 (not banker's rounding weirdness)
        self.assertEqual(_snap_to_strike(1526.94, 5.0), 1525.0)
        self.assertEqual(_snap_to_strike(1527.50, 5.0), 1530.0)
        # .5 boundaries always round UP (half-away-from-zero)
        self.assertEqual(_snap_to_strike(2.5, 5.0), 5.0)
        self.assertEqual(_snap_to_strike(12.5, 25.0), 25.0)
        self.assertEqual(_snap_to_strike(6284.03, 50.0), 6300.0)

    def test_exact_hit(self):
        mp = self._fake_nse_map([1000, 1050, 1100])
        info = _lookup_with_deviation_guard(mp, "TEST", 1050.0, "CE", 50.0, "NSE")
        self.assertIsNotNone(info)
        self.assertEqual(info["strike"], 1050.0)
        self.assertIsNone(last_mapping_error())

    def test_nearest_within_guard(self):
        # Scanner heuristic says 5-pt step but real spacing is 50 (e.g. equity
        # that trades on 50-pt strikes): 1525 must map to the nearest 1550.
        mp = self._fake_nse_map([1500, 1550, 1600])
        info = _lookup_with_deviation_guard(mp, "TEST", 1525.0, "CE", 5.0, "NSE")
        self.assertIsNotNone(info)
        self.assertEqual(info["strike"], 1550.0)

    def test_far_deviation_rejected(self):
        # The MCX disaster scenario: spot ~7800 but master only has 6200/6250.
        mp = self._fake_nse_map([6200, 6250, 6300])
        info = _lookup_with_deviation_guard(mp, "TEST", 7800.0, "CE", 50.0, "NSE")
        self.assertIsNone(info)
        self.assertEqual(last_mapping_error(), "STRIKE_OUT_OF_BOUNDS_OR_MISSING")

    def test_expected_expiry_epoch_col(self):
        # parts[8] (and parts[18]) carry the real expiry epoch.
        row = ["", "X 25 Aug 26 100 CE", "1", "100", "0.05", "", "", "2026-08-12",
               "1787652600", "NSE:X26AUG100CE", "1", "1", "1", "X", "1", "100.0", "CE", "1", "None", "0"]
        self.assertEqual(_expiry_iso_from_parts(row), "2026-08-25")

    def test_expired_excluded_from_map(self):
        # Simulate what the map builder does: expired (expiry < today) are dropped.
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        mp = self._fake_nse_map([1000, 1050])
        mp[f"TEST_2000_CE"] = _entry(2000, "TEST", 100, "2020-01-01")  # expired contract
        info = _lookup_with_deviation_guard(mp, "TEST", 1050.0, "CE", 50.0, "NSE")
        self.assertIsNotNone(info)
        # expired contract must not be selected even if the map contained it
        self.assertLess(info["strike"], 2000)

    def test_dynamic_usable_budget(self):
        # No live token -> fall back to passed cap
        cap, src = _dynamic_usable_budget(10000.0, None)
        self.assertEqual(cap, 10000.0)
        self.assertEqual(src, "passed_cap")
        cap, src = _dynamic_usable_budget(None, None)
        self.assertEqual(cap, float("inf"))

    def test_mcx_standard_ok_and_mini_downgrade(self):
        om._fyers_mcx_cache = {
            "CRUDEOIL_6300_CE": _mcx_entry(6300, "CRUDEOIL", 100),
            "CRUDEOILM_6300_CE": _mcx_entry(6300, "CRUDEOILM", 10),
            "_cache_version": om._CACHE_VERSION,
        }
        om._fyers_nse_cache = {**self._fake_nse_map([1000]), "_cache_version": om._CACHE_VERSION}
        cand = {"symbol": "CRUDEOIL", "spot_price": 6284.03, "direction": "BULLISH",
                "option_type": "CE", "momentum_pct": 0.0}
        std = get_mcx_crude_option_contract(cand["spot_price"], cand["direction"],
                                            budget_cap=15000.0, option_type="CE",
                                            symbol_hint="CRUDEOIL", momentum_pct=0.0)
        self.assertIsNotNone(std)
        self.assertEqual(std["lot_size"], 100)
        mini = get_mcx_crude_option_contract(cand["spot_price"], cand["direction"],
                                             budget_cap=1500.0, option_type="CE",
                                             symbol_hint="CRUDEOIL", momentum_pct=0.0)
        self.assertIsNotNone(mini)
        self.assertEqual(mini["lot_size"], 10)
        self.assertEqual(mini["underlying_symbol"], "CRUDEOILM")

    def test_mcx_insufficient_wallet(self):
        om._fyers_mcx_cache = {
            "CRUDEOIL_6300_CE": _mcx_entry(6300, "CRUDEOIL", 100),
            "CRUDEOILM_6300_CE": _mcx_entry(6300, "CRUDEOILM", 10),
            "_cache_version": om._CACHE_VERSION,
        }
        om._fyers_nse_cache = {**self._fake_nse_map([1000]), "_cache_version": om._CACHE_VERSION}
        cand = {"symbol": "CRUDEOIL", "spot_price": 6284.03, "direction": "BULLISH",
                "option_type": "CE", "momentum_pct": 0.0}
        # Mini premium ~94.6 x 10 = 946 -> budget of 100 cannot afford it
        res = get_mcx_crude_option_contract(cand["spot_price"], cand["direction"],
                                            budget_cap=100.0, option_type="CE",
                                            symbol_hint="CRUDEOIL", momentum_pct=0.0)
        self.assertIsNone(res)
        self.assertEqual(last_mapping_error(), "INSUFFICIENT_WALLET_BALANCE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
