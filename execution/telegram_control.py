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
  - /start executes: python main.py --live in background thread.
"""

import os
import sys
import json
import time
import uuid
import threading
import datetime
import subprocess

from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BOT_DISABLED_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BOT_DISABLED.flag")

# Module-level bot instance
_bot = None
_listener_started = False

# Registry for pending order approvals: { trade_id: { "event": threading.Event(), "approved": False } }
_pending_approvals = {}


def _safe_print(text: str):
    """Print text safely on Windows cp1252 consoles."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    """Returns current datetime in IST (India Standard Time: UTC+5:30)."""
    return datetime.datetime.now(IST_TZ)

def check_is_market_open() -> bool:
    """Validates whether current time is within official NSE or MCX trading windows (Mon-Fri)."""
    now = get_ist_now()
    if now.weekday() >= 5: # 5 = Saturday, 6 = Sunday
        return False
    current_time = now.time()
    nse_open = (datetime.time(9, 15) <= current_time <= datetime.time(15, 30))
    mcx_open = (datetime.time(17, 0) <= current_time <= datetime.time(23, 15))
    return nse_open or mcx_open


def _build_action_keyboard(telebot_module):
    """Generates an interactive inline button keyboard for Telegram messages."""
    markup = telebot_module.types.InlineKeyboardMarkup(row_width=2)
    btn_status = telebot_module.types.InlineKeyboardButton("📊 Status", callback_data="cb_status")
    btn_report = telebot_module.types.InlineKeyboardButton("📊 Performance Report", callback_data="cb_report")
    btn_trades = telebot_module.types.InlineKeyboardButton("📜 Trade Log", callback_data="cb_trades")
    btn_squareoff = telebot_module.types.InlineKeyboardButton("⚡ Square Off", callback_data="cb_squareoff")
    btn_stop = telebot_module.types.InlineKeyboardButton("🛑 Stop Engine", callback_data="cb_stop")
    btn_resume = telebot_module.types.InlineKeyboardButton("✅ Resume Engine", callback_data="cb_resume")
    markup.add(btn_status, btn_report)
    markup.add(btn_trades, btn_squareoff)
    markup.add(btn_stop, btn_resume)
    return markup


APPROVAL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "pending_approvals.json")

def _read_approval_store() -> Dict[str, Any]:
    if os.path.exists(APPROVAL_FILE):
        try:
            with open(APPROVAL_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _write_approval_store(store: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
        with open(APPROVAL_FILE, "w") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        _safe_print(f"[Approval Store Write Error] {e}")

def request_telegram_trade_approval(
    option_symbol: str,
    lot_size: int,
    entry_premium: float,
    total_cost: float,
    target_price: float,
    stop_price: float,
    timeout_seconds: int = 60
) -> bool:
    """
    Sends an interactive Telegram order approval prompt with [✅ Approve Order] and [❌ Reject Order] inline buttons.
    Waits up to timeout_seconds (default 60s) for user tap.
    Returns True if approved, False if rejected or timed out.
    """
    bot = _get_bot()
    if not bot or not TELEGRAM_CHAT_ID:
        _safe_print("[Telegram Control] Bot token or Chat ID missing. Skipping Telegram approval prompt.")
        return False

    import telebot

    trade_id = str(uuid.uuid4())[:8]
    store = _read_approval_store()
    store[trade_id] = {
        "status": "PENDING",
        "option_symbol": option_symbol,
        "total_cost": total_cost,
        "created_at": time.time()
    }
    _write_approval_store(store)

    msg_text = (
        "⚠️ [INTERACTIVE ORDER APPROVAL REQUIRED]\n"
        "========================================\n"
        f"Contract       : {option_symbol}\n"
        f"Quantity       : {lot_size} shares (1 Lot)\n"
        f"Entry Premium  : Rs {entry_premium:.2f} / share\n"
        f"Total Cost     : Rs {total_cost:,.2f} INR\n"
        f"Target (+25%)  : Rs {target_price:.2f}\n"
        f"Stop Loss (-12%): Rs {stop_price:.2f}\n"
        "========================================\n"
        "Do you authorize placing this real order on DhanHQ?"
    )

    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    btn_yes = telebot.types.InlineKeyboardButton("✅ Approve Order (YES)", callback_data=f"approve_{trade_id}")
    btn_no = telebot.types.InlineKeyboardButton("❌ Reject Order (NO)", callback_data=f"reject_{trade_id}")
    markup.add(btn_yes, btn_no)

    try:
        sent_msg = bot.send_message(TELEGRAM_CHAT_ID, msg_text, reply_markup=markup)
        _safe_print(f"[Telegram Control] Sent interactive order approval prompt to Telegram (ID: {trade_id}). Waiting {timeout_seconds}s...")
    except Exception as e:
        _safe_print(f"[Telegram Control Error] Could not send approval prompt: {e}")
        st = _read_approval_store()
        st.pop(trade_id, None)
        _write_approval_store(st)
        return False

    start_wait = time.time()
    is_approved = False
    is_set = False

    while time.time() - start_wait < timeout_seconds:
        time.sleep(0.5)
        st = _read_approval_store().get(trade_id, {})
        status = st.get("status")
        if status in ["APPROVED", "REJECTED"]:
            is_set = True
            is_approved = (status == "APPROVED")
            break

    st_final = _read_approval_store()
    st_final.pop(trade_id, None)
    _write_approval_store(st_final)

    try:
        if is_approved:
            bot.edit_message_text(
                f"✅ [ORDER APPROVED] {option_symbol} (Rs {total_cost:,.2f}) authorized by user. Order executing on DhanHQ...",
                TELEGRAM_CHAT_ID,
                sent_msg.message_id
            )
        elif not is_set:
            bot.edit_message_text(
                f"⏰ [APPROVAL TIMED OUT] Request for {option_symbol} timed out after {timeout_seconds}s. Order aborted safely.",
                TELEGRAM_CHAT_ID,
                sent_msg.message_id
            )
        else:
            bot.edit_message_text(
                f"❌ [ORDER REJECTED] {option_symbol} rejected by user via Telegram. Trade execution aborted.",
                TELEGRAM_CHAT_ID,
                sent_msg.message_id
            )
    except Exception:
        pass

    return is_approved


def _get_bot():
    """Lazily initialize the telebot instance."""
    global _bot
    if _bot is None and TELEGRAM_BOT_TOKEN and not TELEGRAM_BOT_TOKEN.startswith("your_"):
        try:
            import telebot
            import logging
            telebot.logger.setLevel(logging.CRITICAL)
            _bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
            _register_handlers(_bot)
        except ImportError:
            _safe_print("[Telegram Control] pyTelegramBotAPI not installed. Run: pip install pyTelegramBotAPI")
            return None
    return _bot


def start_telegram_listener_background():
    """
    Starts the Telegram bot polling in a non-blocking background thread.
    Safe to call multiple times -- only the first call starts the listener.
    """
    global _listener_started

    if _listener_started:
        return

    if os.getenv("TELEGRAM_LISTENER_DISABLED") == "1":
        _safe_print("[Telegram Control] Sub-process execution mode. Skipping duplicate Telegram polling listener.")
        return

    # Start Interactive Report Web Server in Background
    try:
        from web.server import start_web_server_background
        start_web_server_background()
    except Exception as ws_err:
        _safe_print(f"[Telegram Control Warning] Could not start web server: {ws_err}")

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("your_"):
        _safe_print("[Telegram Control] TELEGRAM_BOT_TOKEN not configured. Background listener skipped.")
        return

    bot = _get_bot()
    if bot is None:
        return

    def _polling_loop():
        _safe_print("[Telegram Control] Cloud polling listener started. Listening 24/7 for /start, /status, /report, /reports, /trades, /stop, /resume...")
        import time
        try:
            bot.remove_webhook()
            bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass

        consecutive_409 = 0
        while True:
            try:
                bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
                consecutive_409 = 0
            except Exception as e:
                err_str = str(e)
                if "409" in err_str or "Conflict" in err_str:
                    consecutive_409 += 1
                    if consecutive_409 == 1:
                        _safe_print("[Telegram Control] 409 Conflict (previous process shutting down). Auto-retrying in background...")
                    time.sleep(10)
                else:
                    _safe_print(f"[Telegram Control] Polling Error: {e}")
                    time.sleep(30)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    _listener_started = True


def _register_handlers(bot):
    """Register all Telegram command handlers and inline button callbacks on the bot instance."""
    import telebot

    @bot.message_handler(commands=["help"])
    def cmd_help(message):
        help_msg = (
            "🤖 <b>[QUANT TRADING BOT COMMANDS]</b>\n"
            "========================================\n"
            "📊 <b>/status</b> - Current system status & wallet balance\n"
            "📄 <b>/report</b> - Download live EOD market report\n"
            "📜 <b>/trades</b> - View today's trade execution log\n"
            "⚡ <b>/squareoff</b> - Emergency manual square-off open position\n"
            "🛑 <b>/stop</b> - Activate remote kill switch (Pause trading)\n"
            "✅ <b>/resume</b> - Resume automated scanner & trading\n"
            "❓ <b>/help</b> - Show this help menu\n"
            "========================================"
        )
        bot.reply_to(message, help_msg, parse_mode="HTML", reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["start"])
    def cmd_start(message):
        if not check_is_market_open():
            bot.reply_to(message, "Market is closed. Active trading sessions: Session 1 NSE (09:00 - 15:30 IST) & Session 2 MCX (17:00 - 23:15 IST).", reply_markup=_build_action_keyboard(telebot))
            return

        bot.reply_to(
            message,
            "🚀 [EXECUTING ENGINE] Launching trading pipeline ('python main.py --live')...\n\n"
            "System scanning active dual-session markets & quantitative matrix.",
            reply_markup=_build_action_keyboard(telebot)
        )

        def _run_pipeline_job():
            try:
                cmd = [sys.executable, "main.py", "--live", "--auto-approve", "--override-daily-limit"]
                _safe_print(f"[Telegram Control] Executing command via /start: {' '.join(cmd)}")
                env = os.environ.copy()
                env["TELEGRAM_LISTENER_DISABLED"] = "1"
                proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                out, _ = proc.communicate(timeout=180)
                _safe_print(f"[Telegram Control Output]\n{out[-500:] if out else 'No output'}")
                if out and ("LIVE LIMIT ORDER PLACED" in out or "CLOSED" in out or "EXECUTED" in out or "TARGET HIT" in out):
                    bot.send_message(TELEGRAM_CHAT_ID, f"✅ <b>[PIPELINE EXECUTED SUCCESSFULLY]</b>\n\n<pre>{out[-600:]}</pre>", parse_mode="HTML")
                elif out and "UDAPI1154" in out:
                    bot.send_message(TELEGRAM_CHAT_ID, "⚠️ <b>[DHAN API NOTICE]</b>\nDhanHQ API may require Railway cloud IP for order execution. Please ensure service is deployed on Railway.", parse_mode="HTML")
                elif out and "Session lock" in out:
                    bot.send_message(TELEGRAM_CHAT_ID, "ℹ️ <b>[SESSION LOCK]</b> Session trade cap reached for today.", parse_mode="HTML")
                else:
                    bot.send_message(TELEGRAM_CHAT_ID, f"ℹ️ <b>[PIPELINE SUMMARY]</b>\n<pre>{out[-400:] if out else 'Scan finished'}</pre>", parse_mode="HTML")
            except Exception as ex:
                _safe_print(f"[Telegram Control Run Error] {ex}")

        # Run pipeline in a background thread so Telegram listener stays non-blocking
        t = threading.Thread(target=_run_pipeline_job, daemon=True)
        t.start()

    @bot.message_handler(commands=["help"])
    def cmd_help(message):
        if not check_is_market_open():
            bot.reply_to(message, "market is closed try during 9:15 AM to 3:15 PM", reply_markup=_build_action_keyboard(telebot))
            return

        help_text = (
            "[DHAN LIVE ALGORITHMIC ENGINE]\n"
            "-------------------------------------------\n"
            "Available Commands:\n"
            "/start   - Launch live trading pipeline ('python main.py --live --auto-approve')\n"
            "/status  - Live DhanHQ Wallet Balance & Bot Health\n"
            "/report  - Download today's Live EOD HTML report\n"
            "/trades  - View today's executed trade log\n"
            "/stop    - Emergency Pause (Kill Switch)\n"
            "/resume  - Re-enable Trading Engine\n"
            "/help    - Show this help message\n"
            "-------------------------------------------\n"
            "Engine Version: v3.0 (DhanHQ Live Production)"
        )
        bot.reply_to(message, help_text, reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["stop"])
    def cmd_stop(message):
        try:
            with open(BOT_DISABLED_FLAG, "w") as f:
                f.write(f"PAUSED_AT={datetime.datetime.now().isoformat()}")
            bot.reply_to(
                message,
                "🛑 [TRADING ENGINE PAUSED]\n"
                "Remote kill switch activated. All automated order placements HALTED.\n"
                "Send /resume to re-enable trading.",
                reply_markup=_build_action_keyboard(telebot)
            )
            _safe_print("[Telegram Control] Trading engine PAUSED via Telegram /stop command.")
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to activate kill switch: {e}")

    @bot.message_handler(commands=["resume"])
    def cmd_resume(message):
        if not check_is_market_open():
            bot.reply_to(message, "Market is closed. Active trading sessions: Session 1 NSE (09:00 - 15:30 IST) & Session 2 MCX (17:00 - 23:15 IST).", reply_markup=_build_action_keyboard(telebot))
            return

        try:
            if os.path.exists(BOT_DISABLED_FLAG):
                os.remove(BOT_DISABLED_FLAG)
            bot.reply_to(
                message,
                "▶️ [TRADING ENGINE RESUMED]\n"
                "Dynamic scanning re-enabled. Triggering active session market scan...",
                reply_markup=_build_action_keyboard(telebot)
            )
            _safe_print("[Telegram Control] Trading engine RESUMED via Telegram /resume command.")

            def _run_resume_job():
                try:
                    cmd = [sys.executable, "main.py", "--live", "--auto-approve", "--override-daily-limit"]
                    _safe_print(f"[Telegram Control] Executing pipeline scan via /resume: {' '.join(cmd)}")
                    env = os.environ.copy()
                    env["TELEGRAM_LISTENER_DISABLED"] = "1"
                    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    out, _ = proc.communicate(timeout=180)
                    _safe_print(f"[Telegram Control Output]\n{out[-500:] if out else 'No output'}")
                    if out and ("LIVE LIMIT ORDER PLACED" in out or "CLOSED" in out or "EXECUTED" in out or "TARGET HIT" in out):
                        bot.send_message(TELEGRAM_CHAT_ID, f"✅ <b>[PIPELINE EXECUTED SUCCESSFULLY]</b>\n\n<pre>{out[-600:]}</pre>", parse_mode="HTML")
                    elif out and "UDAPI1154" in out:
                        bot.send_message(TELEGRAM_CHAT_ID, "⚠️ <b>[DHAN API NOTICE]</b>\nDhanHQ API may require Railway cloud IP for order execution. Please ensure service is deployed on Railway.", parse_mode="HTML")
                    elif out and "Session lock" in out:
                        bot.send_message(TELEGRAM_CHAT_ID, "ℹ️ <b>[SESSION LOCK]</b> Session trade cap reached for today.", parse_mode="HTML")
                    else:
                        bot.send_message(TELEGRAM_CHAT_ID, f"ℹ️ <b>[PIPELINE SUMMARY]</b>\n<pre>{out[-400:] if out else 'Scan finished'}</pre>", parse_mode="HTML")
                except Exception as ex:
                    _safe_print(f"[Telegram Control Run Error] {ex}")

            t = threading.Thread(target=_run_resume_job, daemon=True)
            t.start()
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Failed to resume: {e}")

    @bot.message_handler(commands=["status"])
    def cmd_status(message):
        now = get_ist_now()
        current_time = now.time()

        nse_active = (now.weekday() < 5) and (datetime.time(9, 15) <= current_time <= datetime.time(15, 30))
        mcx_active = (now.weekday() < 5) and (datetime.time(17, 0) <= current_time <= datetime.time(23, 15))

        if nse_active:
            active_session_str = "Session 1 Active (NSE Equities)"
        elif mcx_active:
            active_session_str = "Session 2 Active (MCX Commodities)"
        else:
            active_session_str = "STANDBY (Between Sessions / Closed)"

        is_paused = os.path.exists(BOT_DISABLED_FLAG)
        engine_state = "PAUSED (Kill Switch Active)" if is_paused else ("ONLINE & SCANNING" if (nse_active or mcx_active) else "STANDBY")

        # Fetch live wallet balance directly from Dhan API
        wallet_str = "Rs 1,000.00 INR"
        try:
            from dhanhq import dhanhq, DhanContext
            from config.settings import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TOKEN_FILE_PATH
            client_id = DHAN_CLIENT_ID or os.getenv("DHAN_CLIENT_ID", "")
            token = DHAN_ACCESS_TOKEN or os.getenv("DHAN_ACCESS_TOKEN", "")
            token_file = TOKEN_FILE_PATH if os.path.exists(TOKEN_FILE_PATH) else "access_token.json"
            if (not token or token.startswith("MOCK")) and os.path.exists(token_file):
                with open(token_file, "r") as f:
                    tdata = json.load(f)
                    token = tdata.get("access_token", token)
                    client_id = tdata.get("client_id", client_id)
            if token and not token.startswith("MOCK"):
                ctx = DhanContext(client_id, token)
                dhan = dhanhq(ctx)
                res = dhan.get_fund_limits()
                data = res.get("data", {}) if isinstance(res, dict) else {}
                avail = float(data.get("availabelBalance") or data.get("availableBalance") or 0.0)
                wallet_str = f"Rs {avail:,.2f} INR"
                # Sync state.json
                from execution.state_manager import StateManager
                sm = StateManager()
                sm.state["current_wallet_balance"] = avail
                sm._save_state(sm.state)
        except Exception:
            pass

        # Fetch today's trade count from DhanHQ order book
        trade_count = "0"
        try:
            from dhanhq import dhanhq, DhanContext
            from config.settings import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TOKEN_FILE_PATH
            client_id = DHAN_CLIENT_ID or os.getenv("DHAN_CLIENT_ID", "")
            token = DHAN_ACCESS_TOKEN or os.getenv("DHAN_ACCESS_TOKEN", "")
            token_file = TOKEN_FILE_PATH if os.path.exists(TOKEN_FILE_PATH) else "access_token.json"
            if (not token or token.startswith("MOCK")) and os.path.exists(token_file):
                with open(token_file, "r") as f:
                    tdata = json.load(f)
                    token = tdata.get("access_token", token)
                    client_id = tdata.get("client_id", client_id)
            if token and not token.startswith("MOCK"):
                ctx = DhanContext(client_id, token)
                dhan = dhanhq(ctx)
                res = dhan.get_order_list()
                orders = res.get("data", []) if isinstance(res, dict) else []
                today_str = datetime.datetime.now(IST_TZ).date().isoformat()
                filled_today = [o for o in orders if isinstance(o, dict)
                                and str(o.get("orderStatus", "")).upper() in ("TRADED", "FILLED", "SUCCESS", "EXECUTED")
                                and today_str in str(o.get("createTime", ""))]
                trade_count = str(len(filled_today))
        except Exception:
            pass

        status_msg = (
            "[DHAN DUAL-SESSION SYSTEM STATUS]\n"
            "========================================\n"
            f"Active Session      : {active_session_str}\n"
            f"Session 1 (NSE)     : {'ONLINE' if nse_active else 'CLOSED (09:00 - 15:30 IST)'}\n"
            f"Session 2 (MCX)     : {'ONLINE' if mcx_active else 'CLOSED (17:00 - 23:15 IST)'}\n"
            f"💰 [Dhan Live Wallet] Balance: {wallet_str} | Broker: DhanHQ API v2\n"
            f"Engine State        : {engine_state}\n"
            f"Live Trades Today   : {trade_count}\n"
            "========================================"
        )
        bot.reply_to(message, status_msg, reply_markup=_build_action_keyboard(telebot))

    @bot.message_handler(commands=["settoken"])
    def cmd_settoken(message):
        try:
            parts = message.text.strip().split()
            if len(parts) < 2:
                bot.reply_to(message, "⚠️ Usage: `/settoken <YOUR_24HR_DHAN_ACCESS_TOKEN>`", parse_mode="Markdown")
                return
            new_token = parts[1].strip()
            os.environ["DHAN_ACCESS_TOKEN"] = new_token
            from config.settings import TOKEN_FILE_PATH
            import json
            token_payload = {
                "access_token": new_token,
                "updated_at": datetime.datetime.now().isoformat(),
                "updated_via": "telegram_settoken"
            }
            os.makedirs(os.path.dirname(TOKEN_FILE_PATH), exist_ok=True)
            with open(TOKEN_FILE_PATH, "w") as f:
                json.dump(token_payload, f, indent=4)
            bot.reply_to(message, "✅ <b>[Dhan Access Token Updated]</b>\nNew 24-hour Dhan Access Token saved & activated successfully!", parse_mode="HTML")
            _safe_print("[Telegram Control] Dhan Access Token updated live via Telegram /settoken command.")
        except Exception as e:
            bot.reply_to(message, f"❌ Failed to update Dhan token: {e}")

    @bot.message_handler(commands=["report", "reports", "report_csv"])
    def cmd_report(message):
        # Single Unified Report Command: Returns summary text & interactive Web App URL button
        try:
            import secrets
            session_token = secrets.token_urlsafe(16)

            railway_url = os.getenv("RAILWAY_STATIC_URL", "")
            public_url = os.getenv("WEB_REPORT_URL", os.getenv("PUBLIC_URL", ""))

            if public_url:
                base_url = public_url.rstrip("/")
                if not base_url.startswith("http"):
                    base_url = f"https://{base_url}"
            elif railway_url:
                base_url = f"https://{railway_url.rstrip('/')}"
            else:
                port = os.getenv("PORT", "5000")
                base_url = f"http://localhost:{port}"

            web_app_url = f"{base_url}/report?token={session_token}"

            import html
            escaped_url = html.escape(web_app_url)

            is_https = base_url.startswith("https://") and "localhost" not in base_url and "127.0.0.1" not in base_url

            msg_text = (
                "📊 <b>[QUANT PERFORMANCE REPORT DASHBOARD]</b>\n"
                "========================================\n"
            )

            try:
                from reports.trade_report import get_trade_report_data
                data = get_trade_report_data()
                net_pnl = data["net_pnl"]
                pnl_str = f"+Rs {net_pnl:,.2f}" if net_pnl >= 0 else f"-Rs {abs(net_pnl):,.2f}"

                msg_text += (
                    f"<b>Today's Trades  :</b> {data['total_trades']} (NSE: {data['nse_trades']} | MCX: {data['mcx_trades']})\n"
                    f"<b>Win Rate        :</b> {data['win_rate']}%\n"
                    f"<b>Net Realized PnL:</b> <code>{pnl_str} INR</code>\n"
                    f"<b>Max Drawdown    :</b> {data['max_drawdown_pct']}%\n"
                    "========================================\n"
                )
            except Exception:
                pass

            msg_text += (
                f'👉 <a href="{escaped_url}"><b>Open Interactive Performance Report</b></a> 👈\n\n'
                f'<code>{escaped_url}</code>\n\n'
                "<i>Tap the link above to view interactive dashboard & download PDF reports.</i>"
            )

            if is_https:
                try:
                    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                    btn_web = telebot.types.InlineKeyboardButton("📊 Open Interactive Performance Report", url=web_app_url)
                    markup.add(btn_web)
                    bot.reply_to(message, msg_text, parse_mode="HTML", reply_markup=markup)
                except Exception as btn_err:
                    bot.reply_to(message, msg_text, parse_mode="HTML", reply_markup=_build_action_keyboard(telebot))
            else:
                bot.reply_to(message, msg_text, parse_mode="HTML", reply_markup=_build_action_keyboard(telebot))

            _safe_print(f"[Telegram Control] Sent interactive report URL: {web_app_url}")
        except Exception as e:
            bot.reply_to(message, f"❌ Failed to generate report link: {e}")

    @bot.message_handler(commands=["trades"])
    def cmd_trades(message):
        # /trades works ANYTIME (both when market is open and closed)
        try:
            from dhanhq import dhanhq, DhanContext
            from config.settings import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TOKEN_FILE_PATH

            client_id = DHAN_CLIENT_ID or os.getenv("DHAN_CLIENT_ID", "")
            token = DHAN_ACCESS_TOKEN or os.getenv("DHAN_ACCESS_TOKEN", "")
            token_file = TOKEN_FILE_PATH if os.path.exists(TOKEN_FILE_PATH) else "access_token.json"
            if (not token or token.startswith("MOCK")) and os.path.exists(token_file):
                with open(token_file, "r") as f:
                    tdata = json.load(f)
                    token = tdata.get("access_token", token)
                    client_id = tdata.get("client_id", client_id)

            today_str = datetime.datetime.now(IST_TZ).date().isoformat()

            # --- Primary: DhanHQ Live Order Book ---
            dhan_trades = []
            if token and not token.startswith("MOCK"):
                try:
                    ctx = DhanContext(client_id, token)
                    dhan = dhanhq(ctx)
                    res = dhan.get_order_list()
                    orders = res.get("data", []) if isinstance(res, dict) else []
                    dhan_trades = [
                        o for o in orders
                        if isinstance(o, dict)
                        and str(o.get("orderStatus", "")).upper() in ("TRADED", "FILLED", "SUCCESS", "EXECUTED")
                        and today_str in str(o.get("createTime", ""))
                    ]
                except Exception as dhan_err:
                    _safe_print(f"[Trades Dhan API Notice] {dhan_err}")

            if dhan_trades:
                lines = [f"[TODAY'S DHAN LIVE EXECUTED TRADES] ({len(dhan_trades)} orders)\n========================================"]
                total_pnl = 0.0
                for o in dhan_trades:
                    sym = o.get("tradingSymbol", "N/A")
                    tx = o.get("transactionType", "BUY")
                    avg_p = float(o.get("price") or o.get("averageTradedPrice") or 0.0)
                    qty = int(o.get("quantity") or 0)
                    status = o.get("orderStatus", "TRADED")
                    exch = o.get("exchangeSegment", "")
                    t_str = str(o.get("createTime", ""))
                    lines.append(f"• {tx} {sym} ({qty} qty) @ Rs {avg_p:.2f} [{status}] [{exch}] {t_str}")
                lines.append("========================================")
                bot.reply_to(message, "\n".join(lines), reply_markup=_build_action_keyboard(telebot))
                return

            # --- Fallback: StateManager local trade log ---
            try:
                from execution.state_manager import StateManager
                sm = StateManager()
                local_trades = sm.get_todays_trades()
                if local_trades:
                    lines = [f"[TODAY'S LOCAL TRADE LOG] ({len(local_trades)} trades)\n========================================"]
                    total_pnl = 0.0
                    for i, t in enumerate(local_trades, 1):
                        symbol = (
                            t.get("option_symbol")
                            or t.get("tradingSymbol")
                            or (t.get("option_contract") or {}).get("option_symbol", "N/A")
                        )
                        entry = float(t.get("entry_premium") or 0.0)
                        exit_p = float(t.get("exit_premium") or 0.0)
                        pnl = float(t.get("net_pnl") or 0.0)
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
                    return
            except Exception:
                pass

            bot.reply_to(message, "[TRADES] No live trades executed today.", reply_markup=_build_action_keyboard(telebot))
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Could not fetch trades: {e}")

    @bot.message_handler(commands=["squareoff", "close", "exit"])
    def cmd_squareoff(message):
        bot.reply_to(message, "[SQUARE OFF] Requesting instant live position exit on DhanHQ...", reply_markup=_build_action_keyboard(telebot))
        try:
            from main import execute_hard_eod_squareoff
            res = execute_hard_eod_squareoff(dry_run=False)
            if res:
                msg = (
                    f"✅ [SQUARE OFF EXECUTED]\n"
                    f"========================================\n"
                    f"Contract  : {res.get('option_symbol', 'N/A')}\n"
                    f"Order ID  : {res.get('order_id', 'N/A')}\n"
                    f"Exit Price: Rs {res.get('exit_premium', 0.0):.2f}\n"
                    f"Net PnL   : Rs {res.get('net_pnl', 0.0):,.2f} INR\n"
                    f"========================================"
                )
                bot.reply_to(message, msg, reply_markup=_build_action_keyboard(telebot))
            else:
                bot.reply_to(message, "ℹ️ [SQUARE OFF] No open positions to close. All positions clean.", reply_markup=_build_action_keyboard(telebot))
        except Exception as e:
            bot.reply_to(message, f"[ERROR] Could not square off position: {e}", reply_markup=_build_action_keyboard(telebot))

    _processed_callback_ids = set()

    # Callback Query Handler for Interactive Inline Buttons
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback_query(call):
        # 1. Immediately answer callback query to prevent Telegram server retries/loops
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        # 2. Debounce callback queries by ID to prevent duplicate executions
        call_id = str(getattr(call, "id", ""))
        if call_id and call_id in _processed_callback_ids:
            return
        if call_id:
            _processed_callback_ids.add(call_id)
            if len(_processed_callback_ids) > 100:
                try:
                    _processed_callback_ids.pop()
                except Exception:
                    pass

        # 3. Route callback action
        if call.data.startswith("approve_"):
            trade_id = call.data.split("approve_")[1]
            store = _read_approval_store()
            if trade_id in store:
                store[trade_id]["status"] = "APPROVED"
                _write_approval_store(store)
                _safe_print(f"[Telegram Control] Trade ID {trade_id} APPROVED via Telegram inline button.")
            if trade_id in _pending_approvals:
                _pending_approvals[trade_id]["approved"] = True
                _pending_approvals[trade_id]["event"].set()
        elif call.data.startswith("reject_"):
            trade_id = call.data.split("reject_")[1]
            store = _read_approval_store()
            if trade_id in store:
                store[trade_id]["status"] = "REJECTED"
                _write_approval_store(store)
                _safe_print(f"[Telegram Control] Trade ID {trade_id} REJECTED via Telegram inline button.")
            if trade_id in _pending_approvals:
                _pending_approvals[trade_id]["approved"] = False
                _pending_approvals[trade_id]["event"].set()
        elif call.data == "cb_status":
            cmd_status(call.message)
        elif call.data == "cb_report":
            cmd_report(call.message)
        elif call.data == "cb_trades":
            cmd_trades(call.message)
        elif call.data == "cb_squareoff":
            cmd_squareoff(call.message)
        elif call.data == "cb_stop":
            cmd_stop(call.message)
        elif call.data == "cb_resume":
            cmd_resume(call.message)


def is_bot_disabled() -> bool:
    """Check if the remote Telegram kill switch (/stop) is active."""
    return os.path.exists(BOT_DISABLED_FLAG)




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
