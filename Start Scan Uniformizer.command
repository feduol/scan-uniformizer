#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"

PYTHON=""
for candidate in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$candidate" -c 'import sys, tkinter; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo 'Python 3.10 or newer with Tk is required. Install Python for macOS from python.org, then reopen this launcher.'
    read -r -p 'Press Return to close.'
    exit 1
fi
if [ -x .venv/bin/python ]; then
    PYTHON="$PWD/.venv/bin/python"
elif ! "$PYTHON" -c 'import numpy, PIL, cv2' >/dev/null 2>&1; then
    echo 'First-time setup: installing image-processing libraries into this folder.'
    echo 'An internet connection is needed for this setup only.'
    "$PYTHON" -m venv .venv
    PYTHON="$PWD/.venv/bin/python"
fi
if ! "$PYTHON" -c 'import numpy, PIL, cv2' >/dev/null 2>&1; then
    if ! "$PYTHON" -m pip install -r requirements.txt; then
        echo 'Setup failed. Check your internet connection and reopen this launcher.'
        read -r -p 'Press Return to close.'
        exit 1
    fi
fi
if ! "$PYTHON" scan_uniformizer.py --gui; then
    echo 'The program stopped with an error. See the message above.'
    read -r -p 'Press Return to close.'
    exit 1
fi
