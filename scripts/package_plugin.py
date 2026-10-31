#!/usr/bin/env python3
"""Build an installable Decky zip: plex-audiobooks/<plugin files>."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_JS = ROOT / "dist" / "index.js"
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
VERSION = PACKAGE["version"]
FOLDER = "plex-audiobooks"
OUT_DIR = ROOT / "release"
ZIP_PATH = OUT_DIR / f"plex-audiobooks-v{VERSION}.zip"

FILES = [
    "dist/index.js",
    "main.py",
    "deck_hid.py",
    "game_audio.py",
    "player.py",
    "plex.py",
    "plex_auth.py",
    "progress.py",
    "plugin.json",
    "package.json",
    "LICENSE",
    "NOTICE",
    "README.md",
]


def main() -> None:
    if not DIST_JS.is_file():
        raise SystemExit("dist/index.js is missing. Run pnpm run build first.")
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Missing files: " + ", ".join(missing))
    OUT_DIR.mkdir(exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, arcname=f"{FOLDER}/{name}")
    print(ZIP_PATH)


if __name__ == "__main__":
    main()
