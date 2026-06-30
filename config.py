"""Configuration constants for IBKR Trader."""

import os

# ── IBKR Connection ──────────────────────────────────────────────────
IBKR_HOST = "127.0.0.1"
IBKR_PAPER_PORT = 7497
IBKR_LIVE_PORT = 7496
IBKR_GW_LIVE_PORT = 4001   # IB Gateway 默认: 实盘
IBKR_GW_PAPER_PORT = 4002  # IB Gateway 默认: 模拟盘
# 注: 以上为 IB Gateway 出厂默认端口; 实际以 Gateway → Configure → Settings
# → API → Socket Port 里的设置为准, 不一致时改这里或改 Gateway 设置。
IBKR_CLIENT_ID = 10        # Options GUI (avoid collision with tradebot=1,2)
IBKR_STOCK_CLIENT_ID = 11  # Stock trader client (stock_trader.py)
IBKR_COMBO_CLIENT_ID = 12  # Combo analyzer (combo_analyzer.py)
IBKR_MACRO_CLIENT_ID = 13  # Macro monitor (macro_monitor.py): 美债利率/原油/金银, 只读行情

# ── 新版本开关 (Gateway + 更轻的行情订阅) ────────────────────────────
# 设环境变量 IBKR_USE_GATEWAY=1 → 连 IB Gateway (端口 4001/4002) 而非 TWS
# (7496/7497), 并收紧行情订阅占用, 让期权 GUI + 正股 GUI 同开时不超过
# ~100 行账户上限。旧启动脚本 (start.bat) 不设此变量 → 行为与之前完全一致。
# 新启动脚本 start_gateway.bat 通过独立入口 main_gw.py 设置该变量。
USE_GATEWAY = os.environ.get("IBKR_USE_GATEWAY", "0") == "1"

# ── Market Data ──────────────────────────────────────────────────────
MARKET_DATA_TYPE = 1  # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed-Frozen
# 单组件 (option_chain 每个到期日 tab) 同时订阅的行情上限。
# 新版 (Gateway) 调小, 让期权+正股两个 GUI 合计 <100 行账户上限。
MAX_SIMULTANEOUS_STREAMS = 45 if USE_GATEWAY else 95  # IBKR limit ~100
# 期权链显示/订阅的 ATM 上下行权价档数 (±N); 新版调小减少行情占用。
CHAIN_STRIKES_AROUND_ATM = 10 if USE_GATEWAY else 15

# ── Price Ladder ─────────────────────────────────────────────────────
# Penny Pilot (SPY, QQQ, IWM, AAPL, TSLA, NVDA, AMZN, etc.)
TICK_SIZE_SMALL = 0.01   # For options priced < $3
TICK_SIZE_LARGE = 0.05   # For options priced >= $3
TICK_THRESHOLD = 3.0     # Price threshold for tick size switch
LADDER_ROWS = 201        # Price levels (±100 from center; $2.00 at $0.01 tick)
LADDER_ROW_HEIGHT = 26   # 每个价格档行高 (须与 PriceLadderRow 固定高度一致)
LADDER_EXTEND_CHUNK = 40 # 滚轮滚到顶/底边缘时, 一次向该方向追加的档位数
LADDER_MAX_ROWS = 1600   # 点价梯最多档位数 (防止反复滚动无限扩展占内存)

# Non-Penny-Pilot overrides (index options like SPX)
TICK_SIZE_OVERRIDES = {
    "SPX":  (0.05, 0.10),   # SPX: $0.05 < $3, $0.10 >= $3
    "XSP":  (0.01, 0.01),   # XSP(Mini-SPX): 全系列统一 $0.01 (CBOE 实测最小跳动)
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
FUTURES_COMMISSION_PER_CONTRACT = 0.85  # USD per contract per side (≈IBKR 期货, 仅用于本地显示)
FUTURES_COMMISSION_MIN = 0.85           # Minimum per futures order

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
CALCULATOR_REFRESH_MS = 300   # 计算器随实时行情/时间衰减刷新间隔 (越小越实时)

# ── Index Symbols (secType=IND, not STK) ────────────────────────────
# 含 CBOE 美债收益率指数 (IRX 13周 / FVX 5年 / TNX 10年 / TYX 30年): 计算器右下角
# 利率行用 FVX/TNX 订阅实时收益率。指数值口径: TNX/FVX/TYX = 收益率×10 (显示需 ×0.1),
# IRX ≈ 收益率 (×1.0) —— 换算在 OptionCalculator._RATE_SYMBOLS 里按 scale 处理。
# 注: 2 年期短端已改走 Yahoo `2YY=F` (延迟), 不再用 IRX; IRX/TYX 仍留作 IND 类型注册。
INDEX_SYMBOLS = {"SPX", "XSP", "NDX", "RUT", "VIX", "DJX",
                 "IRX", "FVX", "TNX", "TYX"}

# ── Futures (常用合约: 交易所 + 乘数 + 最小跳动 + 名称) ─────────────────
# 用于点价交易程序的「期货」模式。合约月份由 reqContractDetails 自动解析
# (近月 + 之后几个季月), tick/交易所/乘数从这里取。
# 格式: 根代码 -> (exchange, multiplier, tick_size, 描述)
FUTURES_SPECS = {
    # 股指期货 (季度合约: 3/6/9/12)
    "ES":  ("CME",   50,   0.25,  "E-mini S&P 500"),
    "MES": ("CME",   5,    0.25,  "Micro E-mini S&P 500"),
    "NQ":  ("CME",   20,   0.25,  "E-mini Nasdaq 100"),
    "MNQ": ("CME",   2,    0.25,  "Micro E-mini Nasdaq 100"),
    "RTY": ("CME",   50,   0.10,  "E-mini Russell 2000"),
    "M2K": ("CME",   5,    0.10,  "Micro E-mini Russell 2000"),
    "YM":  ("CBOT",  5,    1.0,   "E-mini Dow"),
    "MYM": ("CBOT",  0.5,  1.0,   "Micro E-mini Dow"),
    # 能源 / 金属 (月度合约)
    "CL":  ("NYMEX", 1000, 0.01,  "Crude Oil WTI"),
    "MCL": ("NYMEX", 100,  0.01,  "Micro Crude Oil"),
    "GC":  ("COMEX", 100,  0.10,  "Gold"),
    "MGC": ("COMEX", 10,   0.10,  "Micro Gold"),
    # 注: 点价梯按 2 位小数网格, 故只收录 tick ≥ 0.01 的品种
    # (如 SI 银 tick=0.005 暂不收录, 以免价格网格错位)。
}
FUTURES_SYMBOLS = list(FUTURES_SPECS.keys())
# 期货模式下合约月份下拉显示的最多档数 (近月起算, 含 ~3 个月后的季月)
FUTURES_MAX_EXPIRIES = 5

# 期货开多(BUY)强制带止盈+止损: 未在「条件单」面板设置好二者就拦截下单,
# 通过后下单并自动挂上 (市价单立即挂; 限价单等成交回报后再挂)。设 False 关闭强制。
FUTURES_REQUIRE_BRACKET = True

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
