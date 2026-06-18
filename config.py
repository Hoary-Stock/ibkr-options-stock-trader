"""Configuration constants for IBKR Trader."""

# ── IBKR Connection ──────────────────────────────────────────────────
IBKR_HOST = "127.0.0.1"
IBKR_PAPER_PORT = 7497
IBKR_LIVE_PORT = 7496
IBKR_GW_PAPER_PORT = 4001
IBKR_GW_LIVE_PORT = 4002
IBKR_CLIENT_ID = 10        # Options GUI (avoid collision with tradebot=1,2)
IBKR_STOCK_CLIENT_ID = 11  # Stock trader client (stock_trader.py)

# ── Market Data ──────────────────────────────────────────────────────
MARKET_DATA_TYPE = 1  # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen
MAX_SIMULTANEOUS_STREAMS = 95  # IBKR limit ~100, leave headroom

# ── Price Ladder ─────────────────────────────────────────────────────
# Penny Pilot (SPY, QQQ, IWM, AAPL, TSLA, NVDA, AMZN, etc.)
TICK_SIZE_SMALL = 0.01   # For options priced < $3
TICK_SIZE_LARGE = 0.05   # For options priced >= $3
TICK_THRESHOLD = 3.0     # Price threshold for tick size switch
LADDER_ROWS = 201        # Price levels (±100 from center; $2.00 at $0.01 tick)

# Non-Penny-Pilot overrides (index options like SPX)
TICK_SIZE_OVERRIDES = {
    "SPX":  (0.05, 0.10),   # SPX: $0.05 < $3, $0.10 >= $3
    "XSP":  (0.05, 0.10),
    "NDX":  (0.05, 0.10),
    "RUT":  (0.05, 0.10),
}

# ── Market Depth ─────────────────────────────────────────────────────
DEPTH_ROWS = 10          # Number of depth levels to request

# ── Account & Refresh ───────────────────────────────────────────────
ACCOUNT_REFRESH_MS = 3000  # Account summary refresh interval

# ── Paper Trading ────────────────────────────────────────────────────
PAPER_STARTING_CAPITAL = 10000.0

# ── Commission (IBKR Pro Fixed) ──────────────────────────────────────
COMMISSION_PER_CONTRACT = 0.65  # USD per contract per side (options)
COMMISSION_MIN = 1.00           # Minimum per order
STOCK_COMMISSION_PER_SHARE = 0.005  # USD per share (stocks, IBKR Pro Fixed)
STOCK_COMMISSION_MIN = 1.00         # Minimum per stock order

# ── Colors (Dark Theme) ─────────────────────────────────────────────
COLOR_BG = "#1a1a2e"
COLOR_BG_DARK = "#16213e"
COLOR_BG_PANEL = "#0f3460"
COLOR_TEXT = "#e0e0e0"
COLOR_TEXT_DIM = "#888888"
COLOR_GREEN = "#00c853"
COLOR_RED = "#ff1744"
COLOR_BUY = "#00c853"
COLOR_SELL = "#ff1744"
COLOR_BID_HIGHLIGHT = "#004d40"    # Teal for bid level
COLOR_ASK_HIGHLIGHT = "#4a4000"    # Dark yellow for ask level
COLOR_ATM_HIGHLIGHT = "#1a237e"    # Deep blue for ATM strike
COLOR_ACCENT = "#00bcd4"
COLOR_BORDER = "#333355"
COLOR_BUTTON_DISABLED = "#404040"
COLOR_PROFIT = "#00c853"
COLOR_LOSS = "#ff1744"

# ── Depth Bar Colors ────────────────────────────────────────────────
COLOR_DEPTH_BID = "#1a472a"      # Green tint for bid depth bars
COLOR_DEPTH_ASK = "#4a1a1a"      # Red tint for ask depth bars
COLOR_MY_ORDER = "#ffab00"       # Amber for my orders at price level

# ── Forex ────────────────────────────────────────────────────────────
FOREX_PAIRS = [
    ("USD", "HKD"),
    ("USD", "CNH"),
    ("USD", "EUR"),
    ("USD", "GBP"),
    ("USD", "JPY"),
]

# ── Ignored IBKR Error Codes ────────────────────────────────────────
# Only truly harmless informational codes.
# Data-connection codes (2100, 2103-2108) are handled specially in
# IBKRApp.error() — they get logged and surfaced to the GUI.
# 10167 is also handled separately (one-time delayed-data warning).
IGNORED_ERROR_CODES = {
    2119,                          # Market data farm connection restored (info)
    2150, 2157, 2158, 2168, 2169,  # Account / permission info
    10090, 10089, 10168,           # Market data subscription info
    2176,  # Fractional share size trimmed (ibapi 9.81 < server v163) —
           # harmless: only fractional volume decimals are dropped
}

# Data-connection error codes — surfaced as warnings, not silenced
DATA_CONNECTION_ERROR_CODES = {
    2100,  # API client has been unsubscribed from account data
    2103,  # Market data farm connection is broken
    2104,  # Market data farm connection is OK (recovery)
    2105,  # HMDS data farm connection is broken
    2106,  # HMDS data farm connection is OK (recovery)
    2107,  # HMDS data farm connection is inactive
    2108,  # Market data farm connection is inactive
}

# ── Option Pricing (Black-Scholes 理论价计算器) ──────────────────────
RISK_FREE_RATE = 0.045        # 无风险年利率 (≈美国短债); 计算器默认值, 可在界面调整
DIVIDEND_YIELD = 0.0          # 标的连续股息率 (SPY/SPX 用 0 即可)
OPTION_MARKET_CLOSE_ET = 16   # 期权到期日收盘小时 (ET); 用于计算剩余时间 T
CALCULATOR_REFRESH_MS = 700   # 计算器随实时行情/时间衰减刷新间隔

# ── Index Symbols (secType=IND, not STK) ────────────────────────────
INDEX_SYMBOLS = {"SPX", "XSP", "NDX", "RUT", "VIX", "DJX"}

# ── Default Symbols ──────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["SPY", "SPX", "QQQ", "IWM", "AAPL", "TSLA", "NVDA", "AMZN", "META"]

# ── Option Chain ─────────────────────────────────────────────────────
MAX_EXPIRY_TABS_PER_RANGE = 10  # Show at most 10 expiries per range filter

# ── SPX Options Trading Sessions (all times ET) ───────────────────
# GTH = Global Trading Hours (夜盘/盘前): 20:15 → 09:15 next day
# RTH = Regular Trading Hours (正常盘): 09:30 → 16:15
# Curb = After-hours (盘后): 16:15 → 17:00 (limited)
# Note: SPY options are RTH only; SPX/SPXW support GTH+RTH
SPX_SESSION_GTH_START = (20, 15)  # 8:15 PM ET
SPX_SESSION_GTH_END = (9, 15)    # 9:15 AM ET
SPX_SESSION_RTH_START = (9, 30)   # 9:30 AM ET
SPX_SESSION_RTH_END = (16, 15)    # 4:15 PM ET

# Symbols that support extended hours (GTH) trading
EXTENDED_HOURS_SYMBOLS = {"SPX"}

# ── Chart (K-Line) ─────────────────────────────────────────────────
# (display_name, ibkr_bar_size, duration, keep_up_to_date)
CHART_TIMEFRAMES = {
    "1秒":   ("1 secs",  "1800 S", False),
    "5秒":   ("5 secs",  "3600 S", False),
    "15秒":  ("15 secs", "7200 S", False),
    "30秒":  ("30 secs", "14400 S", False),
    "1分钟": ("1 min",   "1 D",    True),
    "5分钟": ("5 mins",  "1 W",    True),
    "15分钟":("15 mins", "2 W",    True),
    "30分钟":("30 mins", "1 M",    True),
    "1小时": ("1 hour",  "1 M",    True),
    "2小时": ("2 hours", "1 M",    True),
    "4小时": ("4 hours", "1 M",    True),
    "日线":  ("1 day",   "1 Y",    True),
    "周线":  ("1 week",  "5 Y",    False),
    "月线":  ("1 month", "10 Y",   False),
}

CHART_COLOR_CANDLE_UP = "#00c853"
CHART_COLOR_CANDLE_DOWN = "#ff1744"
CHART_COLOR_MA5 = "#ffeb3b"
CHART_COLOR_MA20 = "#ff9800"
CHART_COLOR_MA50 = "#e040fb"
CHART_COLOR_MA200 = "#00bcd4"
CHART_COLOR_VWAP = "#ffffff"
CHART_COLOR_VOLUME_UP = "#1b5e20"
CHART_COLOR_VOLUME_DOWN = "#b71c1c"
CHART_COLOR_BG = "#0d0d1a"
CHART_COLOR_CROSSHAIR = "#888888"
