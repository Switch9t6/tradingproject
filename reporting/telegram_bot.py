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

def send_telegram_message(message_text: str) -> bool:
    """
    Sends an HTML-formatted notification message to the configured Telegram Chat / Channel.
    Fails gracefully if Telegram credentials are missing or network is unreachable.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_BOT_TOKEN.startswith("your_"):
        print("[Telegram Bot Notice] Credentials (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) not set in .env. Alert skipped.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=4.0)
        if response.status_code == 200:
            print("📱 [Telegram Alert] Notification sent successfully.")
            return True
        else:
            print(f"[Telegram Warning] Failed to send alert ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"[Telegram Exception] Network error sending alert: {e}")
        return False

def send_trade_entry_alert(trade_data: Dict[str, Any], wallet_balance: float = 0.0) -> bool:
    """
    Formats and dispatches a trade entry alert to Telegram.
    """
    option_symbol = trade_data.get("option_symbol", "N/A")
    lot_size = trade_data.get("lot_size", 0)
    entry_p = trade_data.get("entry_premium", 0.0)
    target_p = trade_data.get("target_price", 0.0)
    stop_p = trade_data.get("initial_stop_loss", 0.0)
    score = trade_data.get("composite_score", 0.0)
    mode = trade_data.get("execution_mode", "LIVE")

    msg = f"""
🚀 <b>[INTRADAY OPTIONS BOT] TRADE ENTRY ({mode})</b> 🚀

<b>Option Contract:</b> <code>{option_symbol}</code>
<b>Quantity:</b> {lot_size} shares (Single Lot)
<b>Entry Premium:</b> Rs {entry_p:.2f} / share
<b>Total Lot Value:</b> Rs {entry_p * lot_size:,.2f} INR
<b>Target (+25%):</b> Rs {target_p:.2f}
<b>Initial SL (-12%):</b> Rs {stop_p:.2f}
<b>Composite Score:</b> {score:.1f} / 100 Pts
<b>Live Wallet Balance:</b> Rs {wallet_balance:,.2f} INR
<i>Session Date: {datetime.date.today().isoformat()}</i>
    """.strip()

    return send_telegram_message(msg)

def send_trade_exit_alert(trade_data: Dict[str, Any], wallet_balance: float = 0.0) -> bool:
    """
    Formats and dispatches a trade exit alert (Target Hit, TSL Hit, SL Hit) to Telegram.
    """
    option_symbol = trade_data.get("option_symbol", "N/A")
    exit_p = trade_data.get("exit_premium", 0.0)
    entry_p = trade_data.get("entry_premium", 0.0)
    net_pnl = trade_data.get("net_pnl", 0.0)
    exit_reason = trade_data.get("exit_reason", "EXIT")
    mode = trade_data.get("execution_mode", "LIVE")

    gain_pct = ((exit_p - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
    status_icon = "🎯" if net_pnl > 0 else "🛑"

    msg = f"""
{status_icon} <b>[INTRADAY OPTIONS BOT] TRADE EXIT ({mode})</b> {status_icon}

<b>Option Contract:</b> <code>{option_symbol}</code>
<b>Exit Premium:</b> Rs {exit_p:.2f} ({gain_pct:+.2f}%)
<b>Exit Reason:</b> {exit_reason}
<b>Net Realized PnL:</b> {'+Rs ' if net_pnl >= 0 else '-Rs '}{abs(net_pnl):,.2f} INR
<b>Updated Real-Time Wallet:</b> Rs {wallet_balance:,.2f} INR
<i>Session Date: {datetime.date.today().isoformat()}</i>
    """.strip()

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

    icon = "📈" if net_pnl >= 0 else "📉"

    msg = f"""
{icon} <b>[EOD PERFORMANCE REPORT] - {date_str}</b> {icon}

<b>Total Trades Executed:</b> {total_trades}
<b>Win Rate:</b> {win_rate:.1f}%
<b>Daily Net PnL:</b> {'+Rs ' if net_pnl >= 0 else '-Rs '}{abs(net_pnl):,.2f} INR
<b>Final Wallet Balance:</b> Rs {wallet:,.2f} INR
    """.strip()

    return send_telegram_message(msg)

if __name__ == "__main__":
    print("=" * 75)
    print("      TELEGRAM NOTIFICATION MODULE DIAGNOSTIC TEST       ")
    print("=" * 75)
    
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

    print("\n[Test 1] Trade Entry Alert...")
    send_trade_entry_alert(test_trade, wallet_balance=10000.00)
    
    print("\n[Test 2] Trade Exit Alert...")
    send_trade_exit_alert(test_trade, wallet_balance=12240.31)
