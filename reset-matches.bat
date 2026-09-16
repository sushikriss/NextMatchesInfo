@echo off
setlocal
title Keep the match dashboard alive

rem ==========================================================================
rem  reset-matches.bat  -  keep the football dashboard updating
rem
rem  GitHub switches off a scheduled workflow after 60 days with no push to the
rem  repository, and the dashboard then quietly stops refreshing.
rem
rem  Run this every month or two. It pushes one empty commit, which resets the
rem  60-day clock and rebuilds the page straight away. If the workflow has
rem  already been switched off - you left it longer than 60 days - it turns it
rem  back on first, because a push alone cannot revive it.
rem
rem  Running it more often than needed does no harm.
rem ==========================================================================

set "REPO=C:\Users\kriss\Desktop\BATUDA\TeamsScriptProgramWtf"
set "SITE=https://sushikriss.github.io/NextMatchesInfo/"

echo.
echo   Keeping the match dashboard alive
echo   ---------------------------------
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo   [X] Git is not installed, or is not on your PATH.
  echo       Get it from https://git-scm.com/download/win then run this again.
  goto failed
)

where python >nul 2>&1
if errorlevel 1 (
  echo   [X] Python is not installed, or is not on your PATH.
  echo       Get it from https://www.python.org/downloads/ then run this again.
  goto failed
)

if not exist "%REPO%\keepalive.py" (
  echo   [X] Could not find the project at:
  echo       %REPO%
  echo.
  echo       If you moved the folder, open this file in Notepad and change the
  echo       REPO line near the top to wherever it lives now.
  goto failed
)

cd /d "%REPO%"
if errorlevel 1 (
  echo   [X] Could not open the project folder.
  goto failed
)

python keepalive.py
if errorlevel 1 goto failed

echo.
echo   [OK] Done - the 60-day clock is reset.
echo.
echo        GitHub is rebuilding the page now. Give it a minute or two:
echo        %SITE%
echo.
echo   Closing in 12 seconds...
"%SystemRoot%\System32\timeout.exe" /t 12 /nobreak >nul 2>&1
exit /b 0

:failed
echo.
echo   Something went wrong - see the message above.
echo   Press any key to close.
pause >nul
exit /b 1
