import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import requests
import datetime
from typing import Dict, Any, Optional

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def _safe_print(text: str):
    """Print text safely on Windows cp1252 consoles by replacing unmappable chars."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def send_telegram_message(message_text: str) -> bool:
    """
    Sends an HTML-formatted notification message to the configured Telegram Chat / Channel.
    Fails gracefully if Telegram credentials are missing or network is unreachable.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_BOT_TOKEN.startswith("your_"):
        _safe_print("[Telegram Bot Notice] Credentials (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) not set in .env. Alert skipped.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=10.0)
        if response.status_code == 200:
            _safe_print("[Telegram Alert] Notification sent successfully.")
            return True
        else:
            _safe_print(f"[Telegram Warning] Failed to send alert (HTTP {response.status_code}): {response.text}")
            return False
    except Exception as e:
        _safe_print(f"[Telegram Exception] Network error sending alert: {e}")
        return False


def send_trade_entry_alert(trade_data: Dict[str, Any], wallet_balance: float = 0.0) -> bool:
    """
    Formats and dispatches a detailed executed trade entry notification to Telegram.
    """
    option_symbol = trade_data.get("option_symbol", "N/A")
    lot_size = trade_data.get("lot_size", 0)
    entry_p = trade_data.get("entry_premium", 0.0)
    target_p = trade_data.get("target_price", 0.0)
    stop_p = trade_data.get("initial_stop_loss", 0.0)
    score = trade_data.get("composite_score", 80.0)
    mode = trade_data.get("execution_mode", "LIVE PRODUCTION")

    total_value = entry_p * lot_size

    msg = (
        f"⚡ <b>[EXECUTED TRADE NOTIFICATION - {mode}]</b>\n"
        f"========================================\n"
        f"<b>Contract Symbol :</b> <code>{option_symbol}</code>\n"
        f"<b>Execution Mode  :</b> REAL LIVE UPSTOX ORDER\n"
        f"<b>Order Quantity  :</b> {lot_size} shares (1 Lot)\n"
        f"<b>Fill Premium    :</b> Rs {entry_p:.2f} / share\n"
        f"<b>Total Cost      :</b> Rs {total_value:,.2f} INR\n"
        f"----------------------------------------\n"
        f"<b>Target (+25%)   :</b> Rs {target_p:.2f}\n"
        f"<b>Initial SL (-12%):</b> Rs {stop_p:.2f}\n"
        f"<b>Step TSL Guard  :</b> Breakeven @ +10% | Lock +10% @ +18%\n"
        f"<b>Composite Score :</b> {score:.1f} / 100 Pts\n"
        f"<b>Available Cash  :</b> Rs {wallet_balance:,.2f} INR\n"
        f"========================================\n"
        f"<i>WebSocket Tick Position Monitor Active</i>"
    )

    return send_telegram_message(msg)


def send_trade_exit_alert(trade_data: Dict[str, Any], wallet_balance: float = 0.0) -> bool:
    """
    Formats and dispatches a detailed executed trade exit notification (Target Hit, TSL Hit, SL Hit) to Telegram.
    """
    option_symbol = trade_data.get("option_symbol", "N/A")
    exit_p = trade_data.get("exit_premium", 0.0)
    entry_p = trade_data.get("entry_premium", 0.0)
    net_pnl = trade_data.get("net_pnl", 0.0)
    exit_reason = trade_data.get("exit_reason", "EXIT")
    mode = trade_data.get("execution_mode", "LIVE PRODUCTION")

    gain_pct = ((exit_p - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
    pnl_sign = "+" if net_pnl >= 0 else ""
    header_tag = "🎯 [PROFIT TARGET HIT]" if "TARGET" in exit_reason else ("🔒 [STEP TSL HIT]" if "TSL" in exit_reason else ("⏳ [30-MIN TIME EXIT]" if "TIME" in exit_reason else "🛑 [STOP LOSS HIT]"))

    msg = (
        f"{header_tag}\n"
        f"========================================\n"
        f"<b>Contract Symbol :</b> <code>{option_symbol}</code>\n"
        f"<b>Execution Mode  :</b> {mode}\n"
        f"<b>Entry Premium   :</b> Rs {entry_p:.2f}\n"
        f"<b>Exit Premium    :</b> Rs {exit_p:.2f} ({gain_pct:+.2f}%)\n"
        f"<b>Exit Reason     :</b> {exit_reason}\n"
        f"----------------------------------------\n"
        f"<b>Net Realized PnL:</b> <b>{pnl_sign}Rs {net_pnl:,.2f} INR</b>\n"
        f"<b>Updated Balance :</b> Rs {wallet_balance:,.2f} INR\n"
        f"========================================"
    )

    return send_telegram_message(msg)


def send_daily_summary_alert(stats: Dict[str, Any]) -> bool:
    """
    Sends End-of-Day (EOD) PnL performance summary alert to Telegram.
    """
    date_str = stats.get("date", datetime.date.today().isoformat())
    total_trades = stats.get("total_trades", 0)
    win_rate = stats.get("win_rate", 0.0)
    net_pnl = stats.get("net_pnl", 0.0)
    wallet = stats.get("wallet_balance", 0.0)

    pnl_label = "+Rs " + f"{abs(net_pnl):,.2f}" if net_pnl >= 0 else "-Rs " + f"{abs(net_pnl):,.2f}"

    msg = (
        "<b>[EOD PERFORMANCE REPORT] - " + date_str + "</b>\n"
        "\n"
        "<b>Total Trades Executed:</b> " + str(total_trades) + "\n"
        "<b>Win Rate:</b> " + f"{win_rate:.1f}" + "%\n"
        "<b>Daily Net PnL:</b> " + pnl_label + " INR\n"
        "<b>Final Wallet Balance:</b> Rs " + f"{wallet:,.2f}" + " INR"
    )

    return send_telegram_message(msg)


if __name__ == "__main__":
    _safe_print("=" * 75)
    _safe_print("      TELEGRAM NOTIFICATION MODULE DIAGNOSTIC TEST       ")
    _safe_print("=" * 75)

    _safe_print(f"\n[Config] TELEGRAM_BOT_TOKEN: {'SET (' + TELEGRAM_BOT_TOKEN[:12] + '...)' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    _safe_print(f"[Config] TELEGRAM_CHAT_ID:  {'SET (' + TELEGRAM_CHAT_ID + ')' if TELEGRAM_CHAT_ID else 'NOT SET'}")

    test_trade = {
        "option_symbol": "RELIANCE_2960_CE",
        "lot_size": 250,
        "entry_premium": 37.03,
        "exit_premium": 46.29,
        "target_price": 46.29,
        "initial_stop_loss": 32.59,
        "composite_score": 84.37,
        "net_pnl": 2240.31,
        "exit_reason": "TARGET_HIT_+25%",
        "execution_mode": "DRY_RUN"
    }

    _safe_print("\n[Test 1] Sending Trade Entry Alert...")
    result1 = send_trade_entry_alert(test_trade, wallet_balance=10000.00)
    _safe_print(f"[Test 1] Result: {'SUCCESS' if result1 else 'FAILED'}")

    _safe_print("\n[Test 2] Sending Trade Exit Alert...")
    result2 = send_trade_exit_alert(test_trade, wallet_balance=12240.31)
    _safe_print(f"[Test 2] Result: {'SUCCESS' if result2 else 'FAILED'}")

    _safe_print("\n[Test 3] Sending Daily Summary Alert...")
    result3 = send_daily_summary_alert({
        "date": datetime.date.today().isoformat(),
        "total_trades": 3,
        "win_rate": 66.7,
        "net_pnl": 4097.70,
        "wallet_balance": 14097.70
    })
    _safe_print(f"[Test 3] Result: {'SUCCESS' if result3 else 'FAILED'}")

    _safe_print("\n" + "=" * 75)
    _safe_print(f"  DIAGNOSTIC COMPLETE: {sum([result1, result2, result3])}/3 alerts sent successfully.")
    _safe_print("=" * 75)
