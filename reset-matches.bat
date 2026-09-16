@echo off
setlocal

rem ==========================================================================
rem  reset-matches.bat  -  keep the football dashboard updating
rem
rem  GitHub switches off a scheduled workflow after 60 days with no push to the
rem  repository, and the dashboard then quietly stops refreshing.
rem
rem  This pushes one empty commit. That resets the 60-day clock and, because
rem  the workflow also runs on every push, rebuilds the page straight away.
rem  An empty commit keeps nothing junk in the history.
rem
rem  Run it every month or two. Running it more often does no harm.
rem ==========================================================================

set "REPO=C:\Users\kriss\Desktop\BATUDA\TeamsScriptProgramWtf"
set "SITE=https://sushikriss.github.io/NextMatchesInfo/"
set "ACTIONS=https://github.com/sushikriss/NextMatchesInfo/actions"

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

if not exist "%REPO%\.git" (
  echo   [X] No git repository found at:
  echo       %REPO%
  echo.
  echo       If you moved the project folder, open this .bat in Notepad and
  echo       change the REPO line near the top to the new location.
  goto failed
)

cd /d "%REPO%"
if errorlevel 1 (
  echo   [X] Could not open the project folder.
  goto failed
)

echo   [1/3] Catching up with GitHub...
git pull --rebase --autostash --quiet
if errorlevel 1 (
  echo         Could not pull. Carrying on - the push will report the real problem.
)

echo   [2/3] Making a commit to reset the 60-day timer...
git commit --allow-empty --quiet -m "Keep the scheduled rebuild alive"
if errorlevel 1 (
  echo   [X] Could not create the commit.
  goto failed
)

echo   [3/3] Pushing to GitHub...
git push --quiet origin main
if errorlevel 1 (
  echo.
  echo   [X] The push failed, so the timer was NOT reset.
  echo       Most often Git just needs you to sign in to GitHub again.
  echo       To see the real reason, open a terminal in the project folder
  echo       and run:  git push origin main
  goto failed
)

echo.
echo   [OK] Done - the 60-day clock is reset.
echo.
echo        GitHub is rebuilding the page now. Give it a minute or two:
echo        %SITE%
echo.
echo   If the page still says it has stopped rebuilding, then the workflow
echo   had already been switched off. A push cannot restart it - open this
echo   and press "Enable workflow":
echo        %ACTIONS%
echo.
echo   Closing in 12 seconds...
"%SystemRoot%\System32\timeout.exe" /t 12 /nobreak >nul 2>&1
exit /b 0

:failed
echo.
echo   Nothing was pushed. Press any key to close.
pause >nul
exit /b 1
