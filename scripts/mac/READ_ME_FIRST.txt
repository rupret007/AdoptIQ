AdoptIQ for macOS - Install (about 1 minute)
============================================

0) If macOS blocks opening the DMG with "Apple could not verify..."
   or "cannot check it for malicious software":
   - Click Done.
   - Open System Settings > Privacy & Security.
   - In the Security section, click Open Anyway / Allow for AdoptIQ.
   - Open the DMG again.

1) Drag AdoptIQ to the Applications folder (the icons in this DMG window).

2) In this same DMG window, double-click "Unblock AdoptIQ.command".
   - This clears macOS Gatekeeper's quarantine flag on the installed app
     and starts AdoptIQ.
   - You only need to do this the first time after installing or updating.
   - If macOS asks "Allow Terminal to open this script?", click Open.

3) From now on, launch AdoptIQ from Applications or Spotlight.
   Your browser will open to http://localhost:5151 automatically.


Why is the unblock step needed?
-------------------------------

AdoptIQ is signed adhoc (we don't have an Apple Developer ID), so macOS
quarantines the app when you download the DMG. On Apple Silicon Macs,
quarantined adhoc-signed apps are silently killed at launch - the Dock
icon bounces once and the app exits with no error window.

"Unblock AdoptIQ.command" simply runs:

    xattr -dr com.apple.quarantine /Applications/AdoptIQ.app
    open /Applications/AdoptIQ.app

You can run those two commands manually in Terminal instead if you prefer.


Troubleshooting
---------------

- "Apple could not verify..." or "cannot be opened because Apple cannot
  check it for malicious software" while opening the DMG
  -> Open System Settings > Privacy & Security and click Open Anyway /
     Allow for AdoptIQ, then open the DMG again.

- Same warning after dragging AdoptIQ.app to Applications
  -> Run "Unblock AdoptIQ.command" from this DMG window.

- "Operation not permitted" when running the unblock script
  -> Open System Settings > Privacy & Security > Files and Folders, give
     Terminal access to your Applications folder, then re-run the script.

- App still bounces after unblocking
  -> Open Terminal and run:
       xattr /Applications/AdoptIQ.app
     If you still see "com.apple.quarantine", run:
       sudo xattr -dr com.apple.quarantine /Applications/AdoptIQ.app

- Need to fully reinstall
  -> Drag AdoptIQ.app from /Applications to the Trash, then start over
     from step 1 above.

To quit AdoptIQ: use the AdoptIQ menu in the menu bar, or press Cmd+Q.
