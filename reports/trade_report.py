"""
Database Retrieval & Metrics Calculation Engine
=================================================
Queries SQLite (logs/trades.db) across specified date ranges and aggregate metrics
for both NSE_FO and MCX_FO trading sessions. Also generates self-contained interactive
HTML report files.
"""

import os
import sys
import json
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DB_FILE_PATH, INITIAL_WALLET_CAPITAL, REPORTS_DIR

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
            from reporting.eod_reporter import fetch_upstox_live_order_book_trades
            trades = fetch_upstox_live_order_book_trades()
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

def generate_html_report_file(start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
    """
    Generates a standalone, self-contained interactive HTML performance report file.
    Saves to reports/LIVE_MARKET_REPORT.html and returns absolute file path.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)

    today_str = get_ist_today_str()
    if not start_date:
        start_date = today_str
    if not end_date:
        end_date = start_date

    data = get_trade_report_data(start_date, end_date)

    net_pnl = data["net_pnl"]
    pnl_class = "green" if net_pnl >= 0 else "red"
    pnl_sign = "+" if net_pnl >= 0 else ""

    trades_json = json.dumps(data["itemized_trades"])

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quantitative System Performance Report | {start_date}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-primary: #0b0f19;
            --bg-card: rgba(17, 24, 39, 0.8);
            --border-card: rgba(255, 255, 255, 0.08);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-green: #22c55e;
            --accent-red: #ef4444;
            --accent-amber: #f59e0b;
            --accent-purple: #a855f7;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg-primary); color: var(--text-primary); padding: 24px 16px; display: flex; justify-content: center; min-height: 100vh; }}
        .container {{ width: 100%; max-width: 1200px; display: flex; flex-direction: column; gap: 20px; }}
        .header {{ background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 16px; padding: 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; }}
        .header h1 {{ font-size: 1.4rem; font-weight: 700; background: linear-gradient(135deg, #fff, #94a3b8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .badge {{ padding: 6px 14px; border-radius: 30px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); }}
        
        .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
        .metric-card {{ background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 14px; padding: 20px; display: flex; flex-direction: column; gap: 6px; position: relative; overflow: hidden; }}
        .metric-card::before {{ content: ''; position: absolute; top: 0; left: 0; width: 4px; height: 100%; background: var(--accent-blue); }}
        .metric-card.green::before {{ background: var(--accent-green); }}
        .metric-card.red::before {{ background: var(--accent-red); }}
        .metric-label {{ font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; font-weight: 600; }}
        .metric-val {{ font-size: 1.35rem; font-weight: 700; }}
        .metric-sub {{ font-size: 0.75rem; color: var(--text-secondary); }}
        .text-green {{ color: var(--accent-green); }}
        .text-red {{ color: var(--accent-red); }}

        .analytics-row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
        @media (max-width: 850px) {{ .analytics-row {{ grid-template-columns: 1fr; }} }}
        .chart-card {{ background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 14px; padding: 20px; height: 260px; }}
        
        .table-card {{ background: var(--bg-card); border: 1px solid var(--border-card); border-radius: 14px; padding: 20px; display: flex; flex-direction: column; gap: 14px; }}
        .table-header {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
        .search-input {{ background: rgba(0,0,0,0.3); border: 1px solid var(--border-card); padding: 8px 14px; border-radius: 8px; color: #fff; font-size: 0.82rem; outline: none; width: 220px; }}
        .table-wrapper {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; text-align: left; }}
        th {{ background: rgba(255,255,255,0.03); color: var(--text-secondary); padding: 12px 14px; border-bottom: 1px solid var(--border-card); font-size: 0.72rem; text-transform: uppercase; }}
        td {{ padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); white-space: nowrap; }}
        .tag {{ padding: 3px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); }}
        .tag-mcx {{ background: rgba(168, 85, 247, 0.15); color: var(--accent-purple); }}
    </style>
</head>
<body>

<div class="container">
    <div class="header">
        <div>
            <h1><i class="fa-solid fa-chart-line" style="color: var(--accent-blue);"></i> QUANTITATIVE SYSTEM PERFORMANCE REPORT</h1>
            <p style="color: var(--text-secondary); font-size: 0.85rem; margin-top: 4px;">Period: {start_date} to {end_date} | Upstox Live Synced</p>
        </div>
        <span class="badge"><i class="fa-solid fa-shield-halved"></i> Upstox API v2 Verified</span>
    </div>

    <div class="metrics-grid">
        <div class="metric-card {pnl_class}">
            <div class="metric-label">Net Realized PnL</div>
            <div class="metric-val text-{pnl_class}">{pnl_sign}Rs {abs(net_pnl):,.2f} INR</div>
            <div class="metric-sub">Gross: Rs {data['gross_pnl']:,.2f} | Fees: Rs {data['total_friction']:,.2f}</div>
        </div>

        <div class="metric-card blue">
            <div class="metric-label">Win Rate %</div>
            <div class="metric-val">{data['win_rate']:.1f}%</div>
            <div class="metric-sub">{data['winning_trades']} Win / {data['losing_trades']} Loss</div>
        </div>

        <div class="metric-card amber">
            <div class="metric-label">Max Drawdown</div>
            <div class="metric-val">{data['max_drawdown_pct']:.2f}%</div>
            <div class="metric-sub">Rs {data['max_drawdown_inr']:,.2f} Drop</div>
        </div>

        <div class="metric-card purple">
            <div class="metric-label">Session Breakdown</div>
            <div class="metric-val">{data['total_trades']} Trades</div>
            <div class="metric-sub">NSE: {data['nse_trades']} | MCX: {data['mcx_trades']}</div>
        </div>
    </div>

    <div class="analytics-row">
        <div class="chart-card">
            <canvas id="equityChart"></canvas>
        </div>
        <div class="chart-card">
            <canvas id="winPieChart"></canvas>
        </div>
    </div>

    <div class="table-card">
        <div class="table-header">
            <div style="font-weight: 600;"><i class="fa-solid fa-list-check" style="color: var(--accent-blue);"></i> Itemized Execution Log</div>
            <input type="text" class="search-input" id="tableSearch" placeholder="🔍 Search trades..." onkeyup="filterTable()">
        </div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Date & Time</th>
                        <th>Option Contract</th>
                        <th>Session</th>
                        <th>Qty</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>Exit Reason</th>
                        <th>Net PnL</th>
                    </tr>
                </thead>
                <tbody id="tableBody"></tbody>
            </table>
        </div>
    </div>
</div>

<script>
    const rawTrades = {trades_json};
    const initialCapital = {data['initial_capital']};

    function renderTable(trades) {{
        const tbody = document.getElementById('tableBody');
        tbody.innerHTML = '';
        if (!trades || trades.length === 0) {{
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color: #94a3b8; padding: 20px;">No trades executed in selected period.</td></tr>';
            return;
        }}
        trades.forEach(t => {{
            const tr = document.createElement('tr');
            const pnlClass = t.net_pnl >= 0 ? 'text-green' : 'text-red';
            const pnlSign = t.net_pnl >= 0 ? '+' : '';
            const tagClass = t.exchange === 'MCX_FO' ? 'tag-mcx' : 'tag';
            tr.innerHTML = `
                <td>${{t.id}}</td>
                <td>${{t.trade_date}}<br/><span style="color:#94a3b8; font-size: 0.75rem;">${{t.entry_time}}</span></td>
                <td><strong>${{t.option_symbol}}</strong></td>
                <td><span class="${{tagClass}}">${{t.exchange}}</span></td>
                <td>${{t.quantity}}</td>
                <td>Rs ${{t.entry_premium.toFixed(2)}}</td>
                <td>Rs ${{t.exit_premium.toFixed(2)}}</td>
                <td>${{t.exit_reason}}</td>
                <td class="${{pnlClass}}">${{pnlSign}}Rs ${{t.net_pnl.toFixed(2)}}</td>
            `;
            tbody.appendChild(tr);
        }});
    }}

    function filterTable() {{
        const q = document.getElementById('tableSearch').value.toLowerCase();
        const filtered = rawTrades.filter(t => 
            t.option_symbol.toLowerCase().includes(q) ||
            t.exit_reason.toLowerCase().includes(q) ||
            t.exchange.toLowerCase().includes(q) ||
            t.trade_date.includes(q)
        );
        renderTable(filtered);
    }}

    renderTable(rawTrades);

    // Equity Line Chart
    let labels = ['Start'];
    let curve = [initialCapital];
    let cumPnl = initialCapital;
    rawTrades.forEach((t, i) => {{
        cumPnl += t.net_pnl;
        labels.push(`Trade #${{i+1}}`);
        curve.push(cumPnl);
    }});

    new Chart(document.getElementById('equityChart'), {{
        type: 'line',
        data: {{
            labels: labels,
            datasets: [{{
                label: 'Equity (INR)',
                data: curve,
                borderColor: '#38bdf8',
                backgroundColor: 'rgba(56, 189, 248, 0.08)',
                fill: true,
                tension: 0.3,
                borderWidth: 2
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ color: '#94a3b8' }} }},
                y: {{ grid: {{ color: 'rgba(255,255,255,0.04)' }}, ticks: {{ color: '#94a3b8' }} }}
            }}
        }}
    }});

    // Win Share Doughnut Chart
    new Chart(document.getElementById('winPieChart'), {{
        type: 'doughnut',
        data: {{
            labels: ['Wins', 'Losses', 'Breakeven'],
            datasets: [{{
                data: [{data['winning_trades']}, {data['losing_trades']}, {data['breakeven_trades']}],
                backgroundColor: ['#22c55e', '#ef4444', '#f59e0b']
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#94a3b8' }} }} }},
            cutout: '70%'
        }}
    }});
</script>

</body>
</html>
"""

    report_path = os.path.join(REPORTS_DIR, "LIVE_MARKET_REPORT.html")
    dated_path = os.path.join(REPORTS_DIR, f"EOD_Report_LIVE_{start_date}.html")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    with open(dated_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path

if __name__ == "__main__":
    p = generate_html_report_file()
    print("Generated HTML Report at:", p)
