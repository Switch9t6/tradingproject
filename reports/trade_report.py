"""
Database Retrieval & Metrics Calculation Engine
=================================================
Queries SQLite (logs/trades.db) across specified date ranges and aggregate metrics
for both NSE_FO and MCX_FO trading sessions.
"""

import os
import sys
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DB_FILE_PATH, INITIAL_WALLET_CAPITAL

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_today_str() -> str:
    """Returns current date string in YYYY-MM-DD format (IST)."""
    return datetime.datetime.now(IST_TZ).date().isoformat()

def validate_date_range(start_date: str, end_date: str) -> Tuple[bool, str]:
    """
    Validates start_date and end_date YYYY-MM-DD format and range constraints (1 day to 365 days).
    Returns (is_valid, error_message).
    """
    try:
        s_date = datetime.date.fromisoformat(start_date)
        e_date = datetime.date.fromisoformat(end_date)
    except ValueError:
        return False, "Invalid date format. Expected YYYY-MM-DD."

    if e_date < s_date:
        return False, "End date cannot be earlier than start date."

    delta_days = (e_date - s_date).days + 1
    if delta_days > 365:
        return False, "Maximum date range limit is 1 year (365 days)."
    if delta_days < 1:
        return False, "Minimum date range is 1 day."

    return True, ""

def get_trade_report_data(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    execution_mode: Optional[str] = None
) -> Dict[str, Any]:
    """
    Queries logs/trades.db for trades executed between start_date and end_date (inclusive).
    Aggregates performance metrics across NSE_FO and MCX_FO exchange segments.

    :param start_date: ISO date string 'YYYY-MM-DD' (defaults to today)
    :param end_date: ISO date string 'YYYY-MM-DD' (defaults to start_date)
    :param execution_mode: Optional filter ('LIVE', 'DRY_RUN', or None for all)
    :return: Dict containing summary metrics and itemized trade list
    """
    today_str = get_ist_today_str()
    if not start_date:
        start_date = today_str
    if not end_date:
        end_date = start_date

    is_valid, err_msg = validate_date_range(start_date, end_date)
    if not is_valid:
        raise ValueError(err_msg)

    trades: List[Dict[str, Any]] = []
    
    # Query SQLite Database
    if os.path.exists(DB_FILE_PATH):
        try:
            conn = sqlite3.connect(DB_FILE_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM trades WHERE trade_date >= ? AND trade_date <= ?"
            params: List[Any] = [start_date, end_date]

            if execution_mode:
                query += " AND execution_mode = ?"
                params.append(execution_mode)

            query += " ORDER BY trade_date ASC, entry_time ASC"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            trades = [dict(r) for r in rows]
            conn.close()
        except Exception as db_err:
            print(f"[TradeReport DB Error] {db_err}")

    # Fallback for Today's Live Trades if DB is empty and start_date == today
    if not trades and start_date == today_str and end_date == today_str:
        try:
            from reporting.eod_reporter import fetch_dhan_live_order_book_trades
            trades = fetch_dhan_live_order_book_trades()
        except Exception:
            pass

    # Aggregations & Analytics
    total_trades = len(trades)
    nse_trades = 0
    mcx_trades = 0
    winning_trades = 0
    losing_trades = 0
    breakeven_trades = 0

    gross_profit = 0.0
    gross_loss = 0.0
    gross_pnl = 0.0
    total_friction = 0.0
    net_pnl = 0.0

    # Drawdown calculation variables
    initial_capital = float(INITIAL_WALLET_CAPITAL)
    running_equity = initial_capital
    peak_equity = initial_capital
    max_drawdown_inr = 0.0
    max_drawdown_pct = 0.0

    itemized_trades = []

    for idx, t in enumerate(trades, 1):
        # Session Segment Identification
        exch = str(t.get("exchange") or "").upper()
        sym = str(t.get("option_symbol") or t.get("underlying_symbol") or "").upper()
        if "MCX" in exch or "CRUDE" in sym:
            session_tag = "MCX_FO"
            mcx_trades += 1
        else:
            session_tag = "NSE_FO"
            nse_trades += 1

        t_gross = float(t.get("gross_pnl") or 0.0)
        t_friction = float(t.get("friction_fees") or 0.0)
        t_net = float(t.get("net_pnl") or (t_gross - t_friction))

        gross_pnl += t_gross
        total_friction += t_friction
        net_pnl += t_net

        if t_net > 0:
            winning_trades += 1
            gross_profit += t_net
        elif t_net < 0:
            losing_trades += 1
            gross_loss += abs(t_net)
        else:
            breakeven_trades += 1

        # Track Drawdown
        running_equity += t_net
        if running_equity > peak_equity:
            peak_equity = running_equity
        current_dd_inr = peak_equity - running_equity
        current_dd_pct = (current_dd_inr / peak_equity * 100.0) if peak_equity > 0 else 0.0

        if current_dd_inr > max_drawdown_inr:
            max_drawdown_inr = current_dd_inr
        if current_dd_pct > max_drawdown_pct:
            max_drawdown_pct = current_dd_pct

        itemized_trades.append({
            "id": t.get("id", idx),
            "trade_date": t.get("trade_date", start_date),
            "entry_time": t.get("entry_time", "N/A"),
            "exit_time": t.get("exit_time", "N/A"),
            "execution_mode": t.get("execution_mode", "LIVE"),
            "underlying_symbol": t.get("underlying_symbol", "N/A"),
            "option_symbol": t.get("option_symbol", "N/A"),
            "option_type": t.get("option_type", "CE"),
            "strike_price": float(t.get("strike_price") or 0.0),
            "quantity": int(t.get("quantity") or 0),
            "entry_premium": float(t.get("entry_premium") or 0.0),
            "exit_premium": float(t.get("exit_premium") or 0.0),
            "target_price": float(t.get("target_price") or 0.0),
            "stop_price": float(t.get("stop_price") or 0.0),
            "gross_pnl": round(t_gross, 2),
            "friction_fees": round(t_friction, 2),
            "net_pnl": round(t_net, 2),
            "status": t.get("status", "CLOSED"),
            "exit_reason": t.get("exit_reason", "N/A"),
            "exchange": session_tag
        })

    win_rate = round((winning_trades / total_trades * 100.0), 2) if total_trades > 0 else 0.0
    profit_factor = round((gross_profit / gross_loss), 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_trades": total_trades,
        "nse_trades": nse_trades,
        "mcx_trades": mcx_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "breakeven_trades": breakeven_trades,
        "win_rate": win_rate,
        "gross_pnl": round(gross_pnl, 2),
        "total_friction": round(total_friction, 2),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": profit_factor,
        "max_drawdown_inr": round(max_drawdown_inr, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "initial_capital": initial_capital,
        "itemized_trades": itemized_trades
    }

if __name__ == "__main__":
    data = get_trade_report_data()
    print("Report Data Test:", data)
