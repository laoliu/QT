@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions

cd /d "%~dp0\.."

:: =========================================================================
:: QT - miniQMT launcher + Data Bridge service
:: =========================================================================
:: Steps:
::   1. Start XtMiniQmt.exe (if not running)
::   2. Wait for user to enter verification code and login
::   3. Verify miniQMT connection is ready (tradeable state)
::   4. Start QMT Data Bridge HTTP service (port 18888)
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
        if "%%A"=="PYTHON_BIN" set "BRIDGE_PYTHON=%%B"
    )
)

:: --- Defaults (used when .env is not set) ---
if not defined MINI_QMT_PATH set "MINI_QMT_PATH=D:\st_lc\userdata_mini"
if not defined QMT_INSTALL_DIR set "QMT_INSTALL_DIR=D:\st_lc"
if not defined BRIDGE_PORT set "BRIDGE_PORT=18888"
if not defined BRIDGE_HOST set "BRIDGE_HOST=0.0.0.0"
if not defined BRIDGE_PYTHON set "BRIDGE_PYTHON=python"

:: --- Derived paths ---
set "QMT_EXE=%QMT_INSTALL_DIR%\bin.x64\XtMiniQmt.exe"
set "BRIDGE_SCRIPT=%~dp0..\quant-trading\python\strategies\qmt_data_bridge.py"

echo ===================================================
echo   QT - QMT Data Bridge Launcher
echo ===================================================
echo   QMT_INSTALL_DIR = %QMT_INSTALL_DIR%
echo   MINI_QMT_PATH   = %MINI_QMT_PATH%
echo   QMT_EXE         = %QMT_EXE%
echo   Python          = %BRIDGE_PYTHON%
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
if /I "%BRIDGE_PYTHON%"=="python" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found on PATH. Set PYTHON_BIN in .env.
        pause
        exit /b 1
    )
) else if not exist "%BRIDGE_PYTHON%" (
    echo [ERROR] Python interpreter not found: %BRIDGE_PYTHON%
    echo Please correct PYTHON_BIN in .env.
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
set "XTDATA_OK=0"
call :check_xtdata 1
if not errorlevel 1 goto xtdata_check_done
timeout /t 3 /nobreak >nul
call :check_xtdata 2
if not errorlevel 1 goto xtdata_check_done
timeout /t 3 /nobreak >nul
call :check_xtdata 3
if not errorlevel 1 goto xtdata_check_done
timeout /t 3 /nobreak >nul
call :check_xtdata 4
if not errorlevel 1 goto xtdata_check_done
timeout /t 3 /nobreak >nul
call :check_xtdata 5

:xtdata_check_done
if "%XTDATA_OK%"=="0" (
    echo [WARN] xtdata could not connect using the configured Python interpreter.
    echo        The HTTP bridge will still start, but market-data endpoints may be unavailable.
    echo        Check that the QMT market-data service is running, not only the trading service.
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
echo [INFO] Checking TCP listener on port %BRIDGE_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clear_qmt_bridge_port.ps1" -Port %BRIDGE_PORT%
if errorlevel 1 (
    echo [ERROR] Could not clear the QMT bridge port. See the message above.
    pause
    exit /b 1
)

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

"%BRIDGE_PYTHON%" "%BRIDGE_SCRIPT%" %BRIDGE_ARGS%

echo.
echo Bridge stopped.
pause
goto :eof

:check_xtdata
echo [INFO] xtdata attempt %1/5...
"%BRIDGE_PYTHON%" -c "from xtquant import xtdata; xtdata.connect(); codes=xtdata.get_stock_list_in_sector('沪深A股'); print(f'[OK] xtdata connected, {len(codes)} stocks available'); assert len(codes)>0"
if errorlevel 1 exit /b 1
set "XTDATA_OK=1"
exit /b 0
