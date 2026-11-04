@echo off
rem Starts Kataribe. The console is minimized; closing it ends the app. (Kataribe を起動する。コンソールを閉じるとアプリも終わります)
cd /d "%~dp0"
where python >nul 2>nul && (start "Kataribe" /min python server.py & exit /b 0)
where py >nul 2>nul && (start "Kataribe" /min py -3 server.py & exit /b 0)
echo Python 3 was not found. Install it from https://www.python.org/ (Python 3 が見つかりません)
pause
