@echo off
REM Unblock AdoptIQ files (Windows equivalent of Mac "xattr -cr")
REM Run this if SmartScreen blocks the app. Removes the "downloaded from internet" flag.
cd /d "%~dp0"
echo Unblocking AdoptIQ files...
powershell -NoProfile -Command "Get-ChildItem -Path . -File | Unblock-File -ErrorAction SilentlyContinue"
echo Done. You can now run Run_AdoptIQ.bat or AdoptIQ.exe.
pause
