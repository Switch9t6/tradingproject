import os
from dotenv import load_dotenv

# Load environment variables from .env file (override stale shell vars)
load_dotenv(override=True)

# Upstox API v2 Credentials & Configuration
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "https://localhost")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")

# Upstox Automated TOTP Headless Login Credentials
UPSTOX_USERNAME = os.getenv("UPSTOX_USERNAME", "")
UPSTOX_PIN_CODE = os.getenv("UPSTOX_PIN_CODE", "")
UPSTOX_TOTP_SECRET = os.getenv("UPSTOX_TOTP_SECRET", "")

# Upstox API Base Endpoint
UPSTOX_API_BASE_URL = "https://api.upstox.com/v2"

# Fyers API v3 Credentials & Configuration (Zero Static IP Restrictions)
FYERS_APP_ID = os.getenv("FYERS_APP_ID", os.getenv("FYERS_CLIENT_ID", ""))
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "")
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/default/ui/middleware")
FYERS_USERNAME = os.getenv("FYERS_USERNAME", "")
FYERS_PIN_CODE = os.getenv("FYERS_PIN_CODE", "")
FYERS_TOTP_SECRET = os.getenv("FYERS_TOTP_SECRET", "")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "")
FYERS_API_BASE_URL = "https://api-t1.fyers.in/api/v3"

# Security & Capital Constraints
INITIAL_WALLET_CAPITAL = 10000.0          # Base Initial Capital for Dry Run (INR 10,000.00)
DRY_RUN_INITIAL_WALLET_CAPITAL = 10000.0  # Rs 10,000 INR Capital Base for Dry Run

# Per-segment micro-capital caps (micro sizing mode). NSE single-lot budget is
# tighter (Rs 1,000) than MCX Crude (Rs 3,500) because MCX standard lot = 100 bbl
# and its premium base is larger.
MICRO_CAPITAL_BUDGET_CAP_NSE = 1000.0     # NSE micro cap <= INR 1,000.00
MICRO_CAPITAL_BUDGET_CAP_MCX = 3500.0     # MCX micro cap <= INR 3,500.00
# Backward-compatible alias (defaults to the NSE micro cap).
MICRO_CAPITAL_BUDGET_CAP = MICRO_CAPITAL_BUDGET_CAP_NSE

# Strict Guardrails
MAX_DAILY_TRADES = 1                      # HARD CAP FOR LIVE PRODUCTION: MAX 1 TRADE PER DAY
DRY_RUN_MAX_TRADES_PER_SESSION = 5        # DRY RUN CAP: MAX 5 TRADES PER SESSION (NSE & MCX)
TAKE_PROFIT_PCT = 0.25                    # Target: +25% on Option Premium
STOP_LOSS_PCT = 0.12                      # Base Stop Loss: -12% on Option Premium

# Step-Based Trailing Stop-Loss Rules (Breakeven at +8%, Lock +10% at +15%)
USE_STEP_TRAILING_STOP_LOSS = True
TSL_STEP1_TRIGGER_PCT = 0.08              # +8% Peak Gain -> Raise SL to Breakeven (+1%)
TSL_STEP1_LOCK_PCT = 0.01                 # Lock +1% Profit (Breakeven + fee buffer)
TSL_STEP2_TRIGGER_PCT = 0.15              # +15% Peak Gain -> Lock +10% Profit
TSL_STEP2_LOCK_PCT = 0.10                 # Lock +10% Profit

# 20-Minute Time-Decay Stagnation Exit Rule (Optimized to reduce drawdown from time decay)
MAX_HOLD_SECONDS = 1200                   # 20 Minutes (Optimized from 30 mins)
MIN_GAIN_REQUIRED_AT_30M = 1.05           # Must be up at least +5% after 20 mins to keep holding

QUALIFICATION_SCORE_THRESHOLD = 80.0      # Optimized Composite Score Threshold (80/100 Pts)
MIN_MICRO_PREMIUM_INR = 30.0              # Minimum option premium to keep friction < 4%

# Full Liquid NSE Options (FnO) Universe (Indices + 60 Liquid FnO Equities)
SCANNER_UNIVERSE = [
    {"symbol": "NIFTY", "instrument_key": "NSE_INDEX|Nifty 50", "type": "INDEX", "strike_step": 50.0, "lot_size": 25},
    {"symbol": "BANKNIFTY", "instrument_key": "NSE_INDEX|Nifty Bank", "type": "INDEX", "strike_step": 100.0, "lot_size": 15},
    {"symbol": "FINNIFTY", "instrument_key": "NSE_INDEX|Nifty Fin Service", "type": "INDEX", "strike_step": 50.0, "lot_size": 25},
    {"symbol": "MIDCPNIFTY", "instrument_key": "NSE_INDEX|NIFTY MID SELECT", "type": "INDEX", "strike_step": 25.0, "lot_size": 50},
    {"symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018", "type": "EQUITY", "strike_step": 20.0, "lot_size": 250},
    {"symbol": "HDFCBANK", "instrument_key": "NSE_EQ|INE040A01034", "type": "EQUITY", "strike_step": 10.0, "lot_size": 550},
    {"symbol": "ICICIBANK", "instrument_key": "NSE_EQ|INE090A01021", "type": "EQUITY", "strike_step": 10.0, "lot_size": 700},
    {"symbol": "SBIN", "instrument_key": "NSE_EQ|INE062A01020", "type": "EQUITY", "strike_step": 5.0, "lot_size": 750},
    {"symbol": "INFY", "instrument_key": "NSE_EQ|INE009A01021", "type": "EQUITY", "strike_step": 20.0, "lot_size": 400},
    {"symbol": "TATAMOTORS", "instrument_key": "NSE_EQ|INE155A01022", "type": "EQUITY", "strike_step": 10.0, "lot_size": 1425},
    {"symbol": "AXISBANK", "instrument_key": "NSE_EQ|INE238A01034", "type": "EQUITY", "strike_step": 10.0, "lot_size": 625},
    {"symbol": "BHARTIARTL", "instrument_key": "NSE_EQ|INE397D01024", "type": "EQUITY", "strike_step": 10.0, "lot_size": 950},
    {"symbol": "BANKBARODA", "instrument_key": "NSE_EQ|INE028A01039", "type": "EQUITY", "strike_step": 2.5, "lot_size": 2925},
    {"symbol": "TATASTEEL", "instrument_key": "NSE_EQ|INE081A01020", "type": "EQUITY", "strike_step": 2.5, "lot_size": 5500},
    {"symbol": "BAJFINANCE", "instrument_key": "NSE_EQ|INE296A01024", "type": "EQUITY", "strike_step": 50.0, "lot_size": 125},
    {"symbol": "BAJAJFINSV", "instrument_key": "NSE_EQ|INE918I01026", "type": "EQUITY", "strike_step": 10.0, "lot_size": 500},
    {"symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029", "type": "EQUITY", "strike_step": 50.0, "lot_size": 175},
    {"symbol": "LT", "instrument_key": "NSE_EQ|INE018A01030", "type": "EQUITY", "strike_step": 25.0, "lot_size": 300},
    {"symbol": "KOTAKBANK", "instrument_key": "NSE_EQ|INE237A01028", "type": "EQUITY", "strike_step": 20.0, "lot_size": 400},
    {"symbol": "MARUTI", "instrument_key": "NSE_EQ|INE585B01010", "type": "EQUITY", "strike_step": 100.0, "lot_size": 100},
    {"symbol": "SUNPHARMA", "instrument_key": "NSE_EQ|INE044A01036", "type": "EQUITY", "strike_step": 20.0, "lot_size": 350},
    {"symbol": "TITAN", "instrument_key": "NSE_EQ|INE280A01028", "type": "EQUITY", "strike_step": 50.0, "lot_size": 175},
    {"symbol": "ULTRACEMCO", "instrument_key": "NSE_EQ|INE481G01011", "type": "EQUITY", "strike_step": 100.0, "lot_size": 100},
    {"symbol": "WIPRO", "instrument_key": "NSE_EQ|INE075A01022", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1500},
    {"symbol": "M&M", "instrument_key": "NSE_EQ|INE101A01026", "type": "EQUITY", "strike_step": 20.0, "lot_size": 350},
    {"symbol": "NTPC", "instrument_key": "NSE_EQ|INE733E01010", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1500},
    {"symbol": "ONGC", "instrument_key": "NSE_EQ|INE213A01029", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1875},
    {"symbol": "POWERGRID", "instrument_key": "NSE_EQ|INE752E01010", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1800},
    {"symbol": "JSWSTEEL", "instrument_key": "NSE_EQ|INE019A01038", "type": "EQUITY", "strike_step": 10.0, "lot_size": 675},
    {"symbol": "HINDALCO", "instrument_key": "NSE_EQ|INE038A01020", "type": "EQUITY", "strike_step": 10.0, "lot_size": 1400},
    {"symbol": "COALINDIA", "instrument_key": "NSE_EQ|INE522F01014", "type": "EQUITY", "strike_step": 5.0, "lot_size": 2100},
    {"symbol": "ADANIENT", "instrument_key": "NSE_EQ|INE423A01024", "type": "EQUITY", "strike_step": 20.0, "lot_size": 300},
    {"symbol": "ADANIPORTS", "instrument_key": "NSE_EQ|INE742F01042", "type": "EQUITY", "strike_step": 10.0, "lot_size": 800},
    {"symbol": "ASIANPAINT", "instrument_key": "NSE_EQ|INE021A01026", "type": "EQUITY", "strike_step": 20.0, "lot_size": 200},
    {"symbol": "HEROMOTOCO", "instrument_key": "NSE_EQ|INE158A01026", "type": "EQUITY", "strike_step": 50.0, "lot_size": 150},
    {"symbol": "EICHERMOT", "instrument_key": "NSE_EQ|INE066A01021", "type": "EQUITY", "strike_step": 50.0, "lot_size": 175},
    {"symbol": "GRASIM", "instrument_key": "NSE_EQ|INE047A01021", "type": "EQUITY", "strike_step": 20.0, "lot_size": 250},
    {"symbol": "INDUSINDBK", "instrument_key": "NSE_EQ|INE095A01012", "type": "EQUITY", "strike_step": 20.0, "lot_size": 500},
    {"symbol": "BPCL", "instrument_key": "NSE_EQ|INE029A01011", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1800},
    {"symbol": "IOC", "instrument_key": "NSE_EQ|INE242A01010", "type": "EQUITY", "strike_step": 2.5, "lot_size": 4875},
    {"symbol": "TECHM", "instrument_key": "NSE_EQ|INE669C01036", "type": "EQUITY", "strike_step": 20.0, "lot_size": 600},
    {"symbol": "HCLTECH", "instrument_key": "NSE_EQ|INE860A01027", "type": "EQUITY", "strike_step": 20.0, "lot_size": 350},
    {"symbol": "DIVISLAB", "instrument_key": "NSE_EQ|INE361B01024", "type": "EQUITY", "strike_step": 50.0, "lot_size": 150},
    {"symbol": "CIPLA", "instrument_key": "NSE_EQ|INE059A01026", "type": "EQUITY", "strike_step": 10.0, "lot_size": 650},
    {"symbol": "DRREDDY", "instrument_key": "NSE_EQ|INE089A01023", "type": "EQUITY", "strike_step": 50.0, "lot_size": 125},
    {"symbol": "BRITANNIA", "instrument_key": "NSE_EQ|INE216A01030", "type": "EQUITY", "strike_step": 50.0, "lot_size": 200},
    {"symbol": "NESTLEIND", "instrument_key": "NSE_EQ|INE239A01016", "type": "EQUITY", "strike_step": 20.0, "lot_size": 250},
    {"symbol": "HINDUNILVR", "instrument_key": "NSE_EQ|INE030A01027", "type": "EQUITY", "strike_step": 20.0, "lot_size": 300},
    {"symbol": "ITC", "instrument_key": "NSE_EQ|INE154A01025", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1600},
    {"symbol": "DLF", "instrument_key": "NSE_EQ|INE271C01023", "type": "EQUITY", "strike_step": 10.0, "lot_size": 825},
    {"symbol": "LTIM", "instrument_key": "NSE_EQ|INE214T01019", "type": "EQUITY", "strike_step": 50.0, "lot_size": 150},
    {"symbol": "BEL", "instrument_key": "NSE_EQ|INE263A01024", "type": "EQUITY", "strike_step": 2.5, "lot_size": 2850},
    {"symbol": "HAL", "instrument_key": "NSE_EQ|INE066F01020", "type": "EQUITY", "strike_step": 50.0, "lot_size": 150},
    {"symbol": "TATACHEM", "instrument_key": "NSE_EQ|INE092A01019", "type": "EQUITY", "strike_step": 10.0, "lot_size": 1000},
    {"symbol": "TATAPOWER", "instrument_key": "NSE_EQ|INE245A01021", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1850},
    {"symbol": "VEDL", "instrument_key": "NSE_EQ|INE205A01025", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1150},
    {"symbol": "GAIL", "instrument_key": "NSE_EQ|INE129A01019", "type": "EQUITY", "strike_step": 2.5, "lot_size": 3225},
    {"symbol": "CHOLAFIN", "instrument_key": "NSE_EQ|INE121A01024", "type": "EQUITY", "strike_step": 10.0, "lot_size": 625},
    {"symbol": "PFC", "instrument_key": "NSE_EQ|INE134E01011", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1500},
    {"symbol": "RECLTD", "instrument_key": "NSE_EQ|INE020B01018", "type": "EQUITY", "strike_step": 5.0, "lot_size": 1500}
]

# Sideways Market Prevention Settings
USE_SIDEWAYS_MARKET_FILTER = True
MIN_ADX_TREND_STRENGTH = 25.0             # ADX >= 25 required for strong trending market
MAX_CHOPPINESS_INDEX = 50.0              # Choppiness Index < 50 required (rejects choppy consolidation)
MIN_RELATIVE_ATR_PCT = 0.0040             # Relative ATR >= 0.4% required

# Professional Architectural Upgrades
USE_RS_RW_FILTER = True
MIN_RS_THRESHOLD = 0.0030                 # Stock must outperform NIFTY by >= +0.3% (CE) or underperform by <= -0.3% (PE)

LIMIT_ORDER_BUFFER_PCT = 0.005            # 0.5% Aggressive limit order buffer
LIMIT_ORDER_TIMEOUT_SECONDS = 5           # 5-second fill timeout

# Option Selection Guardrails (real-data gates are enforced only when live
# Fyers option-chain/quotes data is available; estimation mode never fakes a gate)
TARGET_DELTA_MIN = 0.50
TARGET_DELTA_MAX = 0.55
MAX_BID_ASK_SPREAD_PCT = 0.015            # Reject contract if Bid-Ask Spread > 1.5%

# Momentum-based OTM strike selection (option_mapper.py): the engine snaps spot to
# the nearest strike (half-up rounding) then moves the strike OTM by steps based on
# momentum strength, so the option is cheaper and more leveraged while staying
# inside the delta band below.
MOMENTUM_MODERATE_PCT = 1.0    # |momentum| >= 1%  -> 1 strike OTM
MOMENTUM_STRONG_PCT   = 2.0    # |momentum| >= 2%  -> 2 strikes OTM
MAX_STRIKE_OFFSET     = 3      # hard cap: never more than 3 strikes OTM
# Strict fallback guard: when the exact strike is missing from the master cache,
# the nearest-available-strike fallback is ONLY used if its deviation from the
# target strike is within this many strike steps. Otherwise the trade is SKIPPED
# (STRIKE_OUT_OF_BOUNDS_OR_MISSING) - never trade a wrong/deep ITM strike from a
# stale cache. For MCX Crude (50-pt steps) 2 steps = 100 points.
MAX_STRIKE_DEVIATION_STEPS = 2
# Dynamic wallet allocation: per-trade usable budget derived from LIVE available
# broker cash. Never commit more than this fraction of usable cash, and reserve
# an extra buffer for order slippage/charges.
MAX_ALLOCATION_PCT = 0.80     # use at most 80% of available wallet cash
SLIPPAGE_BUFFER_PCT = 0.02    # reserve 2% for slippage + brokerage/STT
# Real-data delta band for the selected (ATM-to-OTM) strike. Enforced ONLY when
# live option-chain greeks are returned by Fyers; absent greeks -> estimate mode.
OPTION_DELTA_MIN = 0.25
OPTION_DELTA_MAX = 0.70
# Real open-interest liquidity filter. OI is read from live option-chain data;
# it only rejects a contract when this flag is True AND real OI is available.
ENABLE_OI_FILTER = False
NSE_MIN_OPTION_OPEN_INTEREST = 100000
MCX_MIN_OPTION_OPEN_INTEREST = 1000

PRIME_WINDOW_1_START = "09:30"
PRIME_WINDOW_1_END = "11:15"
PRIME_WINDOW_2_START = "13:30"
PRIME_WINDOW_2_END = "14:30"

MIN_INDIA_VIX = 11.0                      # Reject if VIX < 11 (too flat)
MAX_INDIA_VIX = 24.0                      # Reject if VIX > 24 (extreme event risk)
CONSECUTIVE_LOSS_SCALING_PCT = 0.80       # Scale budget down to 80% after a loss

MIN_VOLUME_SPIKE_RATIO = 3.0
MIN_PRICE_MOMENTUM_PCT = 0.012

# MCX Crude Oil Commodity Options Settings
MCX_CRUDE_SYMBOL = "CRUDEOIL"
MCX_CRUDE_INSTRUMENT_KEY = "MCX_FO|CRUDEOIL"
MCX_CRUDE_LOT_SIZE = 100                      # Standard Lot = 100 Barrels
MCX_CRUDE_MINI_LOT_SIZE = 10                  # Mini Lot (CRUDEOILM) = 10 Barrels
MCX_CRUDE_STRIKE_STEP = 50.0                  # Strike interval (50 points)
MCX_CRUDE_MIN_ATR = 15.0                      # Volatility Gate: ATR(14) > 15 points required

# Dual-Session Scheduling Windows (IST)
NSE_SESSION_START = "09:00"                   # Session 1: NSE Equities (09:00 AM - 03:30 PM IST)
NSE_SESSION_END = "15:30"
MCX_SESSION_START = "17:00"                   # Session 2: MCX Commodities (05:00 PM - 11:15 PM IST)
MCX_SESSION_END = "23:15"
MCX_PRIME_WINDOW_START = "17:00"
MCX_PRIME_WINDOW_END = "23:00"
MCX_SQUARE_OFF_SCHEDULE_TIME = "23:00"        # 11:00 PM IST MCX Hard EOD Square-Off

# Scheduler Trigger Times & Windows (IST) - auto_scheduler.py + session_runner.py
MORNING_SCAN_TIME = "09:15"                   # Session 1: NSE Morning Session scan trigger
EVENING_SCAN_TIME = "17:00"                   # Session 2: MCX Evening Session scan trigger
MORNING_SESSION_WINDOW = ("09:15", "15:30")   # Session 1: NSE window (matches NSE_SESSION_END)
EVENING_SESSION_WINDOW = ("17:00", "23:15")   # Session 2: MCX window (matches MCX_SESSION_END)

# MANUAL-ONLY OPERATION FLAG: when False (default), automated scheduled session
# triggers (auto_scheduler.py + the Railway daemon auto-scan loop) are PAUSED.
# Sessions then start ONLY via manual Telegram commands (/start, /resume), and each
# live entry still requires interactive Telegram approval before order placement.
# Set ENABLE_AUTO_SCHEDULER=True to re-enable fully automated scheduled entries.
ENABLE_AUTO_SCHEDULER: bool = False

# Commodity News & EIA Inventory Blackout Settings
EIA_BLACKOUT_WINDOW_MINUTES = 15              # Block trading 15 mins before & after 08:00 PM IST Wednesdays
EIA_INVENTORY_DAY_OF_WEEK = 2                 # 2 = Wednesday (0 = Monday)
EIA_RELEASE_TIME_IST = "20:00"                # 08:00 PM IST US EIA Weekly Inventory Report

# Daily Workflow Schedule (IST)
OAUTH_SCHEDULE_TIME = "09:00"
SCAN_EXEC_SCHEDULE_TIME = "09:30"
SQUARE_OFF_SCHEDULE_TIME = "15:15"
EOD_REPORT_SCHEDULE_TIME = "15:30"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Detect Railway Persistent Storage Volume Mount (/data)
RAILWAY_DATA_DIR = "/data" if os.path.exists("/data") else os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
PERSISTENT_BASE_DIR = RAILWAY_DATA_DIR if RAILWAY_DATA_DIR and os.path.exists(RAILWAY_DATA_DIR) else BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data_cache")
LOGS_DIR = os.path.join(PERSISTENT_BASE_DIR, "logs")
REPORTS_DIR = os.path.join(PERSISTENT_BASE_DIR, "reports")
TOKEN_FILE_PATH = os.path.join(PERSISTENT_BASE_DIR, "access_token.json")
FYERS_TOKEN_FILE_PATH = os.path.join(PERSISTENT_BASE_DIR, "fyers_access_token.json")
STATE_FILE_PATH = os.path.join(PERSISTENT_BASE_DIR, "state.json")
DB_FILE_PATH = os.path.join(LOGS_DIR, "trades.db")

# Kill-switch + segment-disable markers (persist across redeploys when /data volume is mounted)
BOT_DISABLED_FLAG = os.path.join(PERSISTENT_BASE_DIR, "BOT_DISABLED.flag")
SEGMENT_DISABLED_FLAG_PATTERN = os.path.join(PERSISTENT_BASE_DIR, "segment_disabled_{segment}.flag")
