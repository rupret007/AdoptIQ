@echo off
REM AdoptIQ launcher - starts AdoptIQ.exe from the same folder
REM Removes "blocked" flag from downloaded files (like Mac xattr -cr) so SmartScreen allows the app
cd /d "%~dp0"
powershell -NoProfile -Command "Set-Location -LiteralPath '%~dp0'; Get-ChildItem -File | Unblock-File -ErrorAction SilentlyContinue"
if exist AdoptIQ.exe (
    AdoptIQ.exe
) else (
    echo AdoptIQ.exe not found in this folder.
    echo Place this batch file in the same folder as AdoptIQ.exe.
    pause
)
