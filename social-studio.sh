#!/usr/bin/env bash
set -euo pipefail

STUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://127.0.0.1:4000"
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"

if ! curl -sf "$URL/api/brands" >/dev/null 2>&1; then
  cd "$STUDIO_DIR"
  nohup "$UV_BIN" run uvicorn app:app --host 127.0.0.1 --port 4000 \
    >"$STUDIO_DIR/studio.log" 2>&1 &
  for _ in $(seq 1 30); do
    if curl -sf "$URL/api/brands" >/dev/null 2>&1; then
      break
    fi
    sleep 0.3
  done
fi

if command -v google-chrome >/dev/null 2>&1; then
  exec google-chrome --app="$URL" >/dev/null 2>&1 &
elif command -v chromium >/dev/null 2>&1; then
  exec chromium --app="$URL" >/dev/null 2>&1 &
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser --app="$URL" >/dev/null 2>&1 &
else
  exec xdg-open "$URL" >/dev/null 2>&1 &
fi
