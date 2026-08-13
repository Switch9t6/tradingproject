import os
import sys
import json
import datetime
from collections import deque
from typing import Dict, Any, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DB_FILE_PATH, REPORTS_DIR

LEARNING_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_STATE_FILE = os.path.join(LEARNING_DIR, "model_state.json")
AI_LOG_FILE = os.path.join(REPORTS_DIR, "AI_Learning_Log.json")
AI_HTML_REPORT = os.path.join(REPORTS_DIR, "AI_Learning_Summary.html")

# =============================================================================
# ANTI-OVERFITTING ADAPTIVE CONTROLLER CONFIGURATION
# -----------------------------------------------------------------------------
# The old engine bumped knobs monotonically on every single loss and never
# relaxed, so thresholds drifted to their caps and permanently locked in stale
# lessons from an old market regime (classic overfitting). The controller below
# instead reacts only to SUSTAINED, statistically-significant error rates from a
# rolling window, and MEAN-REVERTS back toward baseline when the pattern stops.
# =============================================================================

# How many recent closed trades the adverse-rate estimates are computed over.
RATE_WINDOW = 50

# Minimum number of closed trades BEFORE any knob may move (significance guard).
# A single loss must never be able to move a threshold on its own.
MIN_LEARNING_SAMPLE = 15

# Hysteresis deadband: the knob only moves when |recent_rate - target| exceeds
# this. Prevents trade-to-trade flip-flopping around equilibrium.
DEADBAND = 0.08

# Long-run tolerable adverse rates (if the real rate sits below this, the
# knob is left alone or relaxed; above it, the knob is tightened).
ALLOWED_FALSE_BREAKOUT_RATE = 0.30
ALLOWED_THETA_DECAY_RATE = 0.30

# Controller gains: tighten faster than we relax so the system is conservative,
# but every knob CAN relax - no lesson is permanent.
TIGHTEN_GAIN_VOL = 0.05    # min_volume_ratio  units per unit-rate above target
RELAX_GAIN_VOL = 0.03      # min_volume_ratio  units per unit-rate below target
TIGHTEN_GAIN_SCORE = 0.5   # score boost points per unit-rate above target
RELAX_GAIN_SCORE = 0.25    # score boost points per unit-rate below target

# Absolute bounds (never leave these). min_volume_ratio baseline is 2.0 to match
# the current production Engine-A gate exactly, so the learned knob can only
# ever TIGHTEN relative to today's behaviour - it can never loosen the filter.
MIN_VOLUME_RATIO_BASE = 2.0
MIN_VOLUME_RATIO_CEIL = 3.0
SCORE_BOOST_FLOOR = 0.0
SCORE_BOOST_CEIL = 10.0    # adaptive threshold max = 75.0 + 10.0 = 85.0 pts

# Base composite-score threshold the boost is added on top of (Engine A / NSE
# factor-matrix gate). Engine B's QUALIFICATION_SCORE_THRESHOLD (80) is left
# untouched.
SCORE_THRESHOLD_BASE = 75.0

DEFAULT_MODEL_STATE = {
    "version": "2.0-AntiOverfit",
    "total_analyzed_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "false_breakout_count": 0,
    "theta_decay_count": 0,
    "whipsaw_count": 0,
    "learned_adjustments": {
        "min_volume_ratio": MIN_VOLUME_RATIO_BASE,
        "score_threshold_boost": SCORE_BOOST_FLOOR,
        "recommended_take_profit_pct": 0.25,
        "recommended_stop_loss_pct": 0.12
    },
    "recent_outcomes": [],
    "history": []
}


def _load_model_state() -> Dict[str, Any]:
    """Loads + normalizes the model state (migrates v1 -> v2, clamps knobs)."""
    state = json.loads(json.dumps(DEFAULT_MODEL_STATE))
    if os.path.exists(MODEL_STATE_FILE):
        try:
            with open(MODEL_STATE_FILE, "r") as f:
                saved = json.load(f)
            for k in ("total_analyzed_trades", "winning_trades", "losing_trades",
                      "false_breakout_count", "theta_decay_count", "whipsaw_count"):
                if k in saved:
                    state[k] = int(saved.get(k, 0))
            if isinstance(saved.get("learned_adjustments"), dict):
                adj = saved["learned_adjustments"]
                state["learned_adjustments"]["min_volume_ratio"] = float(
                    min(MIN_VOLUME_RATIO_CEIL, max(MIN_VOLUME_RATIO_BASE, float(adj.get("min_volume_ratio", MIN_VOLUME_RATIO_BASE)))))
                state["learned_adjustments"]["score_threshold_boost"] = float(
                    min(SCORE_BOOST_CEIL, max(SCORE_BOOST_FLOOR, float(adj.get("score_threshold_boost", SCORE_BOOST_FLOOR)))))
                state["learned_adjustments"]["recommended_take_profit_pct"] = float(adj.get("recommended_take_profit_pct", 0.25))
                state["learned_adjustments"]["recommended_stop_loss_pct"] = float(adj.get("recommended_stop_loss_pct", 0.12))
            if isinstance(saved.get("recent_outcomes"), list):
                state["recent_outcomes"] = saved["recent_outcomes"][-RATE_WINDOW:]
            elif isinstance(saved.get("history"), list):
                # v1 migration: rebuild the rolling window from the history log.
                state["recent_outcomes"] = [
                    h.get("error_category") for h in saved["history"]
                    if isinstance(h, dict) and h.get("error_category")
                ][-RATE_WINDOW:]
            if isinstance(saved.get("history"), list):
                state["history"] = saved["history"][-200:]
        except Exception:
            pass
    return state


def _save_model_state(state: Dict[str, Any]):
    os.makedirs(LEARNING_DIR, exist_ok=True)
    with open(MODEL_STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _recent_rate(state: Dict[str, Any], category: str) -> float:
    """Share of the last RATE_WINDOW trades that fell in `category` (EMA-free
    rolling estimate; a single burst only moves it by 1/window_size)."""
    window = state.get("recent_outcomes", [])[-RATE_WINDOW:]
    if not window:
        return 0.0
    return window.count(category) / float(len(window))


def _apply_adaptive_controller(state: Dict[str, Any]) -> None:
    """
    Anti-overfit parameter update. Only runs after MIN_LEARNING_SAMPLE closed
    trades and only moves a knob when the recent adverse rate clears the
    deadband. Every knob can move BOTH directions (tighten AND relax), so
    thresholds mean-revert to baseline once the adverse pattern disappears
    instead of drifting monotonically to their caps forever.
    """
    if state["total_analyzed_trades"] < MIN_LEARNING_SAMPLE:
        return

    adj = state["learned_adjustments"]
    fb_rate = _recent_rate(state, "FALSE_BREAKOUT")
    theta_rate = _recent_rate(state, "THETA_DECAY")

    # min_volume_ratio <-> false-breakout rate
    vol = adj["min_volume_ratio"]
    if fb_rate > ALLOWED_FALSE_BREAKOUT_RATE + DEADBAND:
        vol += TIGHTEN_GAIN_VOL * (fb_rate - ALLOWED_FALSE_BREAKOUT_RATE)
    elif fb_rate < ALLOWED_FALSE_BREAKOUT_RATE - DEADBAND:
        vol -= RELAX_GAIN_VOL * (ALLOWED_FALSE_BREAKOUT_RATE - fb_rate)
    adj["min_volume_ratio"] = round(_clamp(vol, MIN_VOLUME_RATIO_BASE, MIN_VOLUME_RATIO_CEIL), 2)

    # score_threshold_boost <-> theta-decay (stagnation/timeout) rate
    boost = adj["score_threshold_boost"]
    if theta_rate > ALLOWED_THETA_DECAY_RATE + DEADBAND:
        boost += TIGHTEN_GAIN_SCORE * (theta_rate - ALLOWED_THETA_DECAY_RATE)
    elif theta_rate < ALLOWED_THETA_DECAY_RATE - DEADBAND:
        boost -= RELAX_GAIN_SCORE * (ALLOWED_THETA_DECAY_RATE - theta_rate)
    adj["score_threshold_boost"] = round(_clamp(boost, SCORE_BOOST_FLOOR, SCORE_BOOST_CEIL), 2)


def get_optimized_scoring_weights() -> Dict[str, Any]:
    """
    Returns the anti-overfit adaptive thresholds for scanners:
      - min_volume_ratio : minimum volume-surge ratio (baseline 2.0, tightening-only)
      - score_threshold  : minimum composite score (75.0 base + learned boost, <= 85.0)
      - take_profit_pct / stop_loss_pct : currently static (not auto-tuned)
    """
    state = _load_model_state()
    adj = state.get("learned_adjustments", {})
    return {
        "min_volume_ratio": float(adj.get("min_volume_ratio", MIN_VOLUME_RATIO_BASE)),
        "score_threshold": float(SCORE_THRESHOLD_BASE + adj.get("score_threshold_boost", SCORE_BOOST_FLOOR)),
        "take_profit_pct": float(adj.get("recommended_take_profit_pct", 0.25)),
        "stop_loss_pct": float(adj.get("recommended_stop_loss_pct", 0.12))
    }


def analyze_closed_trade(trade_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    AI POST-MORTEM ANALYZER (anti-overfit):
    Classifies closed trades into failure patterns, feeds the result into the
    rolling window, and lets the adaptive controller tighten/relax thresholds
    only when the pattern is sustained and statistically significant.
    """
    state = _load_model_state()
    state["total_analyzed_trades"] += 1

    net_pnl = float(trade_record.get("net_pnl") or 0.0)
    exit_reason = str(trade_record.get("exit_reason", "")).upper()
    trade_id = trade_record.get("id") or trade_record.get("trade_id", f"T_{state['total_analyzed_trades']}")
    symbol = trade_record.get("option_symbol") or trade_record.get("symbol", "N/A")

    error_category = "WIN"
    lesson_learned = "Trade executed according to quantitative factor model."

    if net_pnl > 0:
        state["winning_trades"] += 1
        lesson_learned = f"Winning Trade (+Rs {net_pnl:,.2f}). High-conviction breakout pattern validated."
    else:
        state["losing_trades"] += 1
        if "STOP_LOSS" in exit_reason or net_pnl < -100.0:
            state["false_breakout_count"] += 1
            error_category = "FALSE_BREAKOUT"
            lesson_learned = "Loss caused by low-volume false breakout. Tightening min volume ratio if pattern persists."
        elif "STAGNATION" in exit_reason or "TIMEOUT" in exit_reason:
            state["theta_decay_count"] += 1
            error_category = "THETA_DECAY"
            lesson_learned = "Loss caused by option time decay in stagnant trend. Raising score threshold if pattern persists."
        else:
            state["whipsaw_count"] += 1
            error_category = "WHIPSAW"
            lesson_learned = "Loss caused by choppy market regime."

    # Feed the rolling window and run the anti-overfit controller.
    recent = list(state.get("recent_outcomes", []))
    recent.append(error_category)
    state["recent_outcomes"] = recent[-RATE_WINDOW:]
    _apply_adaptive_controller(state)

    log_entry = {
        "trade_id": trade_id,
        "symbol": symbol,
        "net_pnl": net_pnl,
        "exit_reason": exit_reason,
        "error_category": error_category,
        "lesson_learned": lesson_learned,
        "timestamp": datetime.datetime.now().isoformat()
    }

    history = list(state.get("history", []))
    history.append(log_entry)
    state["history"] = history[-200:]
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
        fb_rate = _recent_rate(state, "FALSE_BREAKOUT")
        theta_rate = _recent_rate(state, "THETA_DECAY")
        print(f"\n[AI SELF-LEARNING ENGINE] Trade #{trade_id} Analyzed: Category={error_category} | "
              f"Window FB-rate={fb_rate:.0%} | Theta-rate={theta_rate:.0%}")
    except Exception:
        pass
    return log_entry


def reset_learning_state() -> None:
    """Ops tool: wipe learned state back to factory defaults (anti-overfit reset)."""
    _save_model_state(json.loads(json.dumps(DEFAULT_MODEL_STATE)))
    try:
        if os.path.exists(AI_LOG_FILE):
            os.remove(AI_LOG_FILE)
    except Exception:
        pass
    print("[AI Learning Engine] Learning state reset to factory defaults.")


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
    window = state.get("recent_outcomes", [])[-RATE_WINDOW:]
    fb_rate = round(_recent_rate(state, "FALSE_BREAKOUT") * 100.0, 1)
    theta_rate = round(_recent_rate(state, "THETA_DECAY") * 100.0, 1)
    sample_ready = total >= MIN_LEARNING_SAMPLE

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
    <h1>🧠 AI Self-Learning &amp; Reinforcement Learning Dashboard</h1>
    <p style="color: #94a3b8;">Anti-overfit adaptive controller: thresholds only move on sustained, significant error patterns and mean-revert to baseline when they fade.</p>

    <div class="card">
        <h2>📊 Learning Statistics &amp; Metrics</h2>
        <div class="grid">
            <div class="metric"><div>Total Analyzed Trades</div><div class="metric-val">{total}</div></div>
            <div class="metric"><div>Win Rate</div><div class="metric-val" style="color: #10b981;">{win_rate}%</div></div>
            <div class="metric"><div>Rolling Window Size</div><div class="metric-val">{len(window)} / {RATE_WINDOW}</div></div>
            <div class="metric"><div>Controller Armed</div><div class="metric-val" style="color: {'#10b981' if sample_ready else '#f59e0b'};">{'YES' if sample_ready else f'needs {MIN_LEARNING_SAMPLE - total} more trades'}</div></div>
        </div>
    </div>

    <div class="card">
        <h2>⚙️ Adaptive Parameters (Anti-Overfit)</h2>
        <div class="grid">
            <div class="metric"><div>Min Volume Ratio (baseline 2.0x)</div><div class="metric-val">{adj.get("min_volume_ratio", MIN_VOLUME_RATIO_BASE)}x</div></div>
            <div class="metric"><div>Score Threshold</div><div class="metric-val">{SCORE_THRESHOLD_BASE + adj.get("score_threshold_boost", 0.0)} Pts</div></div>
            <div class="metric"><div>Rolling False-Breakout Rate</div><div class="metric-val" style="color: #f43f5e;">{fb_rate}%</div></div>
            <div class="metric"><div>Rolling Theta-Decay Rate</div><div class="metric-val" style="color: #f59e0b;">{theta_rate}%</div></div>
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
