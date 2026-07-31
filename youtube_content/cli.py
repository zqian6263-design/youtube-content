#!/usr/bin/env python3
"""
CLI entry point for the pip-installed youtube-content package.

Delegates to the bundled scripts/analyze_youtube.py. This wrapper lets
`pip install .` expose a `youtube-content` console command.

Script lookup order:
  1. <repo>/scripts          — source checkout (development)
  2. <sys.prefix>/youtube_content/scripts — pip data-files (venv root)
  3. <package>/scripts       — site-packages layout
"""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

candidates = [
    PACKAGE_DIR.parent / 'scripts',              # repo checkout
    Path(sys.prefix) / 'youtube_content' / 'scripts',  # pip data-files
    PACKAGE_DIR / 'scripts',                     # site-packages
]

SCRIPTS_DIR = None
for c in candidates:
    if c.exists() and (c / 'analyze_youtube.py').exists():
        SCRIPTS_DIR = c
        break

if SCRIPTS_DIR:
    sys.path.insert(0, str(SCRIPTS_DIR))


def main():
    if not SCRIPTS_DIR:
        print("Error: cannot locate youtube-content scripts. Reinstall the package.",
              file=sys.stderr)
        sys.exit(1)
    from analyze_youtube import main as analyze_main
    sys.exit(analyze_main())


if __name__ == '__main__':
    main()
