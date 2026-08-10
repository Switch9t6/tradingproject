import os
import sys
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner.smart_scanner import (
    detect_active_session,
    scan_nse_equities_and_indices,
    scan_mcx_crude_oil_multifactor,
    scan_smart_opportunities
)
from scanner.option_mapper import resolve_atm_option_contract

def test_smart_scanner_suite():
    print("=" * 75)
    print("          TESTING SMART DUAL-ENGINE SCANNER & OPTION MAPPER")
    print("=" * 75)

    # 1. Test Session Detection
    dt_nse = datetime.datetime(2026, 8, 10, 10, 15) # Mon 10:15 AM
    dt_mcx = datetime.datetime(2026, 8, 10, 19, 30) # Mon 07:30 PM
    dt_standby = datetime.datetime(2026, 8, 10, 16, 0) # Mon 04:00 PM

    assert detect_active_session(dt_nse) == "NSE_EQUITY"
    assert detect_active_session(dt_mcx) == "MCX_COMMODITY"
    assert detect_active_session(dt_standby) == "STANDBY"
    print("  [PASSED] Test 1: Session Router Auto-Detection Verified.")

    # 2. Test Engine A (NSE Equity 100-Pt Matrix)
    print("\n[Test 2] Executing Engine A (NSE Equity Scanner)...")
    cand_nse = scan_nse_equities_and_indices(top_3_sectors=["IT", "BANK", "AUTO"])
    print("  Engine A Result:", cand_nse)
    if cand_nse:
        score = cand_nse["composite_rating"]["composite_score"]
        assert score >= 75.0, "Qualified candidate must have score >= 75"
        opt_nse = resolve_atm_option_contract(cand_nse, max_budget=30000.0)
        assert opt_nse is not None
        print(f"  [PASSED] Engine A Candidate: {cand_nse['symbol']} (Score: {score}/100) -> Mapped Contract: {opt_nse['option_symbol']}")

    # 3. Test Engine B (MCX Crude Oil 100-Pt Matrix)
    print("\n[Test 3] Executing Engine B (MCX Commodity Scanner)...")
    cand_mcx = scan_mcx_crude_oil_multifactor()
    print("  Engine B Result:", cand_mcx)
    if cand_mcx:
        score = cand_mcx["composite_rating"]["composite_score"]
        assert score >= 75.0, "Qualified candidate must have score >= 75"
        
        # Test Standard Lot Mapping
        opt_std = resolve_atm_option_contract(cand_mcx, max_budget=15000.0)
        assert opt_std is not None
        assert opt_std["lot_size"] == 100
        print(f"  [PASSED] Engine B Standard Contract: {opt_std['option_symbol']} (Lot Size: 100 barrels, Total Cost: Rs {opt_std['total_lot_cost']:,.2f})")

        # Test Mini Lot Fallback Mapping
        opt_mini = resolve_atm_option_contract(cand_mcx, max_budget=1500.0)
        assert opt_mini is not None
        assert opt_mini["lot_size"] == 10
        assert opt_mini["underlying_symbol"] == "CRUDEOILM"
        print(f"  [PASSED] Engine B Mini Contract Fallback: {opt_mini['option_symbol']} (Lot Size: 10 barrels, Total Cost: Rs {opt_mini['total_lot_cost']:,.2f})")

    # 4. Test Unified Smart Scanner Router
    print("\n[Test 4] Executing Unified scan_smart_opportunities()...")
    res_router = scan_smart_opportunities(session_override="mcx")
    print("  Router Result:", res_router)
    assert res_router is not None

    print("\n" + "=" * 75)
    print("ALL SMART SCANNER INTELLIGENCE METRIC TESTS PASSED 100% PERFECTLY!")
    print("=" * 75)

if __name__ == "__main__":
    test_smart_scanner_suite()
