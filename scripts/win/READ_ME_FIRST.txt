AdoptIQ for Windows - Install (about 1 minute)
==============================================

1) Double-click "Run_AdoptIQ.bat".
   - This clears Windows' "downloaded from the internet" mark on the files
     in this folder, then launches AdoptIQ.exe.
   - A console window stays open while AdoptIQ is running. Closing that
     window stops AdoptIQ.
   - Your default browser opens automatically to http://localhost:5151.

2) If Windows SmartScreen blocks the launch:
   - In the SmartScreen dialog click "More info" -> "Run anyway".
     OR
   - Close the dialog, double-click "Unblock_AdoptIQ.bat" once, then
     double-click "Run_AdoptIQ.bat" again.
     OR
   - Right-click "AdoptIQ.exe" -> Properties -> tick "Unblock" at the
     bottom of the General tab -> OK, then double-click "Run_AdoptIQ.bat".

3) From now on, just double-click "Run_AdoptIQ.bat" to start AdoptIQ.


Why is the unblock step needed?
-------------------------------

AdoptIQ is not signed by an external code-signing CA, so when Windows
sees the file came from an outside source it adds a "Mark of the Web"
flag (the Zone.Identifier alternate data stream). SmartScreen will then
warn or block the launch. "Unblock_AdoptIQ.bat" runs the equivalent of:

    Get-ChildItem -File | Unblock-File

which clears that flag for every file in the folder. You only need to
do it once per fresh download.


Troubleshooting
---------------

- "Could not reach the AdoptIQ server" in the browser
  -> The AdoptIQ console window was closed (or never started). Re-launch
     by double-clicking "Run_AdoptIQ.bat".

- "Windows protected your PC" SmartScreen dialog with no "Run anyway"
  -> Run "Unblock_AdoptIQ.bat" first, then "Run_AdoptIQ.bat".

- Port 5151 already in use
  -> Open Command Prompt and run:
       netstat -ano | findstr :5151
     Then in Task Manager, end the PID shown in the last column.

- "Missing module" or "ModuleNotFoundError" in the console
  -> The AdoptIQ.exe in this folder is incomplete; re-download the full
     OUTBOX folder (AdoptIQ.exe + all .bat helpers + READ_ME_FIRST.txt).

- Need to fully reinstall
  -> Close the AdoptIQ console window, delete this folder, then re-extract
     the new release and start over from step 1 above.

To quit AdoptIQ: close the AdoptIQ console window (or press Ctrl+C in it).
