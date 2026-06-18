# IBKR 点价交易 (ibkr_trader)

基于 **PyQt5 + ibapi** 的 IBKR 点价交易桌面程序。两个独立 GUI client 并行运行于同一个 TWS：
**期权 GUI**(`main.py`,clientId=10)与**正股 client**(`stock_trader.py`,clientId=11)。
核心特性:Futu 风格点价梯(深度摆盘 + 点击下单)、期权 T 型报价链、多腿组合策略、K 线图、
实时持仓与每仓位今日盈亏、真实/模拟双引擎切换。

> 项目整体背景见上层 `../CLAUDE.md`。本文件只详述 `ibkr_trader/` 目录。

> **📌 维护约定(必读)**:本文件是 `ibkr_trader/` 的**唯一总体文档**,作为活文档维护。
> **每次改动本目录的代码(新增/删除文件、改架构、改配置、修 bug、调行为)后,必须同步更新本文件**:
> 改了结构就更新对应小节(目录树 / 文件详解 / 配置速查 / 线程表),
> 并在文末 **[变更记录](#8-变更记录-changelog)** 追加一行(日期 + 一句话说明 + 涉及文件)。
> 文档与代码不一致时,以代码为准并立即修文档。

---

## 1. 快速开始

| 程序 | 启动方式 | clientId | 入口 | 桌面快捷方式 |
|------|---------|----------|------|------------|
| 期权点价 GUI | `start.bat` (`pythonw main.py`) | 10 | `main.py` | "IBKR 点价交易" |
| 正股点价 client | `start_stock.bat` (`pythonw stock_trader.py`) | 11 | `stock_trader.py` | "IBKR 正股交易" |

- 用 `pythonw` 启动(无控制台窗口)。`stdout`/`stderr` 自动重定向到 `logs/app_YYYY-MM-DD.log`
  (正股为 `logs/stock_app_YYYY-MM-DD.log`)。
- 启动时 `single_instance.kill_previous_instances()` 会**只杀掉同一脚本的旧实例**
  (期权 GUI 与正股 client 互不影响),从而在重连前释放 TWS 中占用的 clientId。
- 前置条件:TWS 已登录并开启 API(Global Configuration → API → Enable ActiveX and Socket Clients),
  端口 7496(live)/7497(paper)。详见文末"TWS API vs Gateway"。

---

## 2. 目录结构

```
ibkr_trader/
├── main.py                 # 期权 GUI 入口 (clientId=10, 日志重定向, 杀旧实例, 启动 QApplication)
├── stock_trader.py         # 正股 client 入口 (clientId=11, 点价梯+K线+正股持仓, 自带窗口类)
├── main_window.py          # 期权 GUI 主窗口 MainWindow — 组装所有 widget + 信号连线
├── ibkr_engine.py          # IBKR API 引擎 (EWrapper/EClient + 下单/撤单 + Qt 信号桥)  ★核心
├── paper_engine.py         # 模拟交易引擎 (复用 IBKR 行情, 本地撮合成交)
├── models.py               # 纯数据模型 (dataclass + Enum), 无 Qt/IBKR 依赖
├── config.py               # 全部常量 (连接/费率/颜色/tick/图表/交易时段)
├── single_instance.py      # 启动辅助: 杀掉同脚本的旧进程以释放 clientId
├── start.bat               # 期权 GUI 启动脚本
├── start_stock.bat         # 正股 client 启动脚本
├── check_spx_options.py    # 独立诊断脚本: 探测 SPX 期权合约/交易时段
├── app.ico / app_icon.png  # 期权 GUI 图标
├── stock_app.ico / stock_icon.png # 正股 client 图标
├── logs/                   # 运行日志 + 拒单日志 (自动生成)
│   ├── app_YYYY-MM-DD.log          # 期权 GUI 控制台输出
│   ├── stock_app_YYYY-MM-DD.log    # 正股 client 控制台输出
│   └── order_rejects_YYYY-MM-DD.jsonl  # 拒单详情 (每行一个 JSON)
└── widgets/
    ├── __init__.py
    ├── symbol_bar.py        # 顶栏: 代码搜索(自动补全)+连接状态+模式切换
    ├── option_chain.py      # 期权 T 型报价链 (到期日 Tab + 日期范围过滤)
    ├── price_ladder.py      # 点价梯 (5 列深度摆盘 + 点击下单 + 持仓摘要)  ★核心
    ├── position_panel.py    # 持仓面板 (期权 + 正股/ETF, P/L + 今日盈亏)
    ├── order_panel.py       # 委托面板 (挂单/历史, 撤单, 拒单标红)
    ├── account_bar.py       # 账户摘要条 (净值/现金/购买力/盈亏)
    ├── option_calculator.py # 期权理论价计算器 (Black-Scholes, 右下角, 随实时IV+时间刷新)
    ├── currency_dialog.py   # 外汇兑换对话框 (USD ↔ HKD/CNH/EUR/...)
    ├── quantity_selector.py # 数量选择小部件 (1–100 张)
    ├── strategy_defs.py     # 多腿策略模板 (纯数据: 牛市价差/蝶式/铁鹰/跨式...)
    ├── strategy_window.py   # 策略构建窗口 (懒加载, 组合腿 + combo 下单)
    ├── chart_window.py      # K 线图窗口 (懒加载, numpy+pyqtgraph, 无限滚动+实时)
    ├── candlestick_item.py  # pyqtgraph 自绘 OHLC 蜡烛 (涨空心/跌实心)
    └── chart_indicators.py  # 指标计算 (MA / VWAP / 量柱颜色)
```

---

## 3. 架构与数据流

```
       ┌─────────────────────────────────────────────────────────┐
       │                       TWS (port 7496/7497)                │
       └───────────────▲───────────────────────────┬──────────────┘
        EClient 请求    │                           │ EWrapper 回调
                        │                           ▼
       ┌────────────────┴───────────────────────────────────────┐
       │  IBKRApp(EWrapper, EClient)   —  ibkr_engine.py         │
       │  在独立 reader 线程运行 (app.run())                       │
       └───────────────────────────┬────────────────────────────┘
                                    │ emit pyqtSignal (线程安全跨线程)
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │  IBKRSignalBridge(QObject)  —  纯信号集合 (reader→GUI)   │
       └───────────────────────────┬────────────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │  IBKREngine  (高层封装: connect/下单/订阅/持仓/PnL)       │
       │  ▲ 也可换成 PaperEngine (同样的 bridge 信号接口)          │
       └───────────────────────────┬────────────────────────────┘
                                    ▼  connect 信号到各 widget 的槽
       ┌────────────────────────────────────────────────────────┐
       │  MainWindow  —  组装 widgets, 把信号连到 UI 更新          │
       │   SymbolBar · OptionChain · PriceLadder · PositionPanel  │
       │   OrderPanel · AccountBar · (ChartWindow/StrategyWindow) │
       └────────────────────────────────────────────────────────┘
```

**关键设计:线程边界靠 Qt 信号跨越。** ibapi 的 reader 线程绝不直接碰 Qt widget;
所有回调都通过 `IBKRSignalBridge` 上的 `pyqtSignal` emit,Qt 把它排队到 GUI 线程执行槽函数。

**双引擎可互换。** `IBKREngine` 与 `PaperEngine` 暴露相同的 bridge 信号名(见 `PaperSignalBridge`
逐一对应 `IBKRSignalBridge`),所以 UI 代码无需关心当前是真实还是模拟模式。模拟引擎只借用
IBKR 的行情 tick,在本地按 `BUY: ask<=limit / SELL: bid>=limit` 撮合成交,不向 TWS 发单,不允许做空。

---

## 4. 文件详解

### 4.1 入口层

**`main.py`** (54 行) — 期权 GUI 入口。
- pythonw 下重定向 stdout/stderr 到 `logs/app_*.log`;有控制台时 `os.system("")` 开启 ANSI。
- `kill_previous_instances(__file__)` → 高 DPI 属性 → `QApplication` → `MainWindow().show()` → `app.exec_()`。

**`stock_trader.py`** (369 行) — 正股 client,**自包含主窗口**(不复用 `MainWindow`)。
- clientId=11,与期权 GUI 并行。组装:`PriceLadder`(点价梯)+ `PositionPanel`(正股持仓今日盈亏)
  + `OrderPanel` + `ChartWindow` Tab。
- 日志写 `logs/stock_app_*.log`。正股下单走 `IBKREngine.place_stock_order()`。

**`single_instance.py`** (61 行) — `kill_previous_instances(script_path)`:
遍历进程,匹配 `python.exe/pythonw.exe` 且命令行运行**同名脚本、同目录**的旧实例并 `terminate()`
(超时则 `kill()`)。`_runs_script()` 处理相对路径(用进程 cwd 解析)。

### 4.2 数据模型 `models.py` (233 行,纯 dataclass/Enum)

- **Enum**:`OrderAction`(BUY/SELL)、`OrderStatus`(PendingSubmit/Submitted/Filled/Cancelled/Error)、
  `TradingMode`(Paper/Live)、`InstrumentType`(OPT/STK/ETF)、`OrderType`(LMT/MKT)。
- **`OptionInfo`** — 单个期权合约。`display_name`(如 `SPY 260516 C 585`,正股显示 `SYM (正股)`)、
  `mid`、`to_ibkr_key()`(tick 订阅唯一键;正股共用 `__stock__SYM` 键空间)。
- **`OrderInfo`** — 委托。含 `error_msg`(拒单原因)、`display_action`/`display_status`(中文)。
- **`PositionInfo`** — 期权持仓。`unrealized_pnl`/`net_pnl`(减佣金)/`pnl_pct`/`market_value`/`cost_basis`。
- **`AccountSummary`** — 账户净值/现金/购买力/盈亏。
- **`PortfolioPosition`** — 通用持仓(期权/正股/ETF),含 `daily_pnl`(来自 `reqPnLSingle`)、
  `has_pnl_data`、`multiplier`、`instrument_type`(中文)。
- **`ComboLegInfo`** — 组合订单单腿。**`DepthRow`** — 深度摆盘一行(价/买量/卖量/我的买卖挂单量)。

### 4.3 IBKR 引擎 `ibkr_engine.py` (1580 行) ★

**`IBKRSignalBridge(QObject)`** — reader 线程 → GUI 线程的全部信号定义,例如:
`tick_updated(key,bid,ask,last)`、`chain_ready`、`order_status_changed`、`execution_received`、
`portfolio_position_received`、`pnl_single_updated`、`depth_updated`、`historical_bars_ready`/
`historical_bar_update`、`open_order_received`(重连恢复挂单)、`order_rejected`、`symbol_search_results`。

**`IBKRApp(EWrapper, EClient)`** — 实现的 ibapi 回调:
- 连接/错误:`nextValidId`、`connectionClosed`、`error`(按 `IGNORED_ERROR_CODES` /
  `DATA_CONNECTION_ERROR_CODES` 分类;10167 延迟数据一次性提示;326 = clientId 占用)。
- 合约/链:`contractDetails(End)`、`securityDefinitionOptionParameter(End)`、`symbolSamples`(代码搜索)。
- 行情:`tickPrice`/`tickSize`/`tickString`/`tickGeneric`、`tickByTickBidAsk`/`tickByTickAllLast`、
  `tickOptionComputation`(IV + Greeks + 标的价 undPrice, 写入 `_tick_data[key]` 供计算器轮询;
  `subscribe_option_tick` 对期权请求 generic tick `106` 以确保 IV 下发)。
- 历史:`historicalData`/`historicalDataEnd`/`historicalDataUpdate`(K 线流)。
- 订单:`orderStatus`、`openOrder`、`execDetails`、`commissionReport`。
- 账户/持仓:`accountSummary(End)`、`position(End)`、`pnl`、`pnlSingle`。
- 深度:`updateMktDepth` / `updateMktDepthL2`。

**`IBKREngine`** — 高层封装(GUI 调用的 API):
- 连接:`connect(mode, host, ...)`、`disconnect`、`reconnect`、心跳 `_start_heartbeat`/`_on_heartbeat`、
  `_run_wrapper`(后台跑 `app.run()`)。
- 合约:`search_symbols`、`get_con_id`、`request_option_chain`、`resolve_option_con_id`、
  `_make_underlying_contract`/`_make_stock_contract`/`_make_option_contract`。
- 行情订阅:`subscribe_option_tick`、`subscribe_stock_tick`、`unsubscribe_tick`、`get_tick`、
  `subscribe_market_depth`/`unsubscribe_market_depth`、`request_historical_data`/`cancel_historical_data`。
- 账户/持仓/盈亏:`request_account_summary`、`request_positions`、`request_pnl`、
  `request_pnl_single`(每仓位今日盈亏)及各自 `cancel_*`。
- **下单**:`place_limit_order`、`place_market_order`、`place_stock_order`、`place_forex_order`、
  `place_combo_order`(多腿组合)、`cancel_order`、`cancel_all_orders`、`close_position`。
- 拒单处理:`_on_order_error`(非用户撤单/纯警告才报错)、`_log_rejection`(追加写
  `logs/order_rejects_*.jsonl`,含拒单时刻盘口/持仓/在途订单快照)、`_on_order_status`、`_on_execution`、
  `_on_open_order`(重连恢复 TWS 中已有挂单)。

### 4.4 模拟引擎 `paper_engine.py` (440 行)

- `PaperSignalBridge` 信号名与 `IBKRSignalBridge` 一一对应 → UI 无感切换。
- `PaperEngine(ibkr_engine)` 复用真实引擎的行情,本地按 tick 撮合限价单,维护模拟持仓/现金
  (起始 `PAPER_STARTING_CAPITAL=10000`),按 `config` 费率扣佣金。不做空。

### 4.5 主窗口 `main_window.py` (916 行)

`MainWindow(QMainWindow)`:
- `_build_ui()` 组装顶栏 SymbolBar、账户条 AccountBar、左侧 OptionChain、中间 PriceLadder、
  右侧 PositionPanel + OrderPanel(QSplitter 布局),底部状态栏 + 交易时段指示。
- `_connect_signals()` 把 engine bridge 的全部信号连到对应槽。
- 交互槽:`_on_connect/_on_disconnect`、`_on_symbol_changed`、`_on_mode_changed`(真实/模拟切换)、
  `_load_option_chain`、`_fetch_stock_price`(链头显示标的实时价)、`_on_option_selected`、
  `_on_contract_searched`/`_load_validated_contract`、`_on_order_requested`/`_on_market_order_requested`、
  `_on_close_position_requested`、`_on_cancel_all_requested`/`_on_cancel_order`、`_on_currency_exchange`、
  `_on_open_chart`(懒加载 ChartWindow)、`_on_open_strategy`(懒加载 StrategyWindow)、
  `_on_detach_ladder`/`_on_reattach_ladder`(点价梯独立窗口)、`_update_session_indicator`(SPX GTH/RTH/Curb)、
  `_on_error`/`_on_order_rejected`(弹窗 + 状态栏标红)、`closeEvent`。

### 4.6 widgets

| 文件 | 角色与要点 |
|------|-----------|
| `symbol_bar.py` (300) | 代码搜索框(`QListWidget` 自动补全,走 `symbol_search_results`)+ 连接状态灯 + 真实/模拟 `QComboBox` 切换。 |
| `option_chain.py` (489) | T 型报价表;按到期日分 Tab,顶部日期范围过滤(每范围最多 `MAX_EXPIRY_TABS_PER_RANGE` 个 Tab);ATM 行高亮;受 `MAX_SIMULTANEOUS_STREAMS` 限制订阅数。 |
| `price_ladder.py` (1121) ★ | Futu 风格 5 列摆盘(我的买单/买量/价格/卖量/我的卖单)+ 深度条可视化;点击价格即下限价单;含合约搜索、数量选择、确认勾选、持仓摘要、市价买/卖/平仓、取消所有订单;tick size 按 penny-pilot(<$3=0.01,≥$3=0.05)及 `TICK_SIZE_OVERRIDES`(SPX 0.05/0.10)。 |
| `position_panel.py` (318) | 持仓表;支持期权(引擎)+ 正股/ETF(`portfolio_position_received`);显示未实现盈亏、今日盈亏、百分比、可按类型筛选。 |
| `order_panel.py` (141) | 挂单/历史委托表;撤单按钮;拒单行标红,悬停看原因。 |
| `account_bar.py` (192) | 账户摘要条;净值/现金/购买力/盈亏;每 `ACCOUNT_REFRESH_MS` (3s) 刷新。 |
| `option_calculator.py` (≈330) | **期权理论价计算器**(主窗口右下角)。跟随左侧待交易期权,用 IBKR 推送的 IV + 标的价 + 行权价 + 剩余到期时间跑 Black-Scholes 算「应有价格」,并与盘口中间价比对(偏贵标红/偏便宜标绿)。QTimer 每 `CALCULATOR_REFRESH_MS`(700ms)刷新——既跟随实时行情,也随时间衰减(剩余 T 重算)。取消「跟随实时」勾选进入手动 what-if(可任意改 S/IV/利率/天数);正股伪合约显示「仅期权适用」;IV 未到时显示「等待 IV 行情…」。 |
| `currency_dialog.py` (180) | 外汇兑换对话框,走 `place_forex_order`(`FOREX_PAIRS`)。 |
| `quantity_selector.py` (31) | 1–100 张数量微调器,emit `quantity_changed`。 |
| `strategy_defs.py` (196) | 纯数据:`StrategyType` 枚举 + `LegTemplate`/`StrategyTemplate` + `STRATEGY_REGISTRY`(牛/熊市价差、蝶式、铁鹰、铁蝶、跨式、宽跨、日历价差、自定义)。 |
| `strategy_window.py` (777) | 懒加载组合策略窗口;选模板 + 行权价/到期 → 生成各腿 → `place_combo_order` 一键下 combo。 |
| `chart_window.py` (879) | 懒加载 K 线窗口(numpy + pyqtgraph,约 25MB,故不在启动时导入);多周期(`CHART_TIMEFRAMES`)、MA5/20/50/200 + VWAP + 量柱、向左平移无限加载历史、实时流式/轮询更新。 |
| `candlestick_item.py` (123) | pyqtgraph 自绘 OHLC,涨=空心绿、跌=实心红,cosmetic pen 任意缩放清晰。 |
| `chart_indicators.py` (38) | `IndicatorCalculator` 静态方法:`moving_average`、`vwap`(累积)、`volume_colors`。 |

> `option_chain.py` 的范围过滤桶:**0DTE / 本周 / 下周 / 本月 / 下月 / 远月 / 全部**;
> `position_panel.py` 与 `order_panel.py` 的筛选/上限:持仓可按 全部/期权/正股ETF 筛选,委托面板最多显示约 50 条。

### 4.7 诊断脚本 `check_spx_options.py` (329 行)
独立运行的探测脚本,用于核对 SPX/SPXW 期权合约定义与交易时段(GTH/RTH/Curb),非 GUI 依赖。
**用 clientId=99** 连 live(7496),只读、不下单,避免与 10/11 冲突。

---

## 4.8 线程与定时器一览

| 组件 | 线程 | 机制 |
|------|------|------|
| IBKR API 回调 | reader(daemon) | `app.run()` 循环,EWrapper 回调在此线程触发 |
| Qt GUI | 主线程 | 事件循环,槽函数接收 bridge 信号 |
| 跨线程 | bridge | `pyqtSignal`(线程安全)从 reader → GUI |
| 阻塞请求 | 主线程 | `threading.Event` + 超时(合约/链 ~10s) |
| 模拟撮合 | 主线程 | `PaperEngine` QTimer 500ms 检查挂单成交 |
| 账户刷新 | 主线程 | `AccountBar` QTimer `ACCOUNT_REFRESH_MS`(3s) |
| 报价刷新 | 主线程 | `OptionChain`/`PriceLadder` QTimer ~1s |
| 心跳/超时 | 引擎 | `_on_heartbeat`,~30s 无 tick 告警;clientId 占用(err 326)按 +10 重试 |

---

## 5. 配置 `config.py` 速查

| 类别 | 关键常量 |
|------|---------|
| 连接 | `IBKR_HOST=127.0.0.1`;TWS `7496`(live)/`7497`(paper);Gateway `4002`(live)/`4001`(paper);`IBKR_CLIENT_ID=10`、`IBKR_STOCK_CLIENT_ID=11` |
| 行情 | `MARKET_DATA_TYPE=1`(1实时/2冻结/3延迟/4延迟冻结);`MAX_SIMULTANEOUS_STREAMS=95` |
| Tick | `TICK_SIZE_SMALL=0.01`/`TICK_SIZE_LARGE=0.05`/`TICK_THRESHOLD=3.0`;`LADDER_ROWS=201`;`TICK_SIZE_OVERRIDES`(SPX/XSP/NDX/RUT) |
| 深度 | `DEPTH_ROWS=10` |
| 费率 | 期权 `$0.65/张`,`min $1.00`;正股 `$0.005/股`,`min $1.00`;模拟起始资金 `$10000` |
| 时段(ET) | SPX GTH 20:15→09:15,RTH 09:30→16:15,Curb 16:15→17:00;`EXTENDED_HOURS_SYMBOLS={SPX}` |
| 代码 | `INDEX_SYMBOLS`(secType=IND);`DEFAULT_SYMBOLS`(SPY/SPX/QQQ/...) |
| 错误码 | `IGNORED_ERROR_CODES`(静默)/`DATA_CONNECTION_ERROR_CODES`(2100/2103-2108 作警告上抛) |
| 期权定价 | `RISK_FREE_RATE=0.045`、`DIVIDEND_YIELD=0.0`、`OPTION_MARKET_CLOSE_ET=16`、`CALCULATOR_REFRESH_MS=700`(计算器用) |
| 图表 | `CHART_TIMEFRAMES`(1秒~月线)+ 各类颜色 |

---

## 6. 日志与拒单排查

- **运行日志**:`logs/app_YYYY-MM-DD.log`(期权)/`logs/stock_app_YYYY-MM-DD.log`(正股),
  收纳 pythonw 下所有 `print`/traceback。
- **拒单日志**:`logs/order_rejects_YYYY-MM-DD.jsonl`,每行一个 JSON,含
  `time/mode/order_id/contract/action/qty/order_type/limit_price/reject_code/reject_msg`
  + 拒单时刻盘口(bid/ask/last)+ 当前持仓数量 + 其他在途订单快照。
  **分析方法**:让 Claude 直接读该 jsonl 即可定位拒单原因。
- **常见拒单**:同合约第二笔被 TWS *Duplicate Order Precaution* 拦截(API 订单无法回应确认框,TWS 自动拒)。
  一次性修复:TWS → Global Configuration → API → Precautions → 勾选
  **"Bypass Order Precautions for API Orders"**。

---

## 7. TWS Workstation API 与 IB Gateway 的区别

两者**用的是同一套 IBKR API(EClient/EWrapper,即本项目用的 `ibapi`)**,客户端代码完全不用改,
**唯一实质差异是默认端口**。区别在于"宿主程序"不同:

| 维度 | **TWS (Trader Workstation)** | **IB Gateway** |
|------|------------------------------|----------------|
| 本质 | 完整的图形化交易终端(人用 + API) | 纯 API 网关,**几乎无界面**(只有一个连接状态小窗) |
| 图形界面 | 有:报价、图表、下单面板、风控弹窗等全套 | 无:不能手动看盘/手动下单 |
| 默认端口 | **7496**(live)/ **7497**(paper) | **4001**(live)/ **4002**(paper) |
| 资源占用 | 重(Java GUI,内存/CPU 高) | 轻(无渲染,适合服务器/长跑/无人值守) |
| 稳定性 | GUI 偶发卡顿可能影响 API | 更精简,长时间运行更稳 |
| 自动重启/维护 | 每日需重新登录(可设自动重连) | 同样有每日重启,但更适合配合 IBC 等自动化登录 |
| API 设置位置 | Global Configuration → API → Settings | 同样的 API 设置页,但整个程序就是为 API 服务 |
| 适用场景 | **既要手动盯盘又要 API**(本项目当前用法) | **纯自动化/服务器部署**,不需要人看界面 |
| 行情订阅 | 与 Gateway 共享同一账户的行情权限 | 同上,权限取决于账户订阅,而非用哪个宿主 |

**对本项目的含义:**
- 当前 `config.py` 默认连 **TWS 7496(live)/7497(paper)**;若改用 Gateway,只需把端口改成
  **4002(live)/4001(paper)**,其余代码(`IBKRApp`、下单、回调)一字不改。
- **行情数据包**与用 TWS 还是 Gateway **无关**——取决于账户购买的行情订阅(本项目已购美股快照)。
- **同一时刻**对一个账户,TWS 与 Gateway **通常二选一登录**(同账户重复登录会互踢);但**同一个宿主**
  下可以让**多个 client(不同 clientId)并行**——本项目正是用一个 TWS 同时接 clientId=10(期权)
  与 11(正股)。
- 选型建议:需要一边人工看盘一边跑这套 GUI → 用 **TWS**;要把它丢到服务器纯自动跑、省资源 → 用 **Gateway**
  (配合 IBC 实现自动登录与每日重启)。

---

## 8. 变更记录 (Changelog)

> 倒序排列,最新在上。每次改动本目录代码后追加一行:**日期 — 一句话说明(涉及文件)**。

- **2026-06-18** — **窗口自由缩放 + 子面板按比例联动 + 尺寸记忆**:三层 splitter 加 `setStretchFactor`
  (主竖向 1:1、下方横向 4:5、右侧竖向 5:2) 使面板随窗口等比联动;`setChildrenCollapsible(False)`
  防误折叠;最小尺寸降到 900×600;用 `QSettings` 持久化窗口几何 + 各 splitter 位置 (跨会话);
  点价梯分离/贴回后重设 stretch。(`main_window.py`)
- **2026-06-18** — 修复**未实现/今日盈亏偶尔闪烁成 0** 的 bug:IBKR 对尚未算出的 PnL 字段推 DBL_MAX
  (~1.8e308),原先 `pnlSingle._clean` 将其转成 0 再覆盖显示,导致好值被刷成 0。改为转 NaN,
  GUI(持仓面板 + 账户栏)跳过 NaN 字段、保留上一次的好值;账户级 `pnl()` 同步加 DBL_MAX 处理。
  (`ibkr_engine.py`, `widgets/position_panel.py`, `widgets/account_bar.py`)
- **2026-06-18** — 新增**期权理论价计算器**(主窗口右下角):Black-Scholes 用实时 IV + 标的价算「应有价格」
  并与盘口中间价比对(偏贵/偏便宜),随实时行情与时间衰减自动刷新,支持手动 what-if。
  引擎新增 `tickOptionComputation` 回调接收 IV/Greeks/标的价,`subscribe_option_tick` 对期权请求 generic tick 106。
  (`widgets/option_calculator.py` 新增, `ibkr_engine.py`, `config.py`, `main_window.py`)
- **2026-06-18** — 确立 `README.md` 为本目录唯一总体文档(活文档),加入维护约定与本变更记录。(`README.md`)
- **2026-06-18** — 分析行情连接码 **2108 / 2107**「inactive but should be available upon demand」误报问题:
  这是 IBKR 正常的**空闲(按需自动重连)**状态,非真故障,但 `main_window.py:_on_error` 当前把它和真正断开
  (2103/2105/2100)一样标红「行情数据连接异常」。**修复待定**:应把 2107/2108 单独归为中性提示、不标红,
  仅 2100/2103/2105 标红。(`main_window.py:838-850`, `config.py:92-101`)

### 已知问题 / 待办

- [ ] **2108/2107 误报标红**(见上)——尚未改代码,仅完成分析。

---

> 历史里程碑(本变更记录建立前,摘自提交历史与 `../CLAUDE.md`):
> 实时显示标的价于期权链头 · 重连恢复挂单/撤单修复 · tick size 锁定 penny-pilot(0.01/0.05)并恒显买卖盘 ·
> 免确认快速下单 · 拒单 eTradeOnly/firmQuoteOnly 空字段修复 · 拒单日志 `order_rejects_*.jsonl` · 正股独立 client。
