@echo off
REM Create a git restore point for AdoptIQ.
REM Usage: create_restore_point.bat [message]
REM   If no message, uses: "Restore point: <date>"
REM Excludes: *.log, *.db, diff_full.txt (see .gitignore)

setlocal
cd /d "%~dp0"

set MSG=%~1
if "%MSG%"=="" (
    for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set DT=%%I
    set MSG=Restore point: %date%
)

echo ==============================================
echo   AdoptIQ - Create Restore Point
echo ==============================================
echo.
echo Message: %MSG%
echo.

REM Stage project files (respects .gitignore - excludes *.log, *.db, diff_full.txt)
git add .

REM Show what will be committed
git status
echo.
set /p CONFIRM="Commit with above files? (Y/n): "
if /i "%CONFIRM%"=="n" (
    echo Cancelled.
    exit /b 0
)

git commit -m "%MSG%"
if %ERRORLEVEL% equ 0 (
    echo.
    echo Restore point created successfully.
    git log -1 --oneline
) else (
    echo.
    echo No changes to commit, or commit failed.
)
echo.
pause
