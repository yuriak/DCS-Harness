#!/usr/bin/env sh

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    DCS_HARNESS_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    DCS_HARNESS_PYTHON=python
else
    echo "DCS-Harness setup requires Python 3.10 or newer." >&2
    exit 1
fi

exec "$DCS_HARNESS_PYTHON" "$SCRIPT_DIR/tools/src/py/setup.py" "$@"
