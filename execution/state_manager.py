import os
import json
import sqlite3
import datetime
from typing import Dict, Any, Optional, List
from config.settings import STATE_FILE_PATH, DB_FILE_PATH, LOGS_DIR, MAX_DAILY_TRADES, INITIAL_WALLET_CAPITAL

class StateManager:
    """
    Persistent state manager using SQLite (logs/trades.db) and state.json to enforce:
    1. HARD CAP of MAX 1 TRADE PER DAY.
    2. Dynamic Real-Time Wallet Balance tracking across winning and losing trades.
    3. Strict separation of LIVE vs DRY_RUN trade audit logs.
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
        
        # Column migration check for existing DBs
        cursor.execute("PRAGMA table_info(trades)")
        columns = [column[1] for column in cursor.fetchall()]
        if "execution_mode" not in columns:
            cursor.execute("ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'DRY_RUN'")
            conn.commit()
            
        conn.close()

    def _default_state(self) -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        return {
            "date": today_str,
            "trades_today_count": 0,
            "is_locked_for_today": False,
            "active_trade_id": None,
            "active_position": None,
            "current_wallet_balance": INITIAL_WALLET_CAPITAL
        }

    def reset_daily_state(self) -> Dict[str, Any]:
        """
        Explicitly reset state for a fresh daily run or test simulation.
        """
        state = self._default_state()
        self._save_state(state)
        print(f"[State Manager] Daily state cleanly reset for {state['date']}. Wallet Balance: Rs {state['current_wallet_balance']:,.2f} INR.")
        return state

    def _load_or_init_state(self) -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, 'r') as f:
                    state = json.load(f)
                    if state.get("date") != today_str:
                        print(f"[State Manager] New trading session detected ({today_str}). Updating daily session state.")
                        state["date"] = today_str
                        state["trades_today_count"] = 0
                        state["is_locked_for_today"] = False
                        state["active_trade_id"] = None
                        state["active_position"] = None
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

    def is_trade_allowed_today(self, override_daily_limit: bool = False) -> bool:
        self._check_date_reset()
        if self.is_drawdown_limit_exceeded():
            return False
        if override_daily_limit:
            print(f"[MANUAL OVERRIDE ACTIVE] Daily {MAX_DAILY_TRADES}-trade cap lockout manually bypassed for today ({self.state['date']}).")
            return True
        if self.state["trades_today_count"] >= MAX_DAILY_TRADES or self.state["is_locked_for_today"]:
            print(f"[State Manager] Trade BLOCKED: Daily trade cap of {MAX_DAILY_TRADES} trade per day reached for {self.state['date']}. (Use --override-daily-limit to bypass).")
            return False
        return True

    def get_current_wallet_balance(self) -> float:
        self._check_date_reset()
        return float(self.state.get("current_wallet_balance", INITIAL_WALLET_CAPITAL))

    def record_entry_trade(self, option_contract: Dict[str, Any], entry_premium: float, target_p: float, stop_p: float, execution_mode: str = "DRY_RUN") -> int:
        self._check_date_reset()
        today_str = datetime.date.today().isoformat()
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                trade_date, entry_time, execution_mode, underlying_symbol, option_symbol, option_type,
                strike_price, quantity, entry_premium, target_price, stop_price, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today_str,
            now_str,
            execution_mode,
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

        self.state["trades_today_count"] += 1
        self.state["is_locked_for_today"] = True  # HARD LOCK AFTER 1 TRADE
        self.state["active_trade_id"] = trade_id
        self.state["active_position"] = {
            "trade_id": trade_id,
            "execution_mode": execution_mode,
            "option_symbol": option_contract["option_symbol"],
            "quantity": option_contract["lot_size"],
            "entry_premium": entry_premium,
            "target_price": target_p,
            "stop_price": stop_p
        }
        self._save_state(self.state)
        print(f"[State Manager] Trade #{trade_id} ({execution_mode}) recorded. DAILY LOCK ACTIVATED (MAX 1 TRADE PER DAY ENFORCED).")
        return trade_id

    def record_exit_trade(self, trade_id: int, exit_premium: float, friction_fees: float = 0.0, exit_reason: str = "EXIT"):
        self._check_date_reset()
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT quantity, entry_premium, execution_mode FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()
        
        if row:
            quantity, entry_premium, mode = row
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
            
            print(f"[State Manager] Trade #{trade_id} ({mode}) CLOSED. Exit Premium: Rs {exit_premium:.2f} | Net PnL: Rs {net_pnl:,.2f} INR | Updated Real-Time Wallet: Rs {new_wallet:,.2f} INR")
            
        conn.close()
        self.state["active_trade_id"] = None
        self.state["active_position"] = None
        self._save_state(self.state)

    def get_trades_today_count(self) -> int:
        self._check_date_reset()
        return self.state.get("trades_today_count", 0)

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
        if self.state.get("date") != today_str:
            self.state["date"] = today_str
            self.state["trades_today_count"] = 0
            self.state["is_locked_for_today"] = False
            self.state["active_trade_id"] = None
            self.state["active_position"] = None
            self._save_state(self.state)
