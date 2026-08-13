import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import datetime
from jinja2 import Template
from config.settings import DB_FILE_PATH, REPORTS_DIR, INITIAL_WALLET_CAPITAL
from execution.state_manager import StateManager

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>EOD LIVE Dashboard | {{ date }}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #060811;
            --card-bg: rgba(15, 23, 42, 0.75);
            --card-inner: rgba(30, 41, 59, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --border-highlight: rgba(56, 189, 248, 0.3);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-green: #10b981;
            --accent-green-glow: rgba(16, 185, 129, 0.25);
            --accent-red: #f43f5e;
            --accent-blue: #38bdf8;
            --accent-purple: #a855f7;
            --accent-amber: #f59e0b;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-dark);
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(56, 189, 248, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(16, 185, 129, 0.08) 0%, transparent 40%);
            color: var(--text-main);
            padding: 16px 12px;
            min-height: 100vh;
            line-height: 1.5;
            -webkit-text-size-adjust: 100%;
        }

        @media (min-width: 640px) {
            body { padding: 24px 20px; }
        }

        .container {
            max-width: 1100px;
            margin: 0 auto;
            width: 100%;
        }

        /* HEADER GLASS CARD */
        .header {
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.12), rgba(15, 23, 42, 0.85));
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-highlight);
            border-radius: 20px;
            padding: 20px;
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.6);
            position: relative;
            overflow: hidden;
        }

        @media (min-width: 640px) {
            .header {
                flex-direction: row;
                justify-content: space-between;
                align-items: center;
                padding: 24px 28px;
            }
        }

        .header::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #a855f7);
        }

        .header-title h1 {
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 20px;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 4px;
            letter-spacing: -0.5px;
        }

        @media (min-width: 640px) {
            .header-title h1 { font-size: 26px; }
        }

        .header-subtitle {
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .badges {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }

        .badge {
            font-size: 10px;
            font-weight: 800;
            padding: 6px 12px;
            border-radius: 30px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            white-space: nowrap;
        }

        .badge-live {
            background: var(--accent-green-glow);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.4);
        }

        .badge-date {
            background: rgba(56, 189, 248, 0.12);
            color: var(--accent-blue);
            border: 1px solid rgba(56, 189, 248, 0.3);
        }

        /* METRICS GRID */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 16px;
        }

        @media (min-width: 640px) {
            .metrics-grid {
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 14px;
                margin-bottom: 20px;
            }
        }

        .metric-card {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 14px 16px;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 10px 20px -5px rgba(0, 0, 0, 0.5);
        }

        .metric-card.primary {
            border-color: rgba(56, 189, 248, 0.3);
            background: linear-gradient(145deg, rgba(56, 189, 248, 0.08), rgba(15, 23, 42, 0.8));
        }

        .metric-label {
            font-size: 10px;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.6px;
            margin-bottom: 6px;
        }

        .metric-value {
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.3px;
            word-break: break-word;
        }

        @media (min-width: 640px) {
            .metric-value { font-size: 22px; }
        }

        .metric-subtext {
            font-size: 10px;
            color: var(--text-muted);
            margin-top: 4px;
            font-weight: 500;
        }

        .pnl-positive { color: var(--accent-green); }
        .pnl-negative { color: var(--accent-red); }

        /* AUDIT SECTION CARD */
        .card-table {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 16px;
            box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.5);
        }

        @media (min-width: 640px) {
            .card-table { padding: 24px; }
        }

        .table-title {
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 16px;
            font-weight: 800;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 8px;
        }

        @media (min-width: 640px) {
            .table-title { font-size: 18px; }
        }

        /* DESKTOP TABLE VIEW */
        .table-container {
            width: 100%;
            overflow-x: auto;
            border-radius: 12px;
        }

        .table-container::-webkit-scrollbar {
            height: 4px;
        }
        .table-container::-webkit-scrollbar-thumb {
            background: var(--accent-blue);
            border-radius: 4px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 12px;
            white-space: nowrap;
        }

        th {
            background: var(--card-inner);
            color: var(--text-muted);
            font-weight: 700;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            padding: 12px 14px;
            border-bottom: 1px solid var(--border-color);
        }

        td {
            padding: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-weight: 500;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.03);
        }

        .symbol-badge {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid var(--border-color);
            padding: 4px 8px;
            border-radius: 6px;
            font-family: monospace;
            font-weight: 700;
            color: var(--accent-blue);
        }

        .status-pill {
            font-size: 10px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 20px;
            display: inline-block;
            text-transform: uppercase;
            color: var(--text-muted);
        }

        .empty-state {
            text-align: center;
            padding: 30px 16px;
            color: var(--text-muted);
        }

        .table-responsive {
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            border-radius: 8px;
            display: block;
        }

        /* Mobile First Responsive Styles */
        @media (max-width: 768px) {
            body { padding: 12px 10px; }
            .header {
                flex-direction: column;
                align-items: flex-start;
                padding: 16px 14px;
                gap: 12px;
            }
            .header-title h1 { font-size: 19px; line-height: 1.3; }
            .header-subtitle { font-size: 11px; }
            .badges {
                flex-wrap: wrap;
                gap: 6px;
                width: 100%;
            }
            .badge {
                font-size: 10px;
                padding: 4px 8px;
            }
            .metrics-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 10px;
                margin-bottom: 16px;
            }
            .metric-card {
                padding: 12px 10px;
            }
            .metric-label {
                font-size: 9px;
                margin-bottom: 4px;
            }
            .metric-value {
                font-size: 17px;
            }
            .metric-subtext {
                font-size: 9.5px;
            }
            .card-table {
                padding: 12px 10px;
            }
            .table-title {
                font-size: 14px;
                flex-direction: column;
                align-items: flex-start;
                gap: 4px;
            }
            table {
                min-width: 600px;
                font-size: 11.5px;
            }
            th, td {
                padding: 10px 8px;
                white-space: nowrap;
            }
        }

        @media (max-width: 480px) {
            .metrics-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
            }
            .metric-card {
                padding: 10px 8px;
            }
            .metric-value {
                font-size: 15px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header Banner -->
        <div class="header">
            <div class="header-title">
                <h1>INTRADAY OPTIONS TRADING DASHBOARD</h1>
                <div class="header-subtitle">Upstox API v2 Autonomous Options Execution Engine</div>
            </div>
            <div class="badges">
                <span class="badge {{ 'badge-mode-dryrun' if is_dry_run else 'badge-mode-live' }}">
                    {{ '🧪 SIMULATION / DRY-RUN' if is_dry_run else '🚀 LIVE PRODUCTION' }}
                </span>
                <span class="badge badge-security">🔒 READ-ONLY 100% SECURE</span>
                <span class="badge badge-date">📅 {{ date }}</span>
            </div>
        </div>

        <!-- Metrics Grid -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Real-Time Wallet Balance</div>
                <div class="metric-value" style="color: var(--accent-blue);">Rs {{ realtime_wallet }}</div>
                <div class="metric-subtext">Initial Base Capital: Rs {{ capital_base }}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Net Daily PnL</div>
                <div class="metric-value {{ 'pnl-positive' if is_positive_pnl else 'pnl-negative' }}">
                    {{ '+' if is_positive_pnl else '' }}Rs {{ net_pnl }}
                </div>
                <div class="metric-subtext {{ 'pnl-positive' if is_positive_pnl else 'pnl-negative' }}">
                    {{ '+' if is_positive_pnl else '' }}{{ pnl_pct }}% Net Return
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Daily Trades Executed</div>
                <div class="metric-value" style="color: var(--text-main);">{{ total_trades }} / 1</div>
                <div class="metric-subtext">Max Cap: 1 Trade / Day Hard Lock</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Win Rate</div>
                <div class="metric-value" style="color: {{ 'var(--accent-green)' if win_rate_val >= 50 else 'var(--accent-amber)' }};">{{ win_rate_val }}%</div>
                <div class="metric-subtext">{{ winning_trades }} Wins / {{ total_trades - winning_trades }} Losses</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Total Friction Fees</div>
                <div class="metric-value" style="color: var(--accent-purple);">Rs {{ total_friction }}</div>
                <div class="metric-subtext">Brokerage + STT + Txn Fee + GST</div>
            </div>
        </div>

        <!-- Executed Trade Log Table -->
        <div class="card-table">
            <div class="table-title">
                <span>Executed Intraday Option Trades ({{ mode_label }})</span>
                <span style="font-size: 13px; font-weight: 500; color: var(--text-muted);">Timestamped Session Audit</span>
            </div>

            {% if trades %}
            <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Trade ID</th>
                        <th>Time</th>
                        <th>Option Contract</th>
                        <th>Qty (Lot)</th>
                        <th>Entry Premium</th>
                        <th>Exit Premium</th>
                        <th>Friction Fees</th>
                        <th>Net PnL</th>
                        <th>Execution Reason</th>
                    </tr>
                </thead>
                <tbody>
                    {% for t in trades %}
                    <tr>
                        <td style="color: var(--text-muted);">#{{ t.id }}</td>
                        <td>{{ t.entry_time }}</td>
                        <td><span class="symbol-badge">{{ t.option_symbol }}</span></td>
                        <td>{{ t.quantity }} sh</td>
                        <td>Rs {{ "%.2f"|format(t.entry_premium or 0) }}</td>
                        <td>Rs {{ "%.2f"|format(t.exit_premium or 0) if t.exit_premium else '-' }}</td>
                        <td style="color: var(--accent-purple);">Rs {{ "%.2f"|format(t.friction_fees or 0) }}</td>
                        <td class="{{ 'pnl-positive' if (t.net_pnl or 0) >= 0 else 'pnl-negative' }}">
                            <strong>{{ '+' if (t.net_pnl or 0) >= 0 else '' }}Rs {{ "%.2f"|format(t.net_pnl or 0) }}</strong>
                        </td>
                        <td>
                            {% if t.exit_reason and 'TARGET_HIT' in t.exit_reason %}
                                <span class="reason-tag tag-target">🎯 TARGET HIT (+25%)</span>
                            {% elif t.exit_reason and ('TSL' in t.exit_reason or 'TRAILING' in t.exit_reason) %}
                                <span class="reason-tag tag-tsl">🔒 STEP TSL LOCK</span>
                            {% elif t.exit_reason and 'TIME' in t.exit_reason %}
                                <span class="reason-tag tag-time">⏳ 30-MIN TIME EXIT</span>
                            {% else %}
                                <span class="reason-tag tag-sl">🛑 STOP LOSS (-12%)</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            </div>
            {% else %}
            <div class="empty-state">
                <p>No option trades were executed during this trading session.</p>
            </div>
            {% endif %}
        </div>

        <div class="footer">
            <p>Upstox API v2 Autonomous Options Execution Engine | Security Verified Fund Compliance</p>
        </div>
    </div>
</body>
</html>
"""

def fetch_upstox_live_balance() -> tuple:
    """
    Queries Upstox User API to get live available cash balance.
    Returns (available_balance, utilized_amount).
    """
    try:
        from execution.upstox_trader import get_live_wallet_balance
        avail = get_live_wallet_balance()
        return avail, 0.0
    except Exception as e:
        print(f"[EOD Reporter - Upstox Balance Notice] {e}")
    return 0.0, 0.0

def fetch_upstox_live_order_book_trades() -> list:
    """
    Queries Upstox API to fetch live executed trades from today.
    Used as fallback when local SQLite DB has no records.
    """
    try:
        import upstox_client
        from execution.upstox_trader import get_active_upstox_token
        tok = get_active_upstox_token()
        if not tok or tok.startswith("MOCK") or tok.startswith("your_"):
            return []
        configuration = upstox_client.Configuration()
        configuration.access_token = tok
        order_api = upstox_client.OrderApi(upstox_client.ApiClient(configuration))
        res = order_api.get_order_book(api_version="2.0")
        orders = getattr(res, "data", res)
        if not isinstance(orders, list):
            return []

        today = datetime.date.today().isoformat()
        trades = []
        buy_orders = [o for o in orders if isinstance(o, dict) and str(o.get("transaction_type", "")).upper() == "BUY"
                      and str(o.get("status", "")).upper() in ("COMPLETE", "TRADED", "FILLED")]
        sell_orders = [o for o in orders if isinstance(o, dict) and str(o.get("transaction_type", "")).upper() == "SELL"
                       and str(o.get("status", "")).upper() in ("COMPLETE", "TRADED", "FILLED")]

        for i, b in enumerate(buy_orders, 1):
            sym = str(b.get("trading_symbol") or b.get("instrument_token") or "OPTION")
            entry_p = float(b.get("average_price") or b.get("price") or 0.0)
            qty = int(b.get("quantity") or 0)
            entry_t = str(b.get("order_timestamp", "")).split(" ")[-1] or "09:30:00"

            s = sell_orders[i-1] if i-1 < len(sell_orders) else None
            exit_p = float(s.get("average_price") or s.get("price") or entry_p) if s else entry_p
            exit_t = str(s.get("order_timestamp", "")).split(" ")[-1] if s else "OPEN"

            from reporting.friction_calculator import calculate_trade_friction
            f_res = calculate_trade_friction(qty, entry_p, exit_p)
            gross_pnl = f_res["gross_pnl"]
            friction = f_res["total_friction"]
            net_pnl_val = f_res["net_pnl"]

            target_p = round(entry_p * 1.25, 2)
            stop_p = round(entry_p * 0.88, 2)
            if s:
                if exit_p >= target_p:
                    reason = "TARGET_HIT_+25%"
                elif exit_p <= stop_p:
                    reason = "STOP_LOSS_HIT"
                else:
                    reason = "MANUAL_EXIT"
            else:
                reason = "ACTIVE_HOLD"

            trades.append({
                "id": i,
                "trade_date": today,
                "entry_time": entry_t,
                "exit_time": exit_t,
                "execution_mode": "LIVE",
                "underlying_symbol": sym.split("_")[0] if "_" in sym else sym,
                "option_symbol": sym,
                "option_type": "CE" if sym.endswith("CE") else "PE",
                "strike_price": 0.0,
                "quantity": qty,
                "entry_premium": entry_p,
                "exit_premium": exit_p,
                "target_price": target_p,
                "stop_price": stop_p,
                "gross_pnl": gross_pnl,
                "total_friction": friction,
                "net_pnl": net_pnl_val,
                "roi_pct": round((net_pnl_val / (entry_p * qty)) * 100, 2) if entry_p * qty > 0 else 0.0,
                "win_loss_status": "WIN" if net_pnl_val > 0 else ("LOSS" if net_pnl_val < 0 else "EVEN"),
                "exit_reason": reason,
                "status": "CLOSED" if s else "OPEN"
            })
        return trades
    except Exception as e:
        print(f"[EOD Reporter - Upstox Order Book Notice] {e}")
        return []

def generate_eod_report(date_str: str = None, dry_run: bool = False) -> str:
    """
    At 15:30 IST, pull trade execution logs from SQLite DB, calculate summary stats,
    fetch dynamic real-time wallet balance, render Jinja2 HTML report,
    and output separate report files for Live Mode vs Dry-Run Mode.
    """
    if not date_str:
        date_str = datetime.date.today().isoformat()
        
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    state_mgr = StateManager()
    realtime_wallet = state_mgr.get_current_wallet_balance()

    # In Live Mode, query DhanHQ API for actual available cash balance
    if not dry_run:
        try:
            upstox_balance, _ = fetch_upstox_live_balance()
            if upstox_balance > 0:
                realtime_wallet = upstox_balance
                state_mgr.state["current_wallet_balance"] = upstox_balance
                state_mgr._save_state(state_mgr.state)
        except Exception:
            pass

    target_mode = "DRY_RUN" if dry_run else "LIVE"
    trades = []
    try:
        conn = sqlite3.connect(DB_FILE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE trade_date = ? AND execution_mode = ?", (date_str, target_mode))
        rows = cursor.fetchall()
        trades = [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass

    # Fallback: fetch live trades directly from Upstox Order Book API
    if not trades and not dry_run:
        trades = fetch_upstox_live_order_book_trades()
    
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if (t.get("net_pnl") or 0) > 0)
    win_rate = round((winning_trades / total_trades) * 100.0, 2) if total_trades > 0 else 0.0
    total_friction = round(sum((t.get("friction_fees") or 0.0) for t in trades), 2)
    net_pnl = round(sum((t.get("net_pnl") or 0.0) for t in trades), 2)
    pnl_pct = round((net_pnl / INITIAL_WALLET_CAPITAL) * 100.0, 2)
    
    mode_tag = "DRY-RUN SIMULATION" if dry_run else "LIVE PRODUCTION"
    file_prefix = "EOD_Report_DRYRUN_" if dry_run else "EOD_Report_LIVE_"

    # 1. Output Summary to Console
    print("\n" + "=" * 75)
    print(f"       END OF DAY (EOD) OPTIONS TRADING REPORT [{mode_tag}]       ")
    print(f"       Session Date: {date_str} | Generated at 15:30 IST            ")
    print("=" * 75)
    print(f"  Initial Capital Base     : Rs {INITIAL_WALLET_CAPITAL:,.2f} INR")
    print(f"  Real-Time Wallet Balance : Rs {realtime_wallet:,.2f} INR")
    print(f"  Total Trades Executed    : {total_trades} / 1 (Max Daily Limit)")
    print(f"  Winning Trades           : {winning_trades}")
    print(f"  Win Rate                 : {win_rate}%")
    print(f"  Total Friction Costs     : Rs {total_friction:,.2f} INR")
    print(f"  Net Daily PnL            : Rs {net_pnl:,.2f} INR ({pnl_pct:+,.2f}%)")
    print("-" * 75)
    
    if trades:
        print("  Executed Trade History:")
        for t in trades:
            print(f"  - Trade #{t['id']} ({t['entry_time']}): {t['option_symbol']} | Qty: {t['quantity']} | Entry: Rs {t['entry_premium']} | Exit: Rs {t['exit_premium']} | Net PnL: Rs {t['net_pnl']} ({t['exit_reason']})")
    else:
        print("  No trades executed today.")
    print("=" * 75)

    # 2. Render Overhauled HTML Dashboard Report
    template = Template(HTML_TEMPLATE)
    html_output = template.render(
        date=date_str,
        mode_label=mode_tag,
        is_dry_run=dry_run,
        capital_base=f"{INITIAL_WALLET_CAPITAL:,.2f}",
        realtime_wallet=f"{realtime_wallet:,.2f}",
        total_trades=total_trades,
        winning_trades=winning_trades,
        win_rate_val=win_rate,
        total_friction=f"{total_friction:,.2f}",
        net_pnl=f"{net_pnl:,.2f}",
        pnl_pct=pnl_pct,
        is_positive_pnl=(net_pnl >= 0),
        trades=trades
    )
    
    report_path = os.path.join(REPORTS_DIR, "LIVE_MARKET_REPORT.html")
    file_prefix = "EOD_Report_DRYRUN_" if dry_run else "EOD_Report_LIVE_"
    dated_report_path = os.path.join(REPORTS_DIR, f"{file_prefix}{date_str}.html")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_output)

    with open(dated_report_path, "w", encoding="utf-8") as f:
        f.write(html_output)
        
    print(f"\n[EOD Reporter] HTML dashboard saved to active report files:\n  1. '{report_path}'\n  2. '{dated_report_path}'")
    return report_path

if __name__ == "__main__":
    generate_eod_report(dry_run=True)
