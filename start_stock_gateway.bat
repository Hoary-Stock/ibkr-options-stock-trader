@echo off
REM 正股 client 新版本 (IB Gateway + 轻量行情订阅) 启动脚本。
REM 连 IB Gateway: 4002=实盘, 4001=模拟盘。需先启动并登录 IB Gateway。
REM 与旧版 start_stock.bat (stock_trader.py / TWS) 互不干扰, 可同时运行。
cd /d "%~dp0"
start "" pythonw stock_trader_gw.py
