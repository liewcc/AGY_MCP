@echo off
title AGY MCP - Setup
cd /d "%~dp0"

echo ============================================
echo  AGY MCP Setup
echo ============================================
echo.

echo [1/2] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Checking for the Antigravity CLI...
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" (
    echo   Found: %LOCALAPPDATA%\agy\bin\agy.exe
) else (
    echo   WARNING: agy.exe not found at %LOCALAPPDATA%\agy\bin\agy.exe
    echo   Install the Antigravity CLI and log in before using AGY MCP.
)

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Double-click run.bat to open the control panel (TUI).
echo  2. Connect the MCP server to your AI client:
echo       claude mcp add agy-mcp -- python "%~dp0server.py"
echo ============================================
echo.
pause
