@echo off
REM 宏观行情监控 (美债利率/原油/金银) — IB Gateway 版。
REM 连 IB Gateway: 4001=实盘, 4002=模拟盘。需先启动并登录 IB Gateway。
REM 只读行情、不下单; 独立 clientId=13。
cd /d "%~dp0"
set IBKR_USE_GATEWAY=1
start "" pythonw macro_monitor.py
