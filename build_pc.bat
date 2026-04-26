@echo off
REM Build AdoptIQ as a Windows .exe for distribution.
REM Run this script ON WINDOWS. Requires: Python 3, pip, PyInstaller.

setlocal
cd /d "%~dp0"

set ADOPTIQ_VERSION=1.0.3
set ADOPTIQ_BUILD=1
set STAGING_DIR=C:\Users\jestory\OneDrive - Cisco\AI Projects\Staging\AdoptIQ_PC
set MAC_OUTBOX_STAGING_DIR=C:\Users\jestory\OneDrive - Cisco\AI Projects\Staging\AdoptIQ_MAC\OUTBOX

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

REM Keep OUTBOX deterministic: exactly the user-facing release payload.
for %%F in (OUTBOX\*) do (
    if /I not "%%~nxF"=="AdoptIQ.exe" if /I not "%%~nxF"=="Run_AdoptIQ.bat" if /I not "%%~nxF"=="Unblock_AdoptIQ.bat" if /I not "%%~nxF"=="READ_ME_FIRST.txt" if /I not "%%~nxF"=="README.md" if /I not "%%~nxF"=="build_info.txt" del /Q "%%~fF" >nul 2>nul
)

copy /Y dist\AdoptIQ.exe "OUTBOX\AdoptIQ.exe" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy dist\AdoptIQ.exe into OUTBOX.
    echo        Ensure the file is not locked and retry.
    pause
    exit /b 1
)
copy /Y Run_AdoptIQ.bat "OUTBOX\Run_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy Run_AdoptIQ.bat into OUTBOX.
    pause
    exit /b 1
)
copy /Y Unblock_AdoptIQ.bat "OUTBOX\Unblock_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy Unblock_AdoptIQ.bat into OUTBOX.
    pause
    exit /b 1
)
copy /Y "scripts\win\READ_ME_FIRST.txt" "OUTBOX\READ_ME_FIRST.txt" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy scripts\win\READ_ME_FIRST.txt into OUTBOX.
    pause
    exit /b 1
)
copy /Y README.md "OUTBOX\README.md" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy README.md into OUTBOX.
    pause
    exit /b 1
)
echo AdoptIQ v%ADOPTIQ_VERSION% build %ADOPTIQ_BUILD% > OUTBOX\build_info.txt
echo Built: %date% %time% >> OUTBOX\build_info.txt

REM Mirror release payload to staging folder with the same file contract.
echo Syncing staging folder...
if not exist "%STAGING_DIR%" mkdir "%STAGING_DIR%"
for %%F in ("%STAGING_DIR%\*") do (
    if /I not "%%~nxF"=="AdoptIQ.exe" if /I not "%%~nxF"=="Run_AdoptIQ.bat" if /I not "%%~nxF"=="Unblock_AdoptIQ.bat" if /I not "%%~nxF"=="READ_ME_FIRST.txt" if /I not "%%~nxF"=="README.md" if /I not "%%~nxF"=="build_info.txt" del /Q "%%~fF" >nul 2>nul
)
copy /Y "OUTBOX\AdoptIQ.exe" "%STAGING_DIR%\AdoptIQ.exe" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync AdoptIQ.exe to staging.
    echo        Close any running AdoptIQ.exe from "%STAGING_DIR%" and retry.
    pause
    exit /b 1
)
copy /Y "OUTBOX\Run_AdoptIQ.bat" "%STAGING_DIR%\Run_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync Run_AdoptIQ.bat to staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\Unblock_AdoptIQ.bat" "%STAGING_DIR%\Unblock_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync Unblock_AdoptIQ.bat to staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\READ_ME_FIRST.txt" "%STAGING_DIR%\READ_ME_FIRST.txt" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync READ_ME_FIRST.txt to staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\README.md" "%STAGING_DIR%\README.md" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync README.md to staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\build_info.txt" "%STAGING_DIR%\build_info.txt" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync build_info.txt to staging.
    pause
    exit /b 1
)

REM Also sync the same payload into Mac OUTBOX staging without deleting other files.
echo Syncing Mac OUTBOX staging payload...
if not exist "%MAC_OUTBOX_STAGING_DIR%" mkdir "%MAC_OUTBOX_STAGING_DIR%"
copy /Y "OUTBOX\AdoptIQ.exe" "%MAC_OUTBOX_STAGING_DIR%\AdoptIQ.exe" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync AdoptIQ.exe to Mac OUTBOX staging.
    echo        Close any running AdoptIQ.exe from "%MAC_OUTBOX_STAGING_DIR%" and retry.
    pause
    exit /b 1
)
copy /Y "OUTBOX\Run_AdoptIQ.bat" "%MAC_OUTBOX_STAGING_DIR%\Run_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync Run_AdoptIQ.bat to Mac OUTBOX staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\Unblock_AdoptIQ.bat" "%MAC_OUTBOX_STAGING_DIR%\Unblock_AdoptIQ.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync Unblock_AdoptIQ.bat to Mac OUTBOX staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\READ_ME_FIRST.txt" "%MAC_OUTBOX_STAGING_DIR%\READ_ME_FIRST.txt" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync READ_ME_FIRST.txt to Mac OUTBOX staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\README.md" "%MAC_OUTBOX_STAGING_DIR%\README.md" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync README.md to Mac OUTBOX staging.
    pause
    exit /b 1
)
copy /Y "OUTBOX\build_info.txt" "%MAC_OUTBOX_STAGING_DIR%\build_info.txt" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to sync build_info.txt to Mac OUTBOX staging.
    pause
    exit /b 1
)

REM Unblock built files so SmartScreen allows the app
powershell -NoProfile -Command "Get-ChildItem -Path OUTBOX -File | Unblock-File -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "Get-ChildItem -Path \"%STAGING_DIR%\" -File | Unblock-File -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "Get-ChildItem -Path \"%MAC_OUTBOX_STAGING_DIR%\" -File | Unblock-File -ErrorAction SilentlyContinue"

echo.
echo ==============================================
echo   Done
echo ==============================================
echo.
echo   OUTBOX\AdoptIQ.exe          - Standalone exe (the app itself)
echo   OUTBOX\Run_AdoptIQ.bat      - RECOMMENDED user entry point (auto-unblocks then launches)
echo   OUTBOX\Unblock_AdoptIQ.bat  - Clears SmartScreen "downloaded" flag if needed
echo   OUTBOX\READ_ME_FIRST.txt    - First-run instructions (open in Notepad)
echo   OUTBOX\README.md            - Full user documentation
echo   OUTBOX\build_info.txt       - Version / build metadata
echo.
echo   User double-clicks Run_AdoptIQ.bat; browser opens automatically to http://localhost:5151
echo.
pause
