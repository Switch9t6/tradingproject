"""
Unified Friction & Net PnL Calculator for Upstox Options Trading
Calculates accurate roundtrip transaction fees (Brokerage, STT, Exchange Fees, GST, Stamp Duty, SEBI)
based on official NSE FnO tax rates.
"""

from typing import Dict, Any

def calculate_trade_friction(quantity: int, entry_premium: float, exit_premium: float) -> Dict[str, float]:
    """
    Computes exact roundtrip friction costs and net PnL for an options trade.

    Parameters:
        quantity (int): Number of shares / option contracts (e.g. 65 shares for NIFTY)
        entry_premium (float): Entry price per share
        exit_premium (float): Exit price per share

    Returns:
        Dict[str, float]: {
            "gross_pnl": float,
            "buy_turnover": float,
            "sell_turnover": float,
            "total_turnover": float,
            "brokerage": float,
            "stt": float,
            "exchange_fee": float,
            "gst": float,
            "sebi_fee": float,
            "stamp_duty": float,
            "total_friction": float,
            "net_pnl": float
        }
    """
    if quantity <= 0:
        return {
            "gross_pnl": 0.0, "buy_turnover": 0.0, "sell_turnover": 0.0, "total_turnover": 0.0,
            "brokerage": 0.0, "stt": 0.0, "exchange_fee": 0.0, "gst": 0.0, "sebi_fee": 0.0,
            "stamp_duty": 0.0, "total_friction": 0.0, "net_pnl": 0.0
        }

    buy_turnover = round(entry_premium * quantity, 2)
    sell_turnover = round(exit_premium * quantity, 2)
    total_turnover = round(buy_turnover + sell_turnover, 2)
    gross_pnl = round(sell_turnover - buy_turnover, 2)

    # 1. Flat Brokerage: Rs 20 per executed leg (BUY = 20, SELL = 20 => Rs 40 roundtrip)
    brokerage = 40.0

    # 2. STT (Securities Transaction Tax): 0.0625% on option sell premium turnover
    stt = round(sell_turnover * 0.000625, 2)

    # 3. Exchange Turnover Fee (NSE Options): 0.053% on total premium turnover
    exchange_fee = round(total_turnover * 0.00053, 2)

    # 4. GST: 18% on (Brokerage + Exchange Turnover Fee)
    gst = round((brokerage + exchange_fee) * 0.18, 2)

    # 5. Stamp Duty: 0.003% on BUY side premium turnover
    stamp_duty = round(buy_turnover * 0.00003, 2)

    # 6. SEBI Turnover Charges: Rs 10 per crore (0.0001% on total turnover)
    sebi_fee = round(total_turnover * 0.000001, 2)

    total_friction = round(brokerage + stt + exchange_fee + gst + stamp_duty + sebi_fee, 2)
    net_pnl = round(gross_pnl - total_friction, 2)

    return {
        "gross_pnl": gross_pnl,
        "buy_turnover": buy_turnover,
        "sell_turnover": sell_turnover,
        "total_turnover": total_turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_fee": exchange_fee,
        "gst": gst,
        "sebi_fee": sebi_fee,
        "stamp_duty": stamp_duty,
        "total_friction": total_friction,
        "net_pnl": net_pnl
    }
