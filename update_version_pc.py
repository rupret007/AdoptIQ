#!/usr/bin/env python3
"""Update ADOPTIQ_VERSION and ADOPTIQ_BUILD in config.py and version_info.txt. Called by build_pc.bat (Windows) and build_mac.sh (macOS)."""
import re
import os

v = os.environ.get("ADOPTIQ_VERSION", "1.0.2")
b = os.environ.get("ADOPTIQ_BUILD", "1")

with open("config.py", "r", encoding="utf-8") as f:
    c = f.read()
c = re.sub(r'ADOPTIQ_VERSION = "[^"]*"', f'ADOPTIQ_VERSION = "{v}"', c)
c = re.sub(r'ADOPTIQ_BUILD = "[^"]*"', f'ADOPTIQ_BUILD = "{b}"', c)
with open("config.py", "w", encoding="utf-8") as f:
    f.write(c)

# Write version info for installer (Programs and Features, display)
with open("version_info.txt", "w", encoding="utf-8") as f:
    f.write(f"{v}\n{b}\n")
print(f"Updated config.py and version_info.txt: v{v} build {b}")
