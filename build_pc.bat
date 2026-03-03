@echo off
REM Build AdoptIQ as a Windows .exe for distribution.
REM Run this script ON WINDOWS. Requires: Python 3, pip, PyInstaller.

setlocal
cd /d "%~dp0"

set ADOPTIQ_VERSION=1.0.2
set ADOPTIQ_BUILD=1

echo ==============================================
echo   AdoptIQ - Build Windows .exe
echo ==============================================
echo.

REM Use venv if present
set PYTHON=python
set PIP=pip
if exist venv\Scripts\python.exe (
    set PYTHON=venv\Scripts\python.exe
    set PIP=venv\Scripts\pip.exe
    echo Using venv: %PYTHON%
)

REM Ensure dependencies
echo Ensuring PyInstaller and dependencies...
"%PIP%" install -q -r requirements.txt
"%PIP%" install -q pyinstaller

echo.
echo Embedding configuration...
if not exist secrets.env (
    if exist secrets.env.template (
        copy secrets.env.template secrets.env 1>nul 2>nul
        echo   -^> Created secrets.env from template. Add credentials for full build.
    )
)
"%PYTHON%" embed_credentials.py 1>nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo   -^> Configuration embedded from secrets.env
) else (
    echo def get_secrets^(^): return {} > _bundled_secrets.py
    echo   -^> No configuration; created empty stub
    echo   -^> IMPORTANT: Add SNOWFLAKE/Keeper credentials in secrets.env and re-run embed_credentials.py before building.
)

echo.
echo Updating version/build in config.py...
set ADOPTIQ_VERSION=%ADOPTIQ_VERSION%
set ADOPTIQ_BUILD=%ADOPTIQ_BUILD%
"%PYTHON%" update_version_pc.py
echo   -^> Version %ADOPTIQ_VERSION% (Build %ADOPTIQ_BUILD%)

echo.
echo Running PyInstaller (this may take a few minutes)...
"%PYTHON%" -m PyInstaller --clean --noconfirm adoptiq_pc.spec

if not exist dist\AdoptIQ.exe (
    echo Build failed: dist\AdoptIQ.exe not found.
    pause
    exit /b 1
)

REM Create OUTBOX with standalone exe, readme, and build info
echo.
echo Creating OUTBOX...
if not exist OUTBOX mkdir OUTBOX
copy /Y dist\AdoptIQ.exe OUTBOX\
copy README.md OUTBOX\
echo AdoptIQ v%ADOPTIQ_VERSION% build %ADOPTIQ_BUILD% > OUTBOX\build_info.txt
echo Built: %date% %time% >> OUTBOX\build_info.txt

REM Unblock built files so SmartScreen allows the app
powershell -NoProfile -Command "Get-ChildItem -Path OUTBOX -File | Unblock-File -ErrorAction SilentlyContinue"

echo.
echo ==============================================
echo   Done
echo ==============================================
echo.
echo   OUTBOX\AdoptIQ.exe  - Standalone exe: double-click to run
echo   OUTBOX\README.md   - User instructions
echo.
echo   User double-clicks AdoptIQ.exe; browser opens automatically to http://localhost:5001
echo.
pause
