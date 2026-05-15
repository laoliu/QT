@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0\.."

:: =========================================================================
:: QT - miniQMT launcher + Data Bridge service
:: =========================================================================
:: Steps:
::   1. Start XtMiniQmt.exe (if not running)
::   2. Wait for user to enter verification code and login
::   3. Verify miniQMT connection is ready (tradeable state)
::   4. Start QMT Data Bridge HTTP service (port 58088)
:: =========================================================================

:: --- Load config from .env ---
set "ENV_FILE=%~dp0..\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if "%%A"=="MINI_QMT_PATH" set "MINI_QMT_PATH=%%B"
        if "%%A"=="QMT_INSTALL_DIR" set "QMT_INSTALL_DIR=%%B"
        if "%%A"=="SERVE_PORT" set "BRIDGE_PORT=%%B"
        if "%%A"=="SERVE_HOST" set "BRIDGE_HOST=%%B"
        if "%%A"=="TRADING_ACCOUNT_ID" set "TRADING_ACCOUNT_ID=%%B"
    )
)

:: --- Defaults (used when .env is not set) ---
if not defined MINI_QMT_PATH set "MINI_QMT_PATH=D:\st_lc\userdata_mini"
if not defined QMT_INSTALL_DIR set "QMT_INSTALL_DIR=D:\st_lc"
if not defined BRIDGE_PORT set "BRIDGE_PORT=58088"
if not defined BRIDGE_HOST set "BRIDGE_HOST=0.0.0.0"

:: --- Derived paths ---
set "QMT_EXE=%QMT_INSTALL_DIR%\bin.x64\XtMiniQmt.exe"
set "QMT_PY=%QMT_INSTALL_DIR%\bin.x64\python.exe"
set "BRIDGE_SCRIPT=%~dp0..\quant-trading\python\strategies\qmt_data_bridge.py"

echo ===================================================
echo   QT - QMT Data Bridge Launcher
echo ===================================================
echo   QMT_INSTALL_DIR = %QMT_INSTALL_DIR%
echo   MINI_QMT_PATH   = %MINI_QMT_PATH%
echo   QMT_EXE         = %QMT_EXE%
echo   Bridge          = %BRIDGE_HOST%:%BRIDGE_PORT%
echo   Script          = %BRIDGE_SCRIPT%
echo ===================================================
echo.

:: --- Step 1: Check XtMiniQmt.exe exists ---
if not exist "%QMT_EXE%" (
    echo [ERROR] XtMiniQmt.exe not found: %QMT_EXE%
    echo Please edit this script and update QMT_INSTALL_DIR, or set it in .env
    pause
    exit /b 1
)

:: --- Step 2: Check if miniQMT is already running ---
tasklist /FI "IMAGENAME eq XtMiniQmt.exe" | find /I "XtMiniQmt.exe" >nul
if errorlevel 1 (
    echo [INFO] XtMiniQmt is not running, starting...
    start "" "%QMT_EXE%"
    echo [INFO] XtMiniQmt.exe launched, waiting 5 seconds for init...
    timeout /t 5 /nobreak >nul
) else (
    echo [INFO] XtMiniQmt is already running.
)

:: --- Step 3: Wait for user to input verification code and confirm login ---
echo.
echo ---------------------------------------------------
echo   Please complete the following steps:
echo     1. Enter the data verification code in miniQMT
echo     2. Wait for miniQMT to show "connected" status
echo     3. Ensure the account is in TRADEABLE state
echo ---------------------------------------------------
echo.
set /p "CONFIRM=Press ENTER after miniQMT login is complete and ready to trade... "

:: --- Step 4: Verify miniQMT process is still running ---
tasklist /FI "IMAGENAME eq XtMiniQmt.exe" | find /I "XtMiniQmt.exe" >nul
if errorlevel 1 (
    echo [ERROR] XtMiniQmt.exe is not running! Login may have failed.
    echo Please restart miniQMT and try again.
    pause
    exit /b 1
)
echo [OK] XtMiniQmt.exe is running.

:: --- Step 5: Quick xtdata connectivity check ---
echo [INFO] Verifying xtdata connectivity...
"%QMT_PY%" -c "from xtquant import xtdata; codes=xtdata.get_stock_list_in_sector('沪深A股'); print(f'[OK] xtdata connected, {len(codes)} stocks available'); assert len(codes)>0" 2>nul
if errorlevel 1 (
    echo [WARN] xtdata connectivity check failed.
    echo        miniQMT may still be initializing. Continuing anyway...
    echo        If bridge fails, restart miniQMT and re-run this script.
    echo.
) else (
    echo [OK] xtdata connectivity verified.
)

:: --- Step 6: Build launch command ---
set "BRIDGE_ARGS=--host %BRIDGE_HOST% --port %BRIDGE_PORT% --qmt-path %QMT_INSTALL_DIR% --mini-path %MINI_QMT_PATH%"
if defined TRADING_ACCOUNT_ID (
    set "BRIDGE_ARGS=%BRIDGE_ARGS% --account %TRADING_ACCOUNT_ID%"
)

:: --- Step 7: Start Data Bridge ---
echo.
echo ===================================================
echo   Starting QMT Data Bridge on %BRIDGE_HOST%:%BRIDGE_PORT%
echo ===================================================
echo.
echo   Health check:  curl http://localhost:%BRIDGE_PORT%/health
echo   K-lines:       POST http://localhost:%BRIDGE_PORT%/klines
echo   Quote:         POST http://localhost:%BRIDGE_PORT%/quote
echo   Trading:       POST http://localhost:%BRIDGE_PORT%/trading
echo   Press Ctrl+C to stop.
echo.

python "%BRIDGE_SCRIPT%" %BRIDGE_ARGS%

echo.
echo Bridge stopped.
pause
