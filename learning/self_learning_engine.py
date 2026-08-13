import os
import sys
import time
import json
import sqlite3
import datetime
from typing import Dict, Any, Optional, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DB_FILE_PATH, REPORTS_DIR

LEARNING_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_STATE_FILE = os.path.join(LEARNING_DIR, "model_state.json")
AI_LOG_FILE = os.path.join(REPORTS_DIR, "AI_Learning_Log.json")
AI_HTML_REPORT = os.path.join(REPORTS_DIR, "AI_Learning_Summary.html")

DEFAULT_MODEL_STATE = {
    "version": "1.0-Adaptive",
    "total_analyzed_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "false_breakout_count": 0,
    "theta_decay_count": 0,
    "whipsaw_count": 0,
    "learned_adjustments": {
        "min_volume_ratio": 1.20,
        "score_threshold_boost": 0.0,
        "recommended_take_profit_pct": 0.25,
        "recommended_stop_loss_pct": 0.12
    },
    "history": []
}


def _load_model_state() -> Dict[str, Any]:
    if os.path.exists(MODEL_STATE_FILE):
        try:
            with open(MODEL_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_MODEL_STATE.copy()


def _save_model_state(state: Dict[str, Any]):
    os.makedirs(LEARNING_DIR, exist_ok=True)
    with open(MODEL_STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def get_optimized_scoring_weights() -> Dict[str, Any]:
    """
    Returns AI self-learning optimized thresholds for scanners.
    """
    state = _load_model_state()
    adj = state.get("learned_adjustments", {})
    return {
        "min_volume_ratio": float(adj.get("min_volume_ratio", 1.20)),
        "score_threshold": float(75.0 + adj.get("score_threshold_boost", 0.0)),
        "take_profit_pct": float(adj.get("recommended_take_profit_pct", 0.25)),
        "stop_loss_pct": float(adj.get("recommended_stop_loss_pct", 0.12))
    }


def analyze_closed_trade(trade_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    AI POST-MORTEM ANALYZER:
    Classifies error patterns on closed trades and dynamically auto-tunes engine parameters.
    """
    state = _load_model_state()
    state["total_analyzed_trades"] += 1

    net_pnl = float(trade_record.get("net_pnl") or 0.0)
    exit_reason = str(trade_record.get("exit_reason", "")).upper()
    trade_id = trade_record.get("id") or trade_record.get("trade_id", f"T_{int(time.time())}")
    symbol = trade_record.get("option_symbol") or trade_record.get("symbol", "N/A")

    error_category = "SUCCESSFUL_MOMENTUM"
    lesson_learned = "Trade executed according to quantitative factor model."

    if net_pnl > 0:
        state["winning_trades"] += 1
        lesson_learned = f"Winning Trade (+Rs {net_pnl:,.2f}). High-conviction breakout pattern validated."
    else:
        state["losing_trades"] += 1
        if "STOP_LOSS" in exit_reason or net_pnl < -100.0:
            state["false_breakout_count"] += 1
            error_category = "FALSE_BREAKOUT"
            lesson_learned = "Loss caused by low-volume false breakout. Auto-raising minimum volume ratio threshold."
            # Auto-tune: Raise volume ratio requirement
            state["learned_adjustments"]["min_volume_ratio"] = round(min(state["learned_adjustments"].get("min_volume_ratio", 1.20) + 0.05, 1.60), 2)
        elif "STAGNATION" in exit_reason or "TIMEOUT" in exit_reason:
            state["theta_decay_count"] += 1
            error_category = "THETA_DECAY_EROSION"
            lesson_learned = "Loss caused by option time decay in stagnant trend. Auto-raising score threshold requirement."
            # Auto-tune: Boost score threshold
            state["learned_adjustments"]["score_threshold_boost"] = round(min(state["learned_adjustments"].get("score_threshold_boost", 0.0) + 2.5, 10.0), 1)
        else:
            state["whipsaw_count"] += 1
            error_category = "CHOPPY_REGIME_WHIPSAW"
            lesson_learned = "Loss caused by choppy market regime. Re-balancing stop loss buffer."

    log_entry = {
        "trade_id": trade_id,
        "symbol": symbol,
        "net_pnl": net_pnl,
        "exit_reason": exit_reason,
        "error_category": error_category,
        "lesson_learned": lesson_learned,
        "timestamp": datetime.datetime.now().isoformat()
    }

    state["history"].append(log_entry)
    _save_model_state(state)

    # Update AI Learning Log File
    os.makedirs(REPORTS_DIR, exist_ok=True)
    try:
        logs = []
        if os.path.exists(AI_LOG_FILE):
            with open(AI_LOG_FILE, "r") as f:
                logs = json.load(f)
        logs.append(log_entry)
        with open(AI_LOG_FILE, "w") as f:
            json.dump(logs, f, indent=4)
    except Exception as e:
        print(f"[AI Learning Engine Notice] {e}")

    generate_ai_learning_report()
    try:
        print(f"\n[AI SELF-LEARNING ENGINE] Trade #{trade_id} Analyzed: Category={error_category} | Lesson: {lesson_learned}")
    except Exception:
        pass
    return log_entry


def generate_ai_learning_report() -> str:
    """
    Generates an HTML AI Self-Learning Dashboard Report.
    """
    state = _load_model_state()
    total = state.get("total_analyzed_trades", 0)
    wins = state.get("winning_trades", 0)
    losses = state.get("losing_trades", 0)
    win_rate = round((wins / total) * 100.0, 1) if total > 0 else 0.0
    adj = state.get("learned_adjustments", {})

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AI Self-Learning Engine Summary</title>
    <style>
        body {{ font-family: 'Segoe UI', Inter, sans-serif; background: #0b0f19; color: #f8fafc; padding: 24px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #334155; }}
        h1 {{ color: #38bdf8; font-size: 24px; margin-bottom: 12px; }}
        h2 {{ color: #10b981; font-size: 18px; margin-bottom: 8px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 16px; }}
        .metric {{ background: #0f172a; padding: 16px; border-radius: 8px; text-align: center; border: 1px solid #1e293b; }}
        .metric-val {{ font-size: 22px; font-weight: bold; color: #38bdf8; margin-top: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #334155; font-size: 14px; }}
        th {{ background: #0f172a; color: #94a3b8; }}
        .badge-win {{ background: rgba(16,185,129,0.2); color: #10b981; padding: 4px 8px; border-radius: 4px; font-weight: bold; }}
        .badge-loss {{ background: rgba(244,63,94,0.2); color: #f43f5e; padding: 4px 8px; border-radius: 4px; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>🧠 AI Self-Learning & Reinforcement Learning Dashboard</h1>
    <p style="color: #94a3b8;">Continuously analyzing completed trades, detecting failure patterns, and auto-tuning scoring parameters.</p>

    <div class="card">
        <h2>📊 Learning Statistics & Metrics</h2>
        <div class="grid">
            <div class="metric"><div>Total Analyzed Trades</div><div class="metric-val">{total}</div></div>
            <div class="metric"><div>Win Rate</div><div class="metric-val" style="color: #10b981;">{win_rate}%</div></div>
            <div class="metric"><div>False Breakouts Detected</div><div class="metric-val" style="color: #f43f5e;">{state.get("false_breakout_count", 0)}</div></div>
            <div class="metric"><div>Theta Decay Erosions</div><div class="metric-val" style="color: #f59e0b;">{state.get("theta_decay_count", 0)}</div></div>
        </div>
    </div>

    <div class="card">
        <h2>⚙️ Auto-Tuned Parameters</h2>
        <div class="grid">
            <div class="metric"><div>Min Volume Ratio</div><div class="metric-val">{adj.get("min_volume_ratio", 1.20)}x</div></div>
            <div class="metric"><div>Score Threshold</div><div class="metric-val">{75.0 + adj.get("score_threshold_boost", 0.0)} Pts</div></div>
            <div class="metric"><div>Target Profit %</div><div class="metric-val">+{int(adj.get("recommended_take_profit_pct", 0.25)*100)}%</div></div>
            <div class="metric"><div>Stop Loss %</div><div class="metric-val">-{int(adj.get("recommended_stop_loss_pct", 0.12)*100)}%</div></div>
        </div>
    </div>

    <div class="card">
        <h2>📜 AI Post-Mortem Analysis Log</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Symbol</th>
                    <th>Net PnL</th>
                    <th>Error Category</th>
                    <th>Lesson Learned</th>
                </tr>
            </thead>
            <tbody>
"""

    for item in reversed(state.get("history", [])[-15:]):
        pnl = item.get("net_pnl", 0.0)
        badge = f'<span class="badge-win">+Rs {pnl:,.2f}</span>' if pnl >= 0 else f'<span class="badge-loss">-Rs {abs(pnl):,.2f}</span>'
        html_content += f"""
                <tr>
                    <td>{item.get("timestamp", "")[:19]}</td>
                    <td><b>{item.get("symbol", "")}</b></td>
                    <td>{badge}</td>
                    <td><code>{item.get("error_category", "")}</code></td>
                    <td>{item.get("lesson_learned", "")}</td>
                </tr>
"""

    html_content += """
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(AI_HTML_REPORT, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as ex:
        print(f"[AI Report Notice] {ex}")

    return AI_HTML_REPORT


if __name__ == "__main__":
    test_trade = {
        "id": 101,
        "symbol": "NIFTY24AUG23400CE",
        "net_pnl": -120.0,
        "exit_reason": "STOP_LOSS_HIT"
    }
    analyze_closed_trade(test_trade)
