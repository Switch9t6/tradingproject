import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Upstox API Credentials & Authentication Settings
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/callback")
OAUTH_PORT = int(os.getenv("OAUTH_PORT", 5000))

UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
UPSTOX_API_BASE_URL = "https://api.upstox.com/v2"

# Security & Read-Only Capital Constraints
INITIAL_WALLET_CAPITAL = 257.48           # Actual Upstox Live Wallet Balance Baseline
MICRO_CAPITAL_BUDGET_CAP = 250.0          # Micro-Capital Live Test Budget Cap <= INR 250.00 (UNTOUCHED)
# Note: Standard single-lot premium budget is 100% dynamically sized based on actual available wallet balance.

# Strict Daily Guardrails
MAX_DAILY_TRADES = 1                      # HARD CAP: MAX 1 TRADE PER DAY
TAKE_PROFIT_PCT = 0.25                    # Target: +25% on Option Premium
STOP_LOSS_PCT = 0.12                      # Base Stop Loss: -12% on Option Premium

# Step-Based Trailing Stop-Loss Rules (Breakeven at +10%, Lock +10% at +18%)
USE_STEP_TRAILING_STOP_LOSS = True
TSL_STEP1_TRIGGER_PCT = 0.10              # +10% Peak Gain -> Raise SL to Breakeven (0%)
TSL_STEP1_LOCK_PCT = 0.00                 # Breakeven (0% gain)
TSL_STEP2_TRIGGER_PCT = 0.18              # +18% Peak Gain -> Lock +10% Profit
TSL_STEP2_LOCK_PCT = 0.10                 # Lock +10% Profit

# 30-Minute Time-Decay Stagnation Exit Rule
MAX_HOLD_SECONDS = 1800                   # 30 Minutes
MIN_GAIN_REQUIRED_AT_30M = 1.05           # Must be up at least +5% after 30 mins to keep holding

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

TARGET_DELTA_MIN = 0.50
TARGET_DELTA_MAX = 0.55
MAX_BID_ASK_SPREAD_PCT = 0.015            # Reject contract if Bid-Ask Spread > 1.5%

PRIME_WINDOW_1_START = "09:30"
PRIME_WINDOW_1_END = "11:15"
PRIME_WINDOW_2_START = "13:30"
PRIME_WINDOW_2_END = "14:30"

MIN_INDIA_VIX = 11.0                      # Reject if VIX < 11 (too flat)
MAX_INDIA_VIX = 24.0                      # Reject if VIX > 24 (extreme event risk)
CONSECUTIVE_LOSS_SCALING_PCT = 0.80       # Scale budget down to 80% after a loss

MIN_VOLUME_SPIKE_RATIO = 3.0
MIN_PRICE_MOMENTUM_PCT = 0.012

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
STATE_FILE_PATH = os.path.join(PERSISTENT_BASE_DIR, "state.json")
DB_FILE_PATH = os.path.join(LOGS_DIR, "trades.db")
