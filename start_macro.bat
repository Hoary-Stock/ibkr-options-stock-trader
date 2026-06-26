@echo off
REM 宏观行情监控 (美债利率/原油/金银) — TWS 版。
REM 连 TWS: 7496=实盘, 7497=模拟盘。只读行情、不下单; 独立 clientId=13。
cd /d "%~dp0"
start "" pythonw macro_monitor.py
