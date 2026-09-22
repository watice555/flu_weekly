#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONIOENCODING="utf-8"
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
"$repo/.venv/bin/python" main.py
exec "$repo/.venv/bin/python" -m flu_data.publish_site
