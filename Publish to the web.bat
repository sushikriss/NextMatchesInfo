@echo off
title Publish dashboard
cd /d "%~dp0"
echo Rendering the latest data from ESPN...
echo.
python build.py
if errorlevel 1 (
  echo.
  echo Build failed - see the message above.
  pause
  exit /b 1
)
echo.
echo Opening Netlify Drop and this folder.
echo Drag the "docs" folder onto the page that opens.
echo.
start "" "https://app.netlify.com/drop"
start "" "%~dp0."
timeout /t 5 >nul
