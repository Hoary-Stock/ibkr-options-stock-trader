"""Configuration constants for IBKR Trader."""

# ── IBKR Connection ──────────────────────────────────────────────────
IBKR_HOST = "127.0.0.1"
IBKR_PAPER_PORT = 7497
IBKR_LIVE_PORT = 7496
IBKR_GW_PAPER_PORT = 4001
IBKR_GW_LIVE_PORT = 4002
IBKR_CLIENT_ID = 10  # Avoid collision with tradebot (clientId=1,2)

# ── Market Data ──────────────────────────────────────────────────────
MARKET_DATA_TYPE = 1  # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen
MAX_SIMULTANEOUS_STREAMS = 95  # IBKR limit ~100, leave headroom

# ── Price Ladder ─────────────────────────────────────────────────────
TICK_SIZE_SMALL = 0.05   # For options priced < $3
TICK_SIZE_LARGE = 0.10   # For options priced >= $3
TICK_THRESHOLD = 3.0     # Price threshold for tick size switch
LADDER_ROWS = 25         # Number of price levels to display

# ── Market Depth ─────────────────────────────────────────────────────
DEPTH_ROWS = 10          # Number of depth levels to request

# ── Account & Refresh ───────────────────────────────────────────────
ACCOUNT_REFRESH_MS = 3000  # Account summary refresh interval

# ── Paper Trading ────────────────────────────────────────────────────
PAPER_STARTING_CAPITAL = 10000.0

# ── Commission (IBKR Pro Fixed) ──────────────────────────────────────
COMMISSION_PER_CONTRACT = 0.65  # USD per contract per side
COMMISSION_MIN = 1.00           # Minimum per order

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
IGNORED_ERROR_CODES = {
    2100, 2103, 2104, 2105, 2106, 2107, 2108, 2119,
    2150, 2157, 2158, 2168, 2169,
    10167, 10090, 10089, 10168,
}

# ── Index Symbols (secType=IND, not STK) ────────────────────────────
INDEX_SYMBOLS = {"SPX", "NDX", "RUT", "VIX", "DJX"}

# ── Default Symbols ──────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["SPY", "SPX", "QQQ", "IWM", "AAPL", "TSLA", "NVDA", "AMZN", "META"]

# ── Option Chain ─────────────────────────────────────────────────────
MAX_EXPIRY_TABS_PER_RANGE = 10  # Show at most 10 expiries per range filter
