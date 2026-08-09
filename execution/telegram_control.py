"""
Telegram Command Control Module (Non-Blocking Background Listener)
==================================================================
Runs a Telegram Bot polling loop in a daemon background thread so that
incoming /start, /status, /stop, /resume commands are handled instantly
while the main trading pipeline executes in the foreground.

Commands:
  /start, /help  - Show available commands
  /status        - Live wallet balance, engine state, active positions
  /stop          - Emergency kill switch (creates BOT_DISABLED.flag)
  /resume        - Re-enable trading (removes BOT_DISABLED.flag)
"""

import os
import sys
import threading
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BOT_DISABLED_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BOT_DISABLED.flag")

# Module-level bot instance (initialized only if token is present)
_bot = None
_listener_started = False


def _safe_print(text: str):
    """Print text safely on Windows cp1252 consoles."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


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
    """Register all Telegram command handlers on the bot instance."""

    @bot.message_handler(commands=["start", "help"])
    def cmd_start(message):
        help_text = (
            "[UPSTOX ALGORITHMIC ENGINE]\n"
            "-------------------------------------------\n"
            "Available Commands:\n"
            "/status  - Live Wallet Balance & Bot Health\n"
            "/stop    - Emergency Pause (Kill Switch)\n"
            "/resume  - Re-enable Trading Engine\n"
            "/help    - Show this help message\n"
            "-------------------------------------------\n"
            "Engine Version: v2.0 (Production)"
        )
        bot.reply_to(message, help_text)

    @bot.message_handler(commands=["status"])
    def cmd_status(message):
        now = datetime.datetime.now()
        is_paused = os.path.exists(BOT_DISABLED_FLAG)
        engine_state = "PAUSED (Kill Switch Active)" if is_paused else "ONLINE & SCANNING"

        # Attempt to fetch live wallet balance
        wallet_str = "N/A (Token Refresh Required)"
        try:
            from execution.state_manager import StateManager
            sm = StateManager()
            wallet_val = sm.get_current_wallet_balance()
            wallet_str = f"Rs {wallet_val:,.2f} INR"
        except Exception:
            pass

        # Fetch today's trade count
        trade_count = "N/A"
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
            f"Live Wallet Balance : {wallet_str}\n"
            f"Engine State        : {engine_state}\n"
            f"Trades Today        : {trade_count}\n"
            f"Timestamp           : {now.strftime('%Y-%m-%d %H:%M:%S')} IST\n"
            "========================================"
        )
        bot.reply_to(message, status_msg)

    @bot.message_handler(commands=["stop"])
    def cmd_stop(message):
        try:
            with open(BOT_DISABLED_FLAG, "w") as f:
                f.write("DISABLED_BY_TELEGRAM")
            bot.reply_to(
                message,
                "[EMERGENCY STOP ACTIVATED]\n"
                "Trading engine PAUSED. No new trades will be placed.\n"
                "Send /resume to re-enable."
            )
            _safe_print("[Telegram Control] EMERGENCY STOP activated via Telegram /stop command.")
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to activate kill switch: {e}")

    @bot.message_handler(commands=["resume"])
    def cmd_resume(message):
        try:
            if os.path.exists(BOT_DISABLED_FLAG):
                os.remove(BOT_DISABLED_FLAG)
            bot.reply_to(
                message,
                "[TRADING ENGINE RESUMED]\n"
                "Dynamic scanning re-enabled. New trades will execute normally."
            )
            _safe_print("[Telegram Control] Trading engine RESUMED via Telegram /resume command.")
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to resume: {e}")


def is_bot_disabled() -> bool:
    """Check if the remote Telegram kill switch (/stop) is active."""
    return os.path.exists(BOT_DISABLED_FLAG)


def start_telegram_listener_background():
    """
    Starts the Telegram bot polling in a non-blocking daemon background thread.
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
        _safe_print("[Telegram Control] Background polling listener started. Listening for /status, /stop, /resume...")
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            _safe_print(f"[Telegram Control] Polling loop error: {e}")

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
    _safe_print("")
    _safe_print("  Starting foreground polling (Ctrl+C to exit)...")
    _safe_print("  Send /start, /status, /stop, or /resume from Telegram.")
    _safe_print("=" * 70)

    bot = _get_bot()
    if bot:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    else:
        _safe_print("[ERROR] Could not initialize Telegram bot. Check credentials.")
