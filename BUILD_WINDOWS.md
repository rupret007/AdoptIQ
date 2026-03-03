# Building AdoptIQ for Windows

**Important:** PyInstaller builds for the **current operating system**. You cannot build a Windows `.exe` on a Mac. You must build on Windows (or use CI).

## Option 1: Build on a Windows PC (recommended)

1. Copy the entire `AdoptIQ_PC` folder to a Windows machine.
2. **Optional:** Create `secrets.env` from `secrets.env.template` and fill in credentials (Snowflake, CircuIT, etc.). If omitted, the build creates `secrets.env` from the template (empty) and bundles an empty stub; users can add `.env` to `%APPDATA%\AdoptIQ\` later.
3. Open Command Prompt or PowerShell in the `AdoptIQ_PC` folder.
4. Run:
   ```
   build_pc.bat
   ```
5. When done, the output is in `OUTBOX\`:
   - **AdoptIQ-Setup.exe** – single-file installer (if Inno Setup 6 is installed). Users click to install.
   - **AdoptIQ.exe** – the application (fallback if Inno Setup not installed)
   - **README.md** – user instructions

6. **To create the installer:** Install [Inno Setup 6](https://jrsoftware.org/isdl.php), then run `build_pc.bat` again. The build will produce `AdoptIQ-Setup.exe` – the single file users click to install.

## Option 2: GitHub Actions (automatic Windows build)

1. Push the repo (including `AdoptIQ_PC` and `.github/workflows/build-windows.yml`) to GitHub.
2. The workflow runs on push when `AdoptIQ_PC/**` changes.

3. After the run completes:
   - Go to **Actions** → select the workflow run
   - Download the **AdoptIQ-Windows-Install** artifact (a `.zip` file)

4. The zip contains:
   - `AdoptIQ-Setup.exe` (installer) or `AdoptIQ.exe`
   - `README.md`

5. Share with users. They run `AdoptIQ-Setup.exe` to install, then run AdoptIQ (shortcut or command line) to start the server and browse to http://localhost:5001.

**Note:** The GitHub build uses an empty credentials stub unless `secrets.env` is added to the repo (not recommended). Users can add `.env` to `%APPDATA%\AdoptIQ\` for Snowflake and other credentials.

## Summary

| Method | Where | Output |
|--------|-------|--------|
| `build_pc.bat` | Windows PC | `OUTBOX\AdoptIQ-Setup.exe` (with Inno Setup) or `AdoptIQ.exe` + README |
| GitHub Actions | Cloud (Windows runner) | `AdoptIQ-Windows-Install.zip` artifact |
