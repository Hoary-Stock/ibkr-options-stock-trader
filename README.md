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
| 期权点价 GUI (Gateway, 新版) | `start_gateway.bat` (`pythonw main_gw.py`) | 10 | `main_gw.py` | **"IBKR 点价交易"** |
| 期权点价 GUI (TWS, 旧版) | `start.bat` (`pythonw main.py`) | 10 | `main.py` | — |
| 正股点价 client (Gateway, 新版) | `start_stock_gateway.bat` (`pythonw stock_trader_gw.py`) | 11 | `stock_trader_gw.py` | **"IBKR 正股交易"** |
| 正股点价 client (TWS, 旧版) | `start_stock.bat` (`pythonw stock_trader.py`) | 11 | `stock_trader.py` | — |
| 期权组合分析器 | `start_combo.bat` (`pythonw combo_analyzer.py`) | 12 | `combo_analyzer.py` | — |

> 两个桌面快捷方式现已指向 **Gateway 新版**(`main_gw.py` / `stock_trader_gw.py`,需先登录 IB Gateway:
> 4001 实盘 / 4002 模拟)。旧 TWS 版仍可用 `start.bat` / `start_stock.bat` 手动启动,新旧文件名独立、
> 互不杀进程,可同时运行对比。

- 用 `pythonw` 启动(无控制台窗口)。`stdout`/`stderr` 自动重定向到 `logs/app_YYYY-MM-DD.log`
  (正股为 `logs/stock_app_YYYY-MM-DD.log`)。
- 启动时 `single_instance.kill_previous_instances()` 会**只杀掉同一脚本的旧实例**
  (期权 GUI 与正股 client 互不影响),从而在重连前释放 TWS 中占用的 clientId。
- 前置条件:TWS 已登录并开启 API(Global Configuration → API → Enable ActiveX and Socket Clients),
  端口 7496(live)/7497(paper)。详见文末"TWS API vs Gateway"。
- **模式三选一**(顶栏「模式」下拉):**本地模拟**(本地撮合,无需真实账户)/ **IBKR模拟盘**
  (订单真实发到 7497 的模拟账户,测试下单链路,需先登录一个 paper TWS 会话)/ **实盘**(7496,真实资金)。

---

## 2. 目录结构

```
ibkr_trader/
├── main.py                 # 期权 GUI 入口 — TWS 旧版 (clientId=10, 日志重定向, 杀旧实例, 启动 QApplication)
├── main_gw.py              # 期权 GUI 入口 — Gateway 新版 (设 IBKR_USE_GATEWAY=1, 复用 MainWindow, 标题加[GW])
├── stock_trader.py         # 正股 client 入口 — TWS 旧版 (clientId=11, 点价梯+K线+正股持仓, 自带窗口类)
├── stock_trader_gw.py     # 正股 client 入口 — Gateway 新版 (设 IBKR_USE_GATEWAY=1, 复用 StockTraderWindow)
├── combo_analyzer.py       # 期权组合分析器入口 (clientId=12, 组合历史价合成 + 组合原子交易)
├── start_combo.bat         # 组合分析器启动脚本
├── combo_positions.json    # 自动生成: 组合持仓分组 (IBKR 不保留分组, 本地持久化)
├── main_window.py          # 期权 GUI 主窗口 MainWindow — 组装所有 widget + 信号连线
├── ibkr_engine.py          # IBKR API 引擎 (EWrapper/EClient + 下单/撤单 + Qt 信号桥)  ★核心
├── paper_engine.py         # 模拟交易引擎 (复用 IBKR 行情, 本地撮合成交)
├── models.py               # 纯数据模型 (dataclass + Enum), 无 Qt/IBKR 依赖
├── config.py               # 全部常量 (连接/费率/颜色/tick/图表/交易时段)
├── single_instance.py      # 启动辅助: 杀掉同脚本的旧进程以释放 clientId
├── start.bat               # 期权 GUI 启动脚本 (TWS 旧版)
├── start_gateway.bat       # 期权 GUI 启动脚本 (Gateway 新版)
├── start_stock.bat         # 正股 client 启动脚本 (TWS 旧版)
├── start_stock_gateway.bat # 正股 client 启动脚本 (Gateway 新版)
├── check_spx_options.py    # 独立诊断脚本: 探测 SPX 期权合约/交易时段
├── check_option_history.py # 独立诊断脚本: 检测账户是否有「期权历史数据」权限 (clientId=99)
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
    ├── account_bar.py       # 账户摘要条 (净值/现金/购买力/盈亏 + 各币种现金)
    ├── currency_balance.py  # 各币种现金余额条 (EUR/USD/..., 期权GUI+正股复用)
    ├── option_calculator.py # 期权理论价计算器 (Black-Scholes, 右下角, 随实时IV+时间刷新)
    ├── currency_dialog.py   # 外汇兑换对话框 (USD ↔ HKD/CNH/EUR/...)
    ├── quantity_selector.py # 数量选择小部件 (1–100 张)
    ├── strategy_defs.py     # 多腿策略模板 (纯数据: 牛市价差/蝶式/铁鹰/跨式...)
    ├── combo_pricing.py     # 组合定价纯逻辑 (净价/历史合成/行权价自动分配, 可单测)
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

**三种模式 (`TradingMode`)。**
- **本地模拟 (`PAPER`)** — 走 `PaperEngine`,连 7497 仅借行情,本地按 `BUY: ask<=limit / SELL: bid>=limit`
  撮合,**不向 TWS 发单**、不允许做空。无需真实模拟账户即可试用。
- **IBKR模拟盘 (`IBKR_PAPER`)** — 走真实 `IBKREngine`,连 **7497** 把订单**真实提交到 IBKR 模拟账户**,
  用于端到端验证下单链路 (placeOrder→TWS→成交回报),不涉及真实资金。需先登录一个 paper TWS 会话。
- **实盘 (`LIVE`)** — 走真实 `IBKREngine`,连 **7496**,真实资金;切换时弹确认框。

判定靠 `TradingMode.uses_ibkr_engine` (仅 PAPER 为 False) 与 `is_live_port` (仅 LIVE 连 7496)。

**双引擎可互换。** `IBKREngine` 与 `PaperEngine` 暴露相同的 bridge 信号名(见 `PaperSignalBridge`
逐一对应 `IBKRSignalBridge`),所以 UI 代码无需关心当前用的哪个引擎。

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
  `TradingMode`(Paper 本地模拟 / IBKRPaper IBKR模拟盘 / Live 实盘;含 `label`/`uses_ibkr_engine`/`is_live_port`)、
  `InstrumentType`(OPT/STK/ETF)、`OrderType`(LMT/MKT)。
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
- 行情订阅:`subscribe_option_tick`(流式)、`snapshot_option_tick`(一次性快照, 配 `tickSnapshotEnd`
  自动清理, 不占常驻线)、`subscribe_stock_tick`、`unsubscribe_tick`、`get_tick`、
  `subscribe_market_depth`/`unsubscribe_market_depth`、`request_historical_data`/`cancel_historical_data`。
  错误 300(cancelMktData 找不到 tickerId, 多见于快照已自动取消)静默处理, 不再弹状态栏。
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
| `symbol_bar.py` (300) | 代码搜索框(`QListWidget` 自动补全,走 `symbol_search_results`)+ 连接状态灯 + 模式 `QComboBox`(本地模拟 / IBKR模拟盘 / 实盘,item data 存 `TradingMode.value`,切到实盘弹确认)。 |
| `option_chain.py` (≈520) | T 型报价表;按到期日分 Tab,顶部日期范围过滤(每范围最多 `MAX_EXPIRY_TABS_PER_RANGE` 个 Tab)+ **「🔄 刷新报价」按钮**;ATM 行高亮。**报价改用一次性快照**(`snapshot_option_tick`,切 Tab / 点按钮各拉一次,用完即弃**不占常驻行情线**),解决 Gateway 行情线紧张时整条链(含 TSLA)无数据;受 `MAX_SIMULTANEOUS_STREAMS` 限制每批快照数。 |
| `price_ladder.py` (1121) ★ | Futu 风格 5 列摆盘(我的买单/买量/价格/卖量/我的卖单)+ 深度条可视化;点击价格即下限价单;含合约搜索、数量选择、确认勾选、持仓摘要、市价买/卖/平仓、取消所有订单;tick size 按 penny-pilot(<$3=0.01,≥$3=0.05)及 `TICK_SIZE_OVERRIDES`(SPX 0.05/0.10)。 |
| `position_panel.py` (318) | 持仓表。**真实模式持仓全部来自 IBKR API**(`portfolio_position_received` = reqPositions + `reqPnLSingle` 盈亏),不依赖本地成交跟踪,故无幻影持仓/数目准;模拟模式来自 `PaperEngine` 本地撮合。显示未实现盈亏、今日盈亏、百分比、可按类型筛选。 |
| `order_panel.py` (141) | 挂单/历史委托表;撤单按钮;拒单行标红,悬停看原因。 |
| `account_bar.py` (≈210) | 账户摘要条;净值/现金/购买力/盈亏 + 内嵌 `CurrencyBalanceBar`(各币种现金);每 `ACCOUNT_REFRESH_MS` (3s) 刷新账户摘要 + `request_currency_balances()`。 |
| `currency_balance.py` (≈70) | 各币种现金余额单行标签(`币种: EUR €414.00  USD $0.00`)。订阅引擎 `currency_balance_updated`(来自 `reqAccountSummary "$LEDGER:ALL"` 的 `CashBalance` 行);非零币种排前、含 0 余额也显示;期权 GUI 嵌在 `AccountBar`、正股 client 放顶栏。 |
| `option_calculator.py` (≈610) | **期权理论价计算器**(主窗口右下角),**两列布局**。**左列「正向·理论价」**:跟随左侧待交易期权,用 IBKR 推送的 IV + 标的价 + 行权价 + 剩余到期时间跑 Black-Scholes 算「应有价格」,并与盘口中间价比对(偏贵标红/偏便宜标绿);QTimer 每 `CALCULATOR_REFRESH_MS`(700ms)刷新(随行情 + 时间衰减);取消「跟随实时」进入手动 what-if(改 S/IV/利率/天数)。**右列「反向·求标的价」**:可任意改各参数甚至**目标期权价**,用单调二分法 `solve_underlying_for_price` 反推「在该到期时间下、期权要值目标价、标的需到的价位」,并对比当前标的算需变动金额/百分比(↑绿/↓红);换合约时自动用实时值播种一次,「↺ 用实时值填充」可手动重置;Put 目标价超 `K·e^(-rT)` 显示「无解」。正股伪合约两列均显示「仅期权适用」。 |
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

### 4.8 期权组合分析器 `combo_analyzer.py` (独立程序, clientId=12)

独立 PyQt5 程序(`start_combo.bat`),复用 `IBKREngine`,专做**多腿期权组合**的价格分析与**原子交易**。

**功能:**
- **组合即时净价**:选标的 → 加载期权链 → 选策略(蝶式/铁鹰/铁蝶/跨式/宽跨/各类垂直价差/日历)
  → 自动按 ATM±翼展填各腿行权价(可逐腿手改)→ 显示组合净价(借记为正/贷记为负)与各腿最新价。
- **组合历史价(券商一般不提供)**:为每条腿调 `IBKREngine.request_option_historical_data`
  (期权合约历史 K 线,`formatDate=2` epoch 对齐),按时间戳**交集**合成
  `组合价 = Σ 各腿(BUY:+ / SELL:−)×ratio×close`,画价随时间变化的折线。
  数据类型可选 TRADES / MIDPOINT(期权流动性差时 MIDPOINT 更连续)。
- **今日合并 K 线**:点「今日合并K线」拉各腿**当日 OHLC**(`duration=1 D`),用 `compute_combo_ohlc`
  合并成组合**蜡烛图**(空头腿用其低/高反向贡献给组合高/低,给出价值包络),复用 `CandlestickItem`;
  滚轮缩放 / 拖动平移(pyqtgraph 默认)。
- **当日实时录制(零历史权限)**:点「▶ 录制当日」后, 每 2 秒用各腿实时盘口中价合成组合净价、
  累积成当日曲线实时画出 —— **不调用 `reqHistoricalData`, 只要有实时行情即可**, 适合无期权历史
  权限、只看当日的场景。某腿暂无报价时跳过该次采样。
- **绘图**:折线(历史/录制)与蜡烛(今日)共用一个 `PlotWidget`,统一**索引 x 轴 + 自定义时间刻度**。
- **连接模式**:顶栏「模式」下拉 = **IBKR模拟盘 (7497, 默认)** / **实盘 (7496)**。组合下单必须走真实
  IBKR 引擎(本地模拟不支持 BAG),故无「本地模拟」。默认连模拟盘,可端到端测试组合下单而不动真钱;
  选实盘时弹确认框。
- **组合原子交易**:用 IBKR 原生 **BAG 组合单**(`place_combo_order`)整组开仓,**整组成交**。
  - **持仓视为一个整体**:面板只提供「整组平仓」(反向 BAG 单)与「加仓」(同向 BAG 单),
    **不提供单腿平仓** —— 满足「同一组持仓,只能一起平仓或加仓」。
  - 组合分组 IBKR 账户侧不保留(会拆成各腿),故本地持久化到 `combo_positions.json`,重启恢复。
  - 持仓面板每秒由各腿实时盘口中价合成**现净价**与**盈亏**
    (BUY 组合 `(现−开)×组数×100`,SELL 组合反号)。
- **纯逻辑** `widgets/combo_pricing.py`:`combo_price_from_prices` / `compute_combo_series`
  / `auto_assign_strikes` / `resolved_legs`,无 Qt/IBKR 依赖,可单测。
- 日志:`logs/combo_app_YYYY-MM-DD.log`。

> ⚠️ 「计算组合历史价」依赖账户的**期权历史数据权限**(与实时/快照行情是**独立订阅**);
> 是否具备可用 `check_option_history.py` 实测。无权限时改用「▶ 录制当日」(实时累积, 零历史权限)。
> 本程序只发组合单、从不单腿下单;但无法阻止用户在别的 GUI 里单腿操作这些合约。

---

### 4.9 Gateway 版入口 `main_gw.py` (新版本, 缓解 TWS 崩溃)

**为什么有这个**:TWS(Trader Workstation)是重型 Java GUI,长跑易卡顿/崩溃。本项目典型用法是
期权 GUI(clientId=10)+ 正股 GUI(clientId=11)同开,各自加载期权链时每个到期日 tab 订 ~62 行
行情,**合计逼近甚至超过账户 ~100 行的行情上限**,叠加 TWS 自身的界面渲染负担 → 崩溃高发。

**新版怎么做的**(全部由环境变量 `IBKR_USE_GATEWAY=1` 驱动,在 `config.py` 导入时求值):
- **连 IB Gateway**(纯 API 网关,几乎无界面、内存约为 TWS 一半)而非 TWS:端口
  `4001`(live)/ `4002`(paper)。
- **收紧行情订阅**:`MAX_SIMULTANEOUS_STREAMS` 95→45、`CHAIN_STRIKES_AROUND_ATM` 15→10
  (期权链显示/订阅 ATM±10=21 档),使期权+正股两个 GUI 合计稳在 100 行以内。
- **与旧版完全隔离**:入口文件名独立(`main_gw.py`)→ `kill_previous_instances` 只杀本脚本旧
  进程,**不会动正在运行的 `main.py`**;日志写 `logs/app_gw_*.log`;窗口标题加 `[GW 新版]`。
  旧版 `start.bat`/`main.py` 不设环境变量 → 行为与之前**字节级一致**(TWS + 95/15)。

**启动**:先开并登录 **IB Gateway**(API → Socket Port 确认 4001/4002、勾选 Enable
ActiveX and Socket Clients),再双击 `start_gateway.bat`。在 GUI 顶栏选「IBKR模拟盘」即连 4002
模拟账户测试。注意:同一账户 TWS 与 Gateway 通常二选一登录(同账户重复登录会互踢)。

**回退**:有任何问题直接用旧 `start.bat`(TWS)即可,新版无需卸载、互不影响。

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
| 连接 | `IBKR_HOST=127.0.0.1`;TWS `7496`(live)/`7497`(paper);Gateway `4002`(live)/`4001`(paper);`IBKR_CLIENT_ID=10`、`IBKR_STOCK_CLIENT_ID=11`、`IBKR_COMBO_CLIENT_ID=12` |
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
- 当前 `config.py` 默认连 **TWS 7496(live)/7497(paper)**;若改用 Gateway,端口为
  **4001(live)/4002(paper)** (IB Gateway 出厂默认),其余代码(`IBKRApp`、下单、回调)一字不改。
  现在已内置开关:用 `start_gateway.bat` / `main_gw.py` 启动即设环境变量 `IBKR_USE_GATEWAY=1`,
  自动切到 Gateway 端口(详见 §4.9)。
- **行情数据包**与用 TWS 还是 Gateway **无关**——取决于账户购买的行情订阅(本项目已购美股快照)。
- **同一时刻**对一个账户,TWS 与 Gateway **通常二选一登录**(同账户重复登录会互踢);但**同一个宿主**
  下可以让**多个 client(不同 clientId)并行**——本项目正是用一个 TWS 同时接 clientId=10(期权)
  与 11(正股)。
- 选型建议:需要一边人工看盘一边跑这套 GUI → 用 **TWS**;要把它丢到服务器纯自动跑、省资源 → 用 **Gateway**
  (配合 IBC 实现自动登录与每日重启)。

---

## 8. 变更记录 (Changelog)

> 倒序排列,最新在上。每次改动本目录代码后追加一行:**日期 — 一句话说明(涉及文件)**。

- **2026-06-19** — **双击持仓/委托的合约 → 跳到该标的 + 加载到点价梯**。委托面板 `order_panel` 新增
  `option_selected` 信号(双击行,COMBO 跳过)+ `_row_options` 行映射;`_on_option_selected` 增加:若合约属于
  另一标的则切换标的并重载期权链(`symbol_bar.set_symbol`),再把合约载入点价梯+计算器(持仓面板原本就有双击跳转)。
  (`widgets/order_panel.py`, `widgets/symbol_bar.py`, `main_window.py`)
- **2026-06-19** — **实盘↔模拟切换时盈亏/净值显示按各自账户独立**。`account_bar.stop()`(断开/切换时调用)把
  今日盈亏/未实现/总资产/现金/购买力清回「--」,避免残留上一个账户的数字;重连后由新账户(模拟连 4002 → 模拟账户,
  实盘连 4001 → 实盘账户)的 reqPnL/reqPnLSingle 重新填充,数据本就按连接的账户独立。(`widgets/account_bar.py`)
- **2026-06-19** — **修复热切换后模式下拉/标的框卡死**。切换模式时 `set_switching(True)` 禁用了下拉+标的输入框,
  重连完成后从未复位 → 切到模拟/实盘后无法再改标的、无法再切回(实盘新连正常因为没走切换)。修复:
  `_on_connected` / `_on_disconnected` 开头调 `set_switching(False)` 复位。(`main_window.py`)
- **2026-06-18** — **桌面快捷方式连带自动起 IBC 网关**。新增 `start_full_options.bat` / `start_full_stock.bat`:
  netstat 查 4001 没监听则先用 IBC(`C:\IBC\StartGateway_live.bat`)起实盘 Gateway, 再开交易程序;桌面
  「IBKR 点价交易/正股交易」改指这俩(图标保留)。IBC 用 `ReadOnlyApi=no`(修早先 code 321 只读拒单)、
  `AutoRestartTime=05:00 AM`(一周内免重复 2FA)。launcher 起实盘(4001)+模拟(4002)两网关后 **轮询等 4001
  就绪(最多~3分钟, 控制台显示进度, 超时 pause 不闪退)再开程序** —— 修"程序先于网关启动→连不上(502)像闪退"。
  两网关都在线 → 顶栏「实盘⇄IBKR模拟盘」秒切。(`start_full_options.bat`, `start_full_stock.bat`, 桌面 .lnk, `C:\IBC\*`)
- **2026-06-18** — **今日盈亏修好: dailyPnL 不可用时用 已实现+未实现 兜底**。日志实测: 本账户 reqPnL 的
  `dailyPnL` **常年返回 DBL_MAX(不可用)**→ 被转 NaN → 显示「--」; 但同一回调的 `unrealizedPnL`/
  `realizedPnL` **有效**(实测 realized=-168.79、unrealized=+16.67)。改法: `update_daily_pnl` 在 dailyPnL 为
  NaN 时取 `realizedPnL + unrealizedPnL`(IBKR 的 realizedPnL 已含手续费), dailyPnL 可用时仍优先用它。
  今日盈亏由「--」变为 ≈ -152。**同时撤回了之前两版错误尝试**(本地扣费 / 现金流+市值自算误显示 +$45931)。
  (`ibkr_engine.py`, `widgets/account_bar.py`)
- **2026-06-18** — **真正成交播放提示音**。新增 `sound_alerts.play_fill(side)`(后台线程播放,不卡 GUI):
  优先放 `sounds/BUY.(wav|mp3)` / `sounds/SELL.(wav|mp3)`(可用 GPT-SoVITS 生成),回退 `sounds/FILL.*`,
  再回退 winsound 蜂鸣(买升调/卖降调)。期权 GUI 与正股 client 均接 `execution_received`(仅真实引擎成交,
  本地模拟不响)。语音文件放 `sounds/` 即生效,无需改代码。**已用训练好的御坂美琴 misaka v3 模型生成
  `sounds/BUY.wav`(日语「買い」)/`SELL.wav`(日语「売り」)**(脚本 `GPT-SoVITS-Training/.../gen_fill_voices2.py`,
  走 inference_webui;api_v2 不支持 v3)。(`sound_alerts.py`, `sounds/`, `main_window.py`, `stock_trader.py`)
- **2026-06-18** — **修今日/未实现盈亏闪 0 + 重连后盈亏归 0**。① `request_pnl` 改**幂等**(reqPnL 是流式
  订阅,`account_summary_end` 每 3 秒会调它,原来每次 cancel+重订 → 重订瞬间初值不稳 → 闪 0;现已订阅则直接
  返回)。② **未实现盈亏单一来源**:reqPnL 流接管后,账户摘要里每 3 秒推来的 `UnrealizedPnL`(常为 0/陈旧)
  不再覆盖(`_unrealized_from_stream` 标志;断开时重置以便重连后再作初始回退)。③ 持仓面板 API 期权持仓在
  `reqPnLSingle` 到达前显示「--」而非误导性的 $0.00(新增 `has_pnl` 行标志)。(`ibkr_engine.py`,
  `widgets/account_bar.py`, `widgets/position_panel.py`)
- **2026-06-18** — **持仓一律以 IBKR API 为准 + 撤单类无害响应不再误报**(修"撤单弹拒单框 / 凭空多一个持仓 / 数目不对")。
  ① 错误 **161 / 10147 / 10148**(撤单时订单已不可撤或找不到 —— 多半已成交/已撤)在 `error()` 里静默返回:
  不弹框、不标 ERROR、不写拒单日志(以前会把刚成交的单误显示成"已拒绝")。② **真实引擎 `positions` 属性
  改为返回空**,`_on_execution` 不再本地累加持仓 —— 持仓唯一真相来自 `reqPositions`(`_ibkr_positions`):
  持仓面板用 `portfolio_position_received` + `reqPnLSingle` 渲染(不依赖逐合约行情,Gateway 快照模式也准),
  点价梯摘要改用新方法 `get_position()`,`get_position_qty()` 只读 API。平仓后 reqPositions 推 0 → 自动消失、
  不留残影,也不再有幻影持仓/数目错。模拟引擎 `PaperEngine` 仍用本地撮合持仓(无 API 可用)。
  (`ibkr_engine.py`, `paper_engine.py`, `widgets/price_ladder.py`)
- **2026-06-18** — **期权链报价改一次性快照 + 新增「🔄 刷新报价」按钮(修 Gateway 下整条链/TSLA 无数据)**。
  根因:Gateway 行情线收紧到 45,期权链原本对每个行权价持续流式订阅 → 线不够 → 全 "—"(TSLA 尤甚)。
  改为:引擎新增 `snapshot_option_tick`(`reqMktData snapshot=True`,IBKR 推一次 bid/ask/last/vol 后自动
  取消,**不占常驻线**)+ `tickSnapshotEnd` 清理映射;期权链切 Tab / 点按钮各拉一次快照,显示定时器只读
  缓存不发请求(无持续压力)。顺带:错误 **300**(cancelMktData 找不到 tickerId,快照自动取消后常见)
  改为静默 + 清理,不再刷状态栏。`PaperEngine` 加 `snapshot_option_tick` 委托。
  (`ibkr_engine.py`, `paper_engine.py`, `widgets/option_chain.py`)
- **2026-06-18** — **正股 client 补上 Gateway 版入口 + 两个桌面快捷方式改指 Gateway 新版**。新增
  `stock_trader_gw.py`(设 `IBKR_USE_GATEWAY=1`、独立文件名故不杀运行中的 `stock_trader.py`、日志写
  `stock_app_gw_*.log`、标题加 [GW])与 `start_stock_gateway.bat`,与 `main_gw.py` 对称。桌面
  「IBKR 点价交易」→ `main_gw.py`、「IBKR 正股交易」→ `stock_trader_gw.py`(图标/工作目录不变)。
  旧 TWS 版入口与 bat 全部保留。(`stock_trader_gw.py`, `start_stock_gateway.bat`, 桌面 .lnk)
- **2026-06-18** — **期权 GUI + 正股 client 都新增「各币种现金余额」显示**。引擎用 `reqAccountSummary
  "$LEDGER:ALL"` 拿到每个币种的 `CashBalance`(新信号 `currency_balance_updated`/`currency_balances_end`、
  方法 `request/cancel_currency_balances`,独立于普通账户摘要订阅);新增复用控件
  `widgets/currency_balance.py`(`CurrencyBalanceBar`,显示 `币种: EUR €414.00  USD $0.00`,非零排前、
  含 0 也显示)。期权 GUI 内嵌进 `AccountBar`(随 3s 刷新),正股 client 放顶栏(连接时请求);
  `PaperEngine` 给出模拟 USD 余额以保持接口一致。直接定位"有欧元没美元"导致买美元期权被拒的根因。
  (`ibkr_engine.py`, `paper_engine.py`, `widgets/currency_balance.py`, `widgets/account_bar.py`,
  `main_window.py`, `stock_trader.py`)
- **2026-06-18** — **新增 Gateway 版入口 `main_gw.py` + `start_gateway.bat`(解决 TWS 频繁崩溃)**。
  根因诊断:TWS 是重型 Java GUI,期权 GUI(10)+正股 GUI(11)各订 ~62 行行情、合计超 ~100 行账户
  上限,叠加界面渲染易卡死/崩溃。新版改动:① `config.py` 加 `USE_GATEWAY`(环境变量
  `IBKR_USE_GATEWAY=1`)开关,开则连 IB Gateway(4001 live / 4002 paper)且收紧订阅
  (`MAX_SIMULTANEOUS_STREAMS` 95→45, `CHAIN_STRIKES_AROUND_ATM` 15→10,两个 GUI 合计 <100 行);
  ② `ibkr_engine.py` 端口选择按 `USE_GATEWAY` 路由;③ `option_chain.py` 用 `CHAIN_STRIKES_AROUND_ATM`;
  ④ 新入口 `main_gw.py` 文件名独立 → `kill_previous_instances` **不会杀运行中的 `main.py`(旧版)**,
  新旧可并存对比;日志写 `app_gw_*.log`,窗口标题加 `[GW 新版]`。**旧版完全不受影响**
  (`start.bat`/`main.py` 不设环境变量 → TWS + 95/15,字节级行为不变)。
  **顺带修 bug**:`config.py` 的 GW 端口常量原先写反(paper=4001/live=4002),已按 IB 默认改正
  (live=4001/paper=4002);README §7 自相矛盾的端口说明一并修正。
  (`config.py`, `ibkr_engine.py`, `combo_analyzer.py`, `widgets/option_chain.py`, `main_gw.py`,
  `start_gateway.bat`)
- **2026-06-18** — 组合分析器加**连接模式下拉**(IBKR模拟盘 7497 默认 / 实盘 7496):此前写死 LIVE
  会直连实盘真钱;改为默认连模拟盘, 可端到端测试组合下单而不动真钱, 选实盘弹确认。(`combo_analyzer.py`)
- **2026-06-18** — **新增「IBKR模拟盘」模式,可连模拟账户真实测试下单**。原「Paper」是本地撮合、不发单;
  新增第三模式 `TradingMode.IBKR_PAPER`,走真实 `IBKREngine` 但连 7497 端口,把订单真实提交到 IBKR
  模拟账户,端到端验证 placeOrder→TWS→成交回报。顶栏模式下拉改三项(本地模拟/IBKR模拟盘/实盘,
  item data 存 `TradingMode.value`);端口/引擎判定用新属性 `is_live_port`/`uses_ibkr_engine`;
  状态栏统一用 `mode.label`。(`models.py`, `ibkr_engine.py`, `main_window.py`, `widgets/symbol_bar.py`)
- **2026-06-18** — 组合分析器加「**今日合并K线**」按钮:拉各腿当日 OHLC 合并成组合蜡烛图
  (`compute_combo_ohlc`, 空头腿反向贡献高/低), 复用 `CandlestickItem`, 可滚轮缩放/拖动;
  绘图统一改为索引 x 轴 + 自定义时间刻度(折线与蜡烛共用)。(`combo_analyzer.py`, `widgets/combo_pricing.py`)
- **2026-06-18** — 组合分析器:加载链/选好腿后, 腿表「最新价」与组合净价**持续实时刷新**
  (自动订阅各腿行情, 不必先点「计算/录制」), 修复「加载链后表格看起来空着」的困惑。(`combo_analyzer.py`)
- **2026-06-18** — 新增诊断脚本 `check_option_history.py`(clientId=99, 只读):实测账户是否有
  「期权历史数据」权限(请求 SPY 当日 1 分钟 bar, 试 TRADES/MIDPOINT, 打印根数或错码)。(`check_option_history.py`)
- **2026-06-18** — 组合分析器加**当日实时录制**:每 2 秒用各腿实时盘口中价合成组合净价累积成当日
  曲线, 不依赖历史行情权限 (只需实时行情), 适合「只看当日」。(`combo_analyzer.py`)
- **2026-06-18** — **期权计算器改两列 + 新增反向求标的价**。右列为 what-if 求解器:可改 K/IV/r/到期天数与
  **目标期权价**,用 BS 价对 S 单调的性质做括弧二分(`solve_underlying_for_price`)反推「期权要值目标价时
  标的需到的价位」,并对比当前标的显示需变动金额/%。换合约自动以实时值播种一次,「↺ 用实时值填充」可重置;
  Put 目标价超上界显示「无解」。左列保持原实时正向理论价不变。(`widgets/option_calculator.py`, `README.md`)
- **2026-06-18** — **修复持仓数量莫名变成 -1(幻影空头)**。本程序**只做多期权、从不做空**,但真实引擎
  `_on_execution` 有两个漏洞会建出负持仓:① 卖出超量/重复成交时 `new_qty == 0` 判断让数量穿到负数仍保留;
  ② 卖出一个本地未跟踪的合约(如 `reqPositions` 尚未回来的竞态)会在 `else` 分支直接建出 `-qty`。
  改为:`new_qty <= 0` 一律删除持仓;未跟踪键仅在 `fill_qty > 0`(买入/加仓)时才建仓,卖出不建幻影。
  与上一条的 `_ibkr_positions` seed 合起来,卖出旧持仓会正确归零而非变 -1。(`ibkr_engine.py`)
- **2026-06-18** — **修复重大 bug:崩溃/重启后上一会话的期权持仓卖不掉**。真实模式下 `IBKREngine._positions`
  仅由**本会话成交**填充,而崩溃前开的仓位重启后只经 `reqPositions()` 进到面板的 `_portfolio_positions`,
  引擎并不知道 → 「平仓」按钮报“无持仓可平”、面板里这类持仓双击也打不开点价梯(`option=None`)、
  即便卖出 `_on_execution` 还会建出幻影空头(-qty)。修复:引擎新增 `_ibkr_positions` 缓存 `reqPositions`
  的真实期权持仓;`close_position`/`get_position_qty` 回退到该缓存;`_on_execution` 首次成交前先以真实
  持仓做基线 seed(避免幻影空头);持仓面板为 OPT 组合持仓构造 `OptionInfo` 使双击可开梯平仓。
  (`ibkr_engine.py`, `widgets/position_panel.py`)
- **2026-06-18** — **新增期权组合分析器** `combo_analyzer.py`(独立程序, clientId=12):多腿组合
  (蝶式/铁鹰/跨式/垂直/日历...)即时净价 + 由各腿历史价合成**组合历史价曲线**(券商不提供);
  并支持 IBKR 原生 BAG **组合原子交易** —— 持仓作为整体,只能整组平仓/加仓,**不可单腿**,
  分组本地持久化(`combo_positions.json`)。新增引擎方法 `request_option_historical_data`、
  纯逻辑 `widgets/combo_pricing.py`、`start_combo.bat`、config `IBKR_COMBO_CLIENT_ID=12`。
  (`combo_analyzer.py`, `widgets/combo_pricing.py`, `ibkr_engine.py`, `config.py`, `start_combo.bat`)
- **2026-06-18** — **计算器理论价更新提速**:标的价 S 改为优先读高频的 `__stock__SYM` tick
  (随每笔成交跳动),模型 `undPrice`(每几秒才更新)仅作回退;刷新间隔 700ms→300ms。
  解决理论价随标的变动反应迟钝的问题。(`widgets/option_calculator.py`, `config.py`)
- **2026-06-18** — **模拟模式今日盈亏/未实现盈亏/净值计入手续费**:`_calc_unrealized_pnl` 改用 `net_pnl`
  (= 毛利 − 未平仓持仓累计佣金),修复模拟模式今日盈亏漏算开仓手续费的问题。~~真实模式不变
  (IBKR 的 dailyPnL 已含佣金...)~~ **← 此结论后被实盘实测推翻:reqPnL dailyPnL 未含手续费,
  见上方今日盈亏扣费那条。**
  (`paper_engine.py`)
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
