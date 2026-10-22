@echo off
REM SmokeOS server for Windows. Keeps the server running: if it stops
REM (for example after installing an update) it starts again in 5 seconds.
cd /d "%~dp0"
:loop
py -3 smokeos_server.py run
echo SmokeOS server stopped - restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
