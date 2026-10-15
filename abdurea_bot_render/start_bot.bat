@echo off
title ABDUREA STORE - Dual Telegram Bot System
echo ========================================================
echo   ABDUREA STORE - MULTI-BOT SYSTEM
echo   Customer Bot: @AbdureaStoreBot
echo   Admin Bot:    @trust994 (ID: 6666443909)
echo ========================================================
echo.

set PYTHON_CMD="C:\Users\SolNet\AppData\Local\Programs\Python\Python311\python.exe"

:loop
if exist %PYTHON_CMD% (
    %PYTHON_CMD% run.py --all
) else (
    python run.py --all
)

echo.
echo [!] Process stopped. Restarting in 5 seconds... Press Ctrl+C to cancel.
timeout /t 5 /nobreak >nul
goto loop
