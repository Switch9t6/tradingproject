"""
Telegram Command Control Module (Non-Blocking Background Listener)
==================================================================
Runs a Telegram Bot polling loop continuously so that incoming
Telegram commands (/start, /status, /report, /reports, /trades, /stop, /resume)
and interactive inline keyboard buttons are handled instantly while the main
trading pipeline executes in parallel.

Market Hours Enforcement (09:15 AM to 03:15 PM IST):
- When market is CLOSED (outside Mon-Fri 09:15 AM - 03:15 PM IST):
  - /start, /help, /stop, /resume return: "market is closed try during 9:15 AM to 3:15 PM"
  - /status, /report, /reports, /trades remain ACTIVE to view live stats and reports.
- When market is OPEN (Mon-Fri 09:15 AM - 03:15 PM IST):
  - /start executes: python main.py --live --auto-approve in background thread.
"""

import os
import sys
import threading
import datetime
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BOT_DISABLED_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BOT_DISABLED.flag")

# Module-level bot instance
_bot = None
_listener_started = False


def _safe_print(text: str):
    """Print text safely on Windows cp1252 consoles."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def check_is_market_open() -> bool:
    """Validates whether current time is within official NSE trading window (Mon-Fri 09:15 AM - 03:15 PM IST)."""
    now = datetime.datetime.now()
    if now.weekday() >= 5: # 5 = Saturday, 6 = Sunday
        return False
    current_time = now.time()
    return datetime.time(9, 15) <= current_time <= datetime.time(15, 15)


def _build_action_keyboard(telebot_module):
    """Generates an interactive inline button keyboard for Telegram messages."""
    markup = telebot_module.types.InlineKeyboardMarkup(row_width=2)
    btn_status = telebot_module.types.InlineKeyboardButton("📊 Status", callback_data="cb_status")
    btn_report = telebot_module.types.InlineKeyboardButton("📄 EOD Report", callback_data="cb_report")
    btn_trades = telebot_module.types.InlineKeyboardButton("📜 Trade Log", callback_data="cb_trades")
    btn_stop = telebot_module.types.InlineKeyboardButton("🛑 Stop Engine", callback_data="cb_stop")
    btn_resume = telebot_module.types.InlineKeyboardButton("✅ Resume Engine", callback_data="cb_resume")
    markup.add(btn_status, btn_report)
    markup.add(btn_trades)
    markup.add(btn_stop, btn_resume)
    return markup


def _get_bot():
    """Lazily initialize the telebot instance."""
    global _bot
    if _bot is None and TELEGRAM_BOT_TOKEN and not TELEGRAM_BOT_TOKEN.startswith("your_"):
        try:
            import telebot
            _bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
            _register_handlers(_bot)
        except ImportError:
            _safe_print("[Telegram Control] pyTelegramBotAPI not installed. Run: pip install pyTelegramBotAPI")
            return None
    return _bot


def _register_handlers(bot):
    """Register all Telegram command handlers and inline button callbacks on the bot instance."""
    import telebot

    @bot.message_handler(commands=["start"])
    def cmd_start(message):
        if not check_is_market_open():
            bot.reply_to(message, "market is closed try during 9:15 AM to 3:15 PM", reply_markup=_build_action_keyboard(telebot))
            return

        bot.reply_to(
            message,
            "🚀 [EXECUTING ENGINE] Launching trading pipeline ('python main.py --live')...\n\n"
            "System scanning top-ranked sectors & quantitative multi-factor matrix.",
            reply_markup=_build_action_keyboard(telebot)
        )

        def _run_pipeline_job():
            try:
                cmd = [sys.executable, "main.py", "--live", "--auto-approve"]
                _safe_print(f"[Telegram Control] Executing command via /start: {' '.join(cmd)}")
                subprocess.run(cmd, check=True)
            except Exception as e:
                _safe_print(f"[Telegram Control Error] Execution failed: {e}")

        # Run pipeline in a background thread so Telegram listener stays non-blocking
        t = threading.Thread(target=_run_pipeline_job, daemon=True)
        t.start()

    @bot.message_handler(commands=["help"])
    def cmd_help(message):
        if not check_is_market_open():
            bot.reply_to(message, "market is closed try during 9:15 AM to 3:15 PM", reply_markup=_build_action_keyboard(telebot))
            return

        help_text = (
            "[UPSTOX LIVE ALGORITHMIC ENGINE]\n"
            "-------------------------------------------\n"
            "Available Commands:\n"
            "/start   - Launch live trading pipeline ('python main.py --live')\n"
            "/status  - Live Wallet Balance & Bot Health\n"
            "/report  - Download today's Live EOD HTML report\n"
            "/trades  - View today's executed trade log\n"
            "/stop    - Emergency Pause (Kill Switch)\n"
            "/resume  - Re-enable Trading Engine\n"
            "/help    - Show this help message\n"
            "-------------------------------------------\n"
            "Engine Version: v2.0 (100% Real Live Production)"
        )
        bot.reply_to(message, help_text, reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["stop"])
    def cmd_stop(message):
        if not check_is_market_open():
            bot.reply_to(message, "market is closed try during 9:15 AM to 3:15 PM", reply_markup=_build_action_keyboard(telebot))
            return

        try:
            with open(BOT_DISABLED_FLAG, "w") as f:
                f.write("DISABLED_BY_TELEGRAM")
            bot.reply_to(
                message,
                "[EMERGENCY STOP ACTIVATED]\n"
                "Trading engine PAUSED. No new trades will be placed.\n"
                "Send /resume to re-enable.",
                reply_markup=_build_action_keyboard(telebot)
            )
            _safe_print("[Telegram Control] EMERGENCY STOP activated via Telegram /stop command.")
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to activate kill switch: {e}")

    @bot.message_handler(commands=["resume"])
    def cmd_resume(message):
        if not check_is_market_open():
            bot.reply_to(message, "market is closed try during 9:15 AM to 3:15 PM", reply_markup=_build_action_keyboard(telebot))
            return

        try:
            if os.path.exists(BOT_DISABLED_FLAG):
                os.remove(BOT_DISABLED_FLAG)
            bot.reply_to(
                message,
                "[TRADING ENGINE RESUMED]\n"
                "Dynamic scanning re-enabled. New trades will execute normally.",
                reply_markup=_build_action_keyboard(telebot)
            )
            _safe_print("[Telegram Control] Trading engine RESUMED via Telegram /resume command.")
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to resume: {e}")

    @bot.message_handler(commands=["status"])
    def cmd_status(message):
        # /status works ANYTIME (both when market is open and closed)
        now = datetime.datetime.now()
        market_open = check_is_market_open()
        market_state = "OPEN (Trading Window Active)" if market_open else "CLOSED (Mon-Fri 09:15 AM - 03:15 PM IST)"
        is_paused = os.path.exists(BOT_DISABLED_FLAG)
        engine_state = "PAUSED (Kill Switch Active)" if is_paused else ("ONLINE & SCANNING" if market_open else "STANDBY (Market Closed)")

        # Attempt to fetch live wallet balance
        wallet_str = "N/A (Upstox Token Sync Required)"
        try:
            from execution.state_manager import StateManager
            sm = StateManager()
            wallet_val = sm.get_current_wallet_balance()
            wallet_str = f"Rs {wallet_val:,.2f} INR"
        except Exception:
            pass

        # Fetch today's trade count
        trade_count = "0"
        try:
            from execution.state_manager import StateManager
            sm = StateManager()
            trades = sm.get_todays_trades()
            trade_count = str(len(trades))
        except Exception:
            pass

        status_msg = (
            "[SYSTEM STATUS REPORT]\n"
            "========================================\n"
            f"Market Window       : {market_state}\n"
            f"Live Wallet Balance : {wallet_str}\n"
            f"Engine State        : {engine_state}\n"
            f"Live Trades Today   : {trade_count}\n"
            f"Timestamp           : {now.strftime('%Y-%m-%d %H:%M:%S')} IST\n"
            "========================================"
        )
        bot.reply_to(message, status_msg, reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["report", "reports"])
    def cmd_report(message):
        # /report and /reports work ANYTIME (both when market is open and closed)
        import glob
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        reports_dir = os.path.join(base_dir, "reports")

        today_str = datetime.date.today().strftime("%Y%m%d")
        candidates = [
            os.path.join(reports_dir, f"EOD_Report_LIVE_{today_str}.html"),
            os.path.join(reports_dir, f"LIVE_MARKET_REPORT_{today_str}.html"),
            os.path.join(reports_dir, "LIVE_MARKET_REPORT.html"),
        ]

        # Filter strictly for live reports (excluding dry-run reports)
        all_reports = [f for f in sorted(glob.glob(os.path.join(reports_dir, "*.html")), key=os.path.getmtime, reverse=True) if "DRYRUN" not in os.path.basename(f)]

        sent = False
        for report_path in candidates + all_reports:
            if os.path.exists(report_path):
                try:
                    with open(report_path, "rb") as doc:
                        bot.send_document(message.chat.id, doc, caption=f"[LIVE EOD REPORT] {os.path.basename(report_path)}", reply_markup=_build_action_keyboard(telebot))
                    sent = True
                    _safe_print(f"[Telegram Control] Live report sent: {os.path.basename(report_path)}")
                    break
                except Exception as e:
                    bot.reply_to(message, f"[ERROR] Failed to send report: {e}")
                    return

        if not sent:
            bot.reply_to(message, "[NO REPORTS] No live market EOD report files found yet for today.", reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["trades"])
    def cmd_trades(message):
        # /trades works ANYTIME (both when market is open and closed)
        try:
            from execution.state_manager import StateManager
            sm = StateManager()
            trades = sm.get_todays_trades()

            if not trades:
                bot.reply_to(message, "[TRADES] No live trades executed today.", reply_markup=_build_action_keyboard(telebot))
                return

            lines = [f"[TODAY'S LIVE TRADE LOG] ({len(trades)} trades)\n========================================"]
            total_pnl = 0.0
            for i, t in enumerate(trades, 1):
                symbol = t.get("option_contract", {}).get("option_symbol", "N/A")
                entry = t.get("entry_premium", 0.0)
                exit_p = t.get("exit_premium", 0.0)
                pnl = t.get("net_pnl", 0.0)
                reason = t.get("exit_reason", "OPEN")
                total_pnl += pnl
                lines.append(
                    f"\nTrade #{i}: {symbol}\n"
                    f"  Entry: Rs {entry:.2f} | Exit: Rs {exit_p:.2f}\n"
                    f"  PnL: {'+' if pnl>=0 else ''}Rs {pnl:,.2f} | {reason}"
                )

            lines.append(f"\n========================================")
            lines.append(f"NET DAILY PnL: {'+' if total_pnl>=0 else ''}Rs {total_pnl:,.2f} INR")

            bot.reply_to(message, "\n".join(lines), reply_markup=_build_action_keyboard(telebot))
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Could not fetch trades: {e}")

    # Callback Query Handler for Interactive Inline Buttons
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback_query(call):
        if call.data == "cb_status":
            cmd_status(call.message)
        elif call.data == "cb_report":
            cmd_report(call.message)
        elif call.data == "cb_trades":
            cmd_trades(call.message)
        elif call.data == "cb_stop":
            cmd_stop(call.message)
        elif call.data == "cb_resume":
            cmd_resume(call.message)
            
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


def is_bot_disabled() -> bool:
    """Check if the remote Telegram kill switch (/stop) is active."""
    return os.path.exists(BOT_DISABLED_FLAG)


def start_telegram_listener_background():
    """
    Starts the Telegram bot polling in a non-blocking background thread.
    Safe to call multiple times -- only the first call starts the listener.
    """
    global _listener_started

    if _listener_started:
        return

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("your_"):
        _safe_print("[Telegram Control] TELEGRAM_BOT_TOKEN not configured. Background listener skipped.")
        return

    bot = _get_bot()
    if bot is None:
        return

    def _polling_loop():
        _safe_print("[Telegram Control] Cloud polling listener started. Listening 24/7 for /start, /status, /report, /reports, /trades, /stop, /resume...")
        import time
        while True:
            try:
                bot.infinity_polling(timeout=30, long_polling_timeout=20)
            except Exception as e:
                _safe_print(f"[Telegram Control Warning] Polling glitch: {e}. Auto-reconnecting in 5s...")
                time.sleep(5)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    _listener_started = True
    _safe_print("[Telegram Control] Background listener thread launched successfully.")


if __name__ == "__main__":
    _safe_print("=" * 70)
    _safe_print("    TELEGRAM CONTROL MODULE - STANDALONE DIAGNOSTIC TEST")
    _safe_print("=" * 70)
    _safe_print(f"  BOT TOKEN : {'SET (' + TELEGRAM_BOT_TOKEN[:12] + '...)' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    _safe_print(f"  CHAT ID   : {'SET (' + TELEGRAM_CHAT_ID + ')' if TELEGRAM_CHAT_ID else 'NOT SET'}")
    _safe_print(f"  KILL FLAG : {'ACTIVE' if os.path.exists(BOT_DISABLED_FLAG) else 'CLEAR'}")
    _safe_print(f"  MARKET    : {'OPEN (09:15 AM - 03:15 PM IST)' if check_is_market_open() else 'CLOSED'}")
    _safe_print("")
    _safe_print("  Starting foreground polling (Ctrl+C to exit)...")
    _safe_print("=" * 70)

    bot = _get_bot()
    if bot:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    else:
        _safe_print("[ERROR] Could not initialize Telegram bot. Check credentials.")
