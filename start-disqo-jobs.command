#!/bin/bash
# Double-click this file to start disqo jobs. macOS and Linux.
# It sets everything up the first time, then just opens your browser.
cd "$(dirname "$0")" || exit 1
set -o pipefail

say() { printf "\n  %s\n" "$1"; }
fail() {
  say "$1"
  say "Nothing was changed. Close this window and try again, or ask for help."
  read -r -p "  Press return to close. " _
  exit 1
}

PY=""
for c in python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.10 or newer is required. Install it from python.org, then run this again."

if [ ! -d .venv ]; then
  say "First run — setting up. This takes a couple of minutes."
  "$PY" -m venv .venv || fail "Couldn't create the environment."
fi
# shellcheck disable=SC1091
. .venv/bin/activate || fail "Couldn't activate the environment."

if ! python -c "import jobpilot" >/dev/null 2>&1; then
  say "Installing disqo jobs…"
  pip install --quiet --upgrade pip >/dev/null 2>&1
  pip install --quiet -e . || fail "Install failed. Check your internet connection."
fi

URL="http://127.0.0.1:8000"
say "Starting disqo jobs at $URL"
say "Leave this window open while you use it. Close it to quit."
( sleep 2; command -v open >/dev/null 2>&1 && open "$URL" || \
  { command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL"; } ) >/dev/null 2>&1 &
python -m uvicorn jobpilot.dashboard.server:app --host 127.0.0.1 --port 8000
