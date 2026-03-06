@echo off
REM Build AdoptIQ as a Windows .exe for distribution.
REM Run this script ON WINDOWS. Requires: Python 3, pip, PyInstaller.

setlocal
cd /d "%~dp0"

set ADOPTIQ_VERSION=1.0.3
set ADOPTIQ_BUILD=1
set STAGING_DIR=C:\Users\jestory\OneDrive - Cisco\AI Projects\Staging\AdoptIQ_PC

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
"%PYTHON%" embed_credentials.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: embed_credentials.py failed. Cannot build without credentials.
    echo        Ensure secrets.env exists and contains required values, then re-run.
    pause
    exit /b 1
)
echo   -^> Configuration embedded from secrets.env

echo.
echo Updating version/build in config.py...
set ADOPTIQ_VERSION=%ADOPTIQ_VERSION%
set ADOPTIQ_BUILD=%ADOPTIQ_BUILD%
"%PYTHON%" update_version_pc.py
echo   -^> Version %ADOPTIQ_VERSION% (Build %ADOPTIQ_BUILD%)

echo.
echo Running PyInstaller (this may take a few minutes)...
if exist build\adoptiq_pc rmdir /S /Q build\adoptiq_pc
if exist dist\AdoptIQ.exe del /F /Q dist\AdoptIQ.exe
"%PYTHON%" -m PyInstaller --clean --noconfirm adoptiq_pc.spec
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: PyInstaller failed.
    pause
    exit /b 1
)

if not exist dist\AdoptIQ.exe (
    echo Build failed: dist\AdoptIQ.exe not found.
    pause
    exit /b 1
)

REM Create OUTBOX with standalone exe, readme, and build info
echo.
echo Creating OUTBOX...
if not exist OUTBOX mkdir OUTBOX

REM Keep OUTBOX deterministic: exactly three files.
for %%F in (OUTBOX\*) do (
    if /I not "%%~nxF"=="AdoptIQ.exe" if /I not "%%~nxF"=="README.md" if /I not "%%~nxF"=="build_info.txt" del /Q "%%~fF" >nul 2>nul
)

copy /Y dist\AdoptIQ.exe "OUTBOX\AdoptIQ.exe" >nul
copy /Y README.md "OUTBOX\README.md" >nul
echo AdoptIQ v%ADOPTIQ_VERSION% build %ADOPTIQ_BUILD% > OUTBOX\build_info.txt
echo Built: %date% %time% >> OUTBOX\build_info.txt

REM Mirror release payload to staging folder with the same 3-file contract.
echo Syncing staging folder...
if not exist "%STAGING_DIR%" mkdir "%STAGING_DIR%"
for %%F in ("%STAGING_DIR%\*") do (
    if /I not "%%~nxF"=="AdoptIQ.exe" if /I not "%%~nxF"=="README.md" if /I not "%%~nxF"=="build_info.txt" del /Q "%%~fF" >nul 2>nul
)
copy /Y "OUTBOX\AdoptIQ.exe" "%STAGING_DIR%\AdoptIQ.exe" >nul
copy /Y "OUTBOX\README.md" "%STAGING_DIR%\README.md" >nul
copy /Y "OUTBOX\build_info.txt" "%STAGING_DIR%\build_info.txt" >nul

REM Unblock built files so SmartScreen allows the app
powershell -NoProfile -Command "Get-ChildItem -Path OUTBOX -File | Unblock-File -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "Get-ChildItem -Path \"%STAGING_DIR%\" -File | Unblock-File -ErrorAction SilentlyContinue"

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
