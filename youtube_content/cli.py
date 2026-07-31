#!/usr/bin/env python3
"""
CLI entry point for the pip-installed youtube-content package.

Delegates to the bundled scripts/analyze_youtube.py. This wrapper lets
`pip install .` expose a `youtube-content` console command.

Script lookup order:
  1. <repo>/scripts (source checkout)
  2. <package>/scripts (pip-installed data files)
"""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# 1. Source checkout: <repo>/scripts
repo_scripts = PACKAGE_DIR.parent / 'scripts'
# 2. Pip install: <package>/scripts
pkg_scripts = PACKAGE_DIR / 'scripts'

if repo_scripts.exists():
    SCRIPTS_DIR = repo_scripts
elif pkg_scripts.exists():
    SCRIPTS_DIR = pkg_scripts
else:
    SCRIPTS_DIR = None

if SCRIPTS_DIR:
    sys.path.insert(0, str(SCRIPTS_DIR))


def main():
    if not SCRIPTS_DIR:
        print("Error: cannot locate youtube-content scripts. Reinstall the package.", file=sys.stderr)
        sys.exit(1)
    from analyze_youtube import main as analyze_main
    sys.exit(analyze_main())


if __name__ == '__main__':
    main()
