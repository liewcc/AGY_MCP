@echo off
REM Check if agy CLI is already installed
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" (
    echo [OK] agy CLI found at %LOCALAPPDATA%\agy\bin\agy.exe
    goto :pip_install
)

echo [!] agy CLI not found.
set /p INSTALL_AGY="Install agy CLI now? (y/n): "
if /i "%INSTALL_AGY%"=="y" (
    echo Installing agy CLI...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://antigravity.google/cli/install.ps1 | iex"
    if not exist "%LOCALAPPDATA%\agy\bin\agy.exe" (
        echo [ERROR] Installation failed. Please install manually: https://antigravity.google/download#antigravity-cli
        exit /b 1
    )
    echo [OK] agy CLI installed successfully.
) else (
    echo [SKIP] Skipping agy CLI installation.
    echo        Install manually later: https://antigravity.google/download#antigravity-cli
)

:pip_install

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
