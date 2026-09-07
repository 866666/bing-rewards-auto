@echo off
REM rewards_daily.bat - 手动运行 rewards_daily.py（带终端显示）
REM 用法：双击或在命令行运行
REM 解释器：%BING_REWARDS_PYTHON%（--setup 写入，自动 pythonw->python 以显示输出），否则 PATH 上 python.exe
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
if defined BING_REWARDS_PYTHON set "PY=%BING_REWARDS_PYTHON:pythonw=python%"
if not defined PY set PY=python.exe
"%PY%" rewards_daily.py %*