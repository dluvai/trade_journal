@echo off
cd /d "%~dp0"
echo Starting SteadFast server...
echo.
echo Once it says "Running on http://127.0.0.1:5151", open this in your browser:
echo     http://felixjournal.local:5151
echo (or http://localhost:5151 if that name isn't set up)
echo.
echo Leave this window open while you use the dashboard. Close it to stop the server.
echo.
python3 server.py
pause
