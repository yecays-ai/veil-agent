@echo off
chcp 65001 >nul
cd /d %~dp0
if exist E:\AI\Python312\python.exe (E:\AI\Python312\python.exe verify_isolation.py) else (python verify_isolation.py)
