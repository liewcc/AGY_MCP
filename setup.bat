@echo off
setlocal enabledelayedexpansion
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

echo [1/5] Installing Python dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed.
    echo Make sure Python 3.10+ is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [2/5] Checking for the Antigravity CLI...
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" (
    echo   Found: %LOCALAPPDATA%\agy\bin\agy.exe
) else (
    echo   WARNING: agy.exe not found at %LOCALAPPDATA%\agy\bin\agy.exe
    echo   Install the Antigravity CLI and log in before using AGY MCP.
)

echo.
echo [3/5] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\AGY MCP.lnk'); $lnk.TargetPath = '%~dp0run.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0img\logo.ico'; $lnk.Save()"
if %ERRORLEVEL% neq 0 (
    echo   WARNING: Could not create desktop shortcut.
) else (
    echo   Shortcut created: Desktop\AGY MCP.lnk
)

echo.
echo [4/5] Registering with Claude Code...
set "SERVER_PATH=%~dp0server.py"
set "FIND_CLAUDE_PY=%TEMP%\find_claude.py"
> "%FIND_CLAUDE_PY%" echo import os, glob, json, sys
>> "%FIND_CLAUDE_PY%" echo cands = glob.glob^(os.path.expandvars^(r'%%LOCALAPPDATA%%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json'^)^)
>> "%FIND_CLAUDE_PY%" echo p2 = os.path.expandvars^(r'%%APPDATA%%\Claude\claude_desktop_config.json'^)
>> "%FIND_CLAUDE_PY%" echo if os.path.exists^(p2^): cands.append^(p2^)
>> "%FIND_CLAUDE_PY%" echo if not cands: sys.exit^(2^)
>> "%FIND_CLAUDE_PY%" echo cfg = cands[0]
>> "%FIND_CLAUDE_PY%" echo if len^(sys.argv^) ^> 1 and sys.argv[1] == '--check':
>> "%FIND_CLAUDE_PY%" echo     try: d = json.load^(open^(cfg, encoding='utf-8'^)^)
>> "%FIND_CLAUDE_PY%" echo     except Exception: sys.exit^(3^)
>> "%FIND_CLAUDE_PY%" echo     ms = d.get^('mcpServers', {}^)
>> "%FIND_CLAUDE_PY%" echo     sys.exit^(0 if ^('agy' in ms or 'agy-mcp' in ms^) else 1^)
>> "%FIND_CLAUDE_PY%" echo if len^(sys.argv^) ^> 1 and sys.argv[1] == '--write':
>> "%FIND_CLAUDE_PY%" echo     server = os.environ['SERVER_PATH']
>> "%FIND_CLAUDE_PY%" echo     d = json.load^(open^(cfg, encoding='utf-8'^)^)
>> "%FIND_CLAUDE_PY%" echo     d.setdefault^('mcpServers', {}^)['agy-mcp'] = {'command': 'python', 'args': [server]}
>> "%FIND_CLAUDE_PY%" echo     json.dump^(d, open^(cfg, 'w', encoding='utf-8'^), indent=2^)
>> "%FIND_CLAUDE_PY%" echo     sys.exit^(0^)
>> "%FIND_CLAUDE_PY%" echo print^(cfg^)

python "%FIND_CLAUDE_PY%" --check >nul 2>&1
set "CLAUDE_RC=!ERRORLEVEL!"
if "!CLAUDE_RC!"=="2" (
    echo   WARNING: Claude config not found. Run manually:
    echo     claude mcp add agy-mcp -- python "%~dp0server.py"
    del "%FIND_CLAUDE_PY%" >nul 2>&1
    goto :register_antigravity
)
if "!CLAUDE_RC!"=="0" (
    echo   [OK] agy-mcp already registered with Claude Code.
    del "%FIND_CLAUDE_PY%" >nul 2>&1
    goto :register_antigravity
)

for /f "delims=" %%P in ('python "%FIND_CLAUDE_PY%"') do set "CLAUDE_CFG=%%P"
echo   Found config: !CLAUDE_CFG!
set /p REGISTER_MCP="  Register agy-mcp with Claude Code now? (y/n): "
if /i not "!REGISTER_MCP!"=="y" (
    echo   Skipped.
    del "%FIND_CLAUDE_PY%" >nul 2>&1
    goto :register_antigravity
)

python "%FIND_CLAUDE_PY%" --write
del "%FIND_CLAUDE_PY%" >nul 2>&1
echo   [OK] agy-mcp registered with Claude Code.

:register_antigravity
echo.
echo [5/5] Registering with Antigravity...
set "AGY_FOUND="
if exist "%LOCALAPPDATA%\agy\bin\agy.exe"                              set "AGY_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe"         set "AGY_FOUND=1"
if exist "%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe" set "AGY_FOUND=1"

if not defined AGY_FOUND (
    echo   NOTE: No Antigravity product found. If you install one later,
    echo   add this to %%USERPROFILE%%\.gemini\config\mcp_config.json:
    echo   { "mcpServers": { "agy-mcp": { "command": "python", "args": ["%~dp0server.py"] } } }
    goto :done
)

python -c "import json,os; p=os.path.expandvars(r'%%USERPROFILE%%/.gemini/config/mcp_config.json'); exit(0 if os.path.exists(p) and 'agy-mcp' in json.load(open(p,encoding='utf-8')).get('mcpServers',{}) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] agy-mcp already registered with Antigravity.
    goto :done
)

set /p REGISTER_AGY="  Register agy-mcp with Antigravity now? (y/n): "
if /i not "%REGISTER_AGY%"=="y" (
    echo   Skipped.
    goto :done
)

set "MCP_CONFIG_DIR=%USERPROFILE%\.gemini\config"
set "MCP_CONFIG_FILE=%MCP_CONFIG_DIR%\mcp_config.json"
set "SERVER_PY_PATH=%~dp0server.py"
set "TEMP_PY=%TEMP%\update_mcp.py"

if not exist "%MCP_CONFIG_DIR%" mkdir "%MCP_CONFIG_DIR%"

> "%TEMP_PY%" echo import json, os
>> "%TEMP_PY%" echo file_path = r"%MCP_CONFIG_FILE%"
>> "%TEMP_PY%" echo server_path = r"%SERVER_PY_PATH%"
>> "%TEMP_PY%" echo data = {"mcpServers": {}}
>> "%TEMP_PY%" echo if os.path.exists^(file_path^):
>> "%TEMP_PY%" echo     with open^(file_path, "r", encoding="utf-8"^) as f:
>> "%TEMP_PY%" echo         try: data = json.load^(f^)
>> "%TEMP_PY%" echo         except ValueError: pass
>> "%TEMP_PY%" echo if "mcpServers" not in data: data["mcpServers"] = {}
>> "%TEMP_PY%" echo data["mcpServers"]["agy-mcp"] = {"command": "python", "args": [server_path]}
>> "%TEMP_PY%" echo with open^(file_path, "w", encoding="utf-8"^) as f:
>> "%TEMP_PY%" echo     json.dump^(data, f, indent=2^)

python "%TEMP_PY%"
if exist "%TEMP_PY%" del "%TEMP_PY%"

python -c "import json,os; p=os.path.expandvars(r'%%USERPROFILE%%/.gemini/config/mcp_config.json'); exit(0 if os.path.exists(p) and 'agy-mcp' in json.load(open(p,encoding='utf-8')).get('mcpServers',{}) else 1)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   [OK] agy-mcp registered with Antigravity successfully.
) else (
    echo   WARNING: Could not verify. Check %%USERPROFILE%%\.gemini\config\mcp_config.json
)

:done
echo.
echo ============================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Double-click "AGY MCP" on your desktop to open the control panel.
echo  2. In your AI host (Claude Code or Antigravity), paste the smoke test prompt from README section 1.5 to verify the install.
echo ============================================
echo.
pause
