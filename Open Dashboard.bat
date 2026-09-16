@echo off
title Real Madrid ^& Manchester United
cd /d "%~dp0"
python main.py
if errorlevel 1 (
  echo.
  echo Could not start. Check that Python 3 is installed and on your PATH.
  pause
)
