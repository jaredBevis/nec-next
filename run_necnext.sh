#!/bin/sh
# Launch NEC Next on Linux / Raspberry Pi.  Add --no-gl to use the software 3D view.
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python -m necnext "$@"; fi
exec python3 -m necnext "$@"
