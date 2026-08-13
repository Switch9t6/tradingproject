import os
import json
import sqlite3
import datetime
from typing import Dict, Any, Optional, List
from config.settings import STATE_FILE_PATH, DB_FILE_PATH, LOGS_DIR, MAX_DAILY_TRADES, INITIAL_WALLET_CAPITAL

class StateManager:
    """
    Persistent state manager using SQLite (logs/trades.db) and state.json to enforce:
    1. Session-Isolated Dynamic Trade Caps (Max 1 trade per session: NSE_FO & MCX_FO).
    2. Automatic Midnight IST Reset.
    3. Dynamic Real-Time Wallet Balance tracking across winning and losing trades.
    4. Telegram alerting when a session cap is reached.
    """
    def __init__(self, db_path: str = DB_FILE_PATH, state_path: str = STATE_FILE_PATH, force_reset: bool = False):
        self.db_path = db_path
        self.state_path = state_path
        os.makedirs(LOGS_DIR, exist_ok=True)
        self._init_sqlite_db()
        if force_reset:
            self.state = self.reset_daily_state()
        else:
            self.state = self._load_or_init_state()

    def _init_sqlite_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                execution_mode TEXT DEFAULT 'DRY_RUN',
                exchange TEXT DEFAULT 'NSE_FO',
                underlying_symbol TEXT NOT NULL,
                option_symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                entry_premium REAL NOT NULL,
                exit_premium REAL,
                target_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                gross_pnl REAL DEFAULT 0.0,
                friction_fees REAL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                status TEXT NOT NULL,
                exit_reason TEXT
            )
        """)
        conn.commit()
        
        # Column migration checks for existing DBs
        cursor.execute("PRAGMA table_info(trades)")
        columns = [column[1] for column in cursor.fetchall()]
        if "execution_mode" not in columns:
            cursor.execute("ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'DRY_RUN'")
        if "exchange" not in columns:
            cursor.execute("ALTER TABLE trades ADD COLUMN exchange TEXT DEFAULT 'NSE_FO'")
        conn.commit()
        conn.close()

    def _default_state(self) -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        return {
            "date": today_str,
            "last_reset_date": today_str,
            "NSE_FO_trades_today": 0,
            "MCX_FO_trades_today": 0,
            "is_nse_locked_today": False,
            "is_mcx_locked_today": False,
            "session_cap_alerted": {
                "NSE_FO": False,
                "MCX_FO": False
            },
            "active_trade_id": None,
            "active_position": None,
            "current_wallet_balance": INITIAL_WALLET_CAPITAL,
            "disabled_segments": {}
        }

    def reset_daily_state(self) -> Dict[str, Any]:
        """
        Explicitly reset state for a fresh daily run or test simulation.
        """
        state = self._default_state()
        self._save_state(state)
        print(f"[State Manager] Daily session state cleanly reset for {state['date']}. Wallet Balance: Rs {state['current_wallet_balance']:,.2f} INR.")
        return state

    def _load_or_init_state(self) -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, 'r') as f:
                    state = json.load(f)
                    if state.get("date") != today_str or state.get("last_reset_date") != today_str:
                        print(f"[State Manager] New trading session detected ({today_str}). Resetting session-isolated counters.")
                        state["date"] = today_str
                        state["last_reset_date"] = today_str
                        state["NSE_FO_trades_today"] = 0
                        state["MCX_FO_trades_today"] = 0
                        state["is_nse_locked_today"] = False
                        state["is_mcx_locked_today"] = False
                        state["session_cap_alerted"] = {"NSE_FO": False, "MCX_FO": False}
                        state["active_trade_id"] = None
                        state["active_position"] = None
                        state["disabled_segments"] = {}
                        if "current_wallet_balance" not in state:
                            state["current_wallet_balance"] = INITIAL_WALLET_CAPITAL
                        self._save_state(state)
                    return state
            except Exception as e:
                print(f"[State Manager] Error reading state.json: {e}. Re-initializing fresh state.")
                state = self._default_state()
                self._save_state(state)
                return state
        else:
            state = self._default_state()
            self._save_state(state)
            return state

    def _save_state(self, state: Dict[str, Any]):
        try:
            with open(self.state_path, 'w') as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"[State Manager] Error saving state: {e}")

    def is_drawdown_limit_exceeded(self, max_drawdown_pct: float = 5.0) -> bool:
        """
        Calculates today's realized PnL against portfolio capital.
        Returns True if total loss exceeds max_drawdown_pct (e.g. -5.0%), halting further trade execution.
        """
        today_str = datetime.date.today().isoformat()
        trades = self.get_todays_trades()
        net_daily_pnl = sum((t.get("net_pnl") or 0.0) for t in trades)
        wallet = self.get_current_wallet_balance()
        max_allowed_loss = -1.0 * (wallet * (max_drawdown_pct / 100.0))
        
        if net_daily_pnl < max_allowed_loss:
            print(f"  [CIRCUIT BREAKER ACTIVATED] Daily Net PnL (Rs {net_daily_pnl:,.2f}) breached -{max_drawdown_pct}% max drawdown limit. All trading halted for today.")
            return True
        return False

    def reconcile_state_with_db(self):
        """
        Self-healing state reconciliation:
        Checks actual recorded trades in SQLite database for today.
        If 0 actual trades exist for NSE_FO or MCX_FO in DB, clears invalid session locks in state.json!
        """
        try:
            trades = self.get_today_trades()
            
            # Only LIVE trades count toward session cap — DRY_RUN trades are ignored
            live_nse_trades = [t for t in trades if t.get("execution_mode", "").upper() == "LIVE" and ("NSE" in str(t.get("exchange", "")).upper() or "NIFTY" in str(t.get("option_symbol", "")).upper() or "BANK" in str(t.get("option_symbol", "")).upper())]
            live_mcx_trades = [t for t in trades if t.get("execution_mode", "").upper() == "LIVE" and ("MCX" in str(t.get("exchange", "")).upper() or "CRUDE" in str(t.get("option_symbol", "")).upper() or "CRUDE" in str(t.get("underlying_symbol", "")).upper())]
            
            actual_nse_count = len(live_nse_trades)
            actual_mcx_count = len(live_mcx_trades)
            
            state_changed = False
            
            if self.state.get("NSE_FO_trades_today", 0) > actual_nse_count:
                self.state["NSE_FO_trades_today"] = actual_nse_count
                state_changed = True
            if actual_nse_count == 0 and self.state.get("is_nse_locked_today"):
                self.state["is_nse_locked_today"] = False
                state_changed = True
                
            if self.state.get("MCX_FO_trades_today", 0) > actual_mcx_count:
                self.state["MCX_FO_trades_today"] = actual_mcx_count
                state_changed = True
            if actual_mcx_count == 0 and self.state.get("is_mcx_locked_today"):
                self.state["is_mcx_locked_today"] = False
                state_changed = True

            if state_changed:
                print(f"[State Reconciled] Corrected session state from DB truth (NSE Trades: {actual_nse_count}, MCX Trades: {actual_mcx_count}).")
                self._save_state(self.state)
        except Exception as e:
            print(f"[State Reconcile Notice] {e}")

    def is_trade_allowed_today(self, exchange: str = "NSE_FO", override_daily_limit: bool = False, dry_run: bool = True) -> bool:
        """
        ENFORCE DYNAMIC SESSION GATES:
        - In DRY_RUN mode: Allow up to 5 trades per session (NSE & MCX).
        - In LIVE mode: Allow 1 trade per session (NSE & MCX).
        """
        self._check_date_reset()
        self.reconcile_state_with_db()
        if self.is_drawdown_limit_exceeded():
            return False

        if override_daily_limit:
            print(f"[MANUAL OVERRIDE ACTIVE] Session trade cap lockout manually bypassed for {exchange} ({self.state['date']}).")
            return True

        segment = "MCX_FO" if ("MCX" in str(exchange).upper() or "CRUDE" in str(exchange).upper()) else "NSE_FO"
        from config.settings import DRY_RUN_MAX_TRADES_PER_SESSION, MAX_DAILY_TRADES
        max_trades = DRY_RUN_MAX_TRADES_PER_SESSION if dry_run else MAX_DAILY_TRADES

        if segment == "MCX_FO":
            count = self.state.get("MCX_FO_trades_today", 0)
            locked = self.state.get("is_mcx_locked_today", False)
            session_name = "MCX Commodity (Evening Session)"
        else:
            count = self.state.get("NSE_FO_trades_today", 0)
            locked = self.state.get("is_nse_locked_today", False)
            session_name = "NSE Equity (Morning Session)"

        if count >= max_trades or locked:
            print(f"[State Manager] Trade BLOCKED: Max {max_trades} trade cap reached for {session_name} on {self.state['date']}.")
            return False

        return True

    def get_current_wallet_balance(self) -> float:
        self._check_date_reset()
        return float(self.state.get("current_wallet_balance", INITIAL_WALLET_CAPITAL))

    def record_entry_trade(self, option_contract: Dict[str, Any], entry_premium: float, target_p: float, stop_p: float, execution_mode: str = "DRY_RUN", exchange: str = "NSE_FO") -> int:
        self._check_date_reset()
        today_str = datetime.date.today().isoformat()
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        ex_segment = "MCX_FO" if ("MCX" in str(exchange).upper() or "CRUDE" in str(exchange).upper() or option_contract.get("is_mcx") or "CRUDE" in str(option_contract.get("underlying_symbol")).upper()) else "NSE_FO"

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                trade_date, entry_time, execution_mode, exchange, underlying_symbol, option_symbol, option_type,
                strike_price, quantity, entry_premium, target_price, stop_price, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today_str,
            now_str,
            execution_mode,
            ex_segment,
            option_contract["underlying_symbol"],
            option_contract["option_symbol"],
            option_contract["option_type"],
            option_contract["strike_price"],
            option_contract["lot_size"],
            entry_premium,
            target_p,
            stop_p,
            "OPEN"
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Only increment session cap counter for REAL LIVE trades
        if execution_mode.upper() == "LIVE":
            if ex_segment == "MCX_FO":
                self.state["MCX_FO_trades_today"] = self.state.get("MCX_FO_trades_today", 0) + 1
            else:
                self.state["NSE_FO_trades_today"] = self.state.get("NSE_FO_trades_today", 0) + 1
            print(f"[State Manager] LIVE Trade #{trade_id} ({ex_segment}) recorded. SESSION LOCK ACTIVATED for today.")
        else:
            print(f"[State Manager] DRY_RUN Trade #{trade_id} recorded (session cap NOT incremented — dry-run only).")

        self.state["active_trade_id"] = trade_id
        self.state["active_position"] = {
            "trade_id": trade_id,
            "execution_mode": execution_mode,
            "exchange": ex_segment,
            "option_symbol": option_contract["option_symbol"],
            "fyers_symbol": option_contract.get("fyers_symbol") or option_contract.get("instrument_key") or "",
            "tick_size": float(option_contract.get("tick_size") or 0.05),
            "quantity": option_contract["lot_size"],
            "entry_premium": entry_premium,
            "target_price": target_p,
            "stop_price": stop_p
        }
        self._save_state(self.state)
        return trade_id

    def record_exit_trade(self, trade_id: int, exit_premium: float, friction_fees: float = 0.0, exit_reason: str = "EXIT"):
        self._check_date_reset()
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT quantity, entry_premium, execution_mode, exchange FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()
        
        ex_segment = "NSE_FO"
        if row:
            quantity, entry_premium, mode, ex_segment = row
            from reporting.friction_calculator import calculate_trade_friction
            f_res = calculate_trade_friction(quantity, entry_premium, exit_premium)
            gross_pnl = f_res["gross_pnl"]
            calc_friction = f_res["total_friction"] if friction_fees <= 0 else friction_fees
            net_pnl = round(gross_pnl - calc_friction, 2)
            
            cursor.execute("""
                UPDATE trades
                SET exit_time = ?, exit_premium = ?, gross_pnl = ?, friction_fees = ?, net_pnl = ?, status = ?, exit_reason = ?
                WHERE id = ?
            """, (now_str, exit_premium, gross_pnl, calc_friction, net_pnl, "CLOSED", exit_reason, trade_id))
            conn.commit()
            
            # Settle Net PnL into Real-Time Wallet Balance
            prev_wallet = self.state.get("current_wallet_balance", INITIAL_WALLET_CAPITAL)
            new_wallet = max(0.0, round(prev_wallet + net_pnl, 2))
            self.state["current_wallet_balance"] = new_wallet
            
            print(f"[State Manager] Trade #{trade_id} ({mode} / {ex_segment}) CLOSED. Exit Premium: Rs {exit_premium:.2f} | Net PnL: Rs {net_pnl:,.2f} INR | Updated Real-Time Wallet: Rs {new_wallet:,.2f} INR")

            # Trigger AI Self-Learning Engine Post-Mortem Analysis
            try:
                from learning.self_learning_engine import analyze_closed_trade
                analyze_closed_trade({
                    "id": trade_id,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason,
                    "exchange": ex_segment
                })
            except Exception as ai_err:
                print(f"[AI Learning Engine Notice] {ai_err}")
            
        conn.close()

        # Lock out new entries for that segment until the next calendar day
        if "MCX" in str(ex_segment).upper():
            self.state["is_mcx_locked_today"] = True
            session_label = "MCX Commodity (Evening Session)"
        else:
            self.state["is_nse_locked_today"] = True
            session_label = "NSE Equity (Morning Session)"

        self.state["active_trade_id"] = None
        self.state["active_position"] = None
        self._save_state(self.state)

        # Trigger Telegram alert for session cap reached
        try:
            from reporting.telegram_bot import send_telegram_message
            cap_alerts = self.state.get("session_cap_alerted", {})
            cap_key = "MCX_FO" if "MCX" in str(ex_segment).upper() else "NSE_FO"
            if not cap_alerts.get(cap_key, False):
                alert_msg = (
                    f"🔒 <b>[SESSION CAP REACHED]</b>\n"
                    f"========================================\n"
                    f"Max 1 trade executed for <b>{session_label}</b>.\n"
                    f"System locked for the rest of the session ({self.state['date']}).\n"
                    f"========================================"
                )
                send_telegram_message(alert_msg)
                cap_alerts[cap_key] = True
                self.state["session_cap_alerted"] = cap_alerts
                self._save_state(self.state)
        except Exception as alert_err:
            print(f"[State Manager Exit Alert Notice] {alert_err}")

    def get_trades_today_count(self, exchange: str = "NSE_FO") -> int:
        self._check_date_reset()
        segment = "MCX_FO" if ("MCX" in str(exchange).upper()) else "NSE_FO"
        return self.state.get(f"{segment}_trades_today", 0)

    def get_last_trade_pnl(self) -> float:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT net_pnl FROM trades WHERE status = 'CLOSED' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] is not None else 0.0

    def get_today_trades(self, execution_mode: Optional[str] = None) -> List[Dict[str, Any]]:
        today_str = datetime.date.today().isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if execution_mode:
            cursor.execute("SELECT * FROM trades WHERE trade_date = ? AND execution_mode = ?", (today_str, execution_mode))
        else:
            cursor.execute("SELECT * FROM trades WHERE trade_date = ?", (today_str,))
        rows = cursor.fetchall()
        trades = [dict(r) for r in rows]
        conn.close()
        return trades

    def get_todays_trades(self, execution_mode: Optional[str] = None) -> List[Dict[str, Any]]:
        """Alias for get_today_trades."""
        return self.get_today_trades(execution_mode=execution_mode)

    def _check_date_reset(self):
        today_str = datetime.date.today().isoformat()
        if self.state.get("date") != today_str or self.state.get("last_reset_date") != today_str:
            print(f"[State Manager Midnight Reset] Date reset triggered for {today_str}.")
            self.state["date"] = today_str
            self.state["last_reset_date"] = today_str
            self.state["NSE_FO_trades_today"] = 0
            self.state["MCX_FO_trades_today"] = 0
            self.state["is_nse_locked_today"] = False
            self.state["is_mcx_locked_today"] = False
            self.state["session_cap_alerted"] = {"NSE_FO": False, "MCX_FO": False}
            self.state["active_trade_id"] = None
            self.state["active_position"] = None
            self.state["disabled_segments"] = {}
            self._save_state(self.state)
