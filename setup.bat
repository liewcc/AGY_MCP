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

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [2/3] Checking for the Antigravity CLI...
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" (
    echo   Found: %LOCALAPPDATA%\agy\bin\agy.exe
) else (
    echo   WARNING: agy.exe not found at %LOCALAPPDATA%\agy\bin\agy.exe
    echo   Install the Antigravity CLI and log in before using AGY MCP.
)

echo.
echo [3/3] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\AGY MCP.lnk'); $lnk.TargetPath = '%~dp0run.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0img\logo.ico'; $lnk.Save()"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Could not create desktop shortcut.
) else (
    echo   Shortcut created: Desktop\AGY MCP.lnk
)

echo.
echo [4/4] Registering with Claude Code...
where claude >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo   WARNING: claude CLI not found in PATH.
    echo   Run this command manually after installing Claude Code:
    echo     claude mcp add agy-mcp -- python "%~dp0server.py"
    goto :done
)

set /p REGISTER_MCP="  Register agy-mcp with Claude Code now? (y/n): "
if /i not "%REGISTER_MCP%"=="y" (
    echo   Skipped. Run manually when ready:
    echo     claude mcp add agy-mcp -- python "%~dp0server.py"
    goto :done
)

claude mcp add agy-mcp -- python "%~dp0server.py"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Registration failed. Try running manually:
    echo     claude mcp add agy-mcp -- python "%~dp0server.py"
    goto :done
)

echo.
echo   Verifying registration...
claude mcp list | findstr "agy-mcp" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] agy-mcp registered successfully.
) else (
    echo   WARNING: Could not verify registration. Check with: claude mcp list
)

:done
echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Double-click "AGY MCP" on your desktop to open the control panel.
echo  2. In Claude Code, say: "继续：根据 SETUP.md 第二步，执行集成测试"
echo ============================================
echo.
pause
