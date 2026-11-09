#!/bin/sh
set -eu

SERVICE_NAME="tbank-business-api-token"
TOKEN="$(/usr/bin/security find-generic-password -a "$USER" -s "$SERVICE_NAME" -w 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "T-Банк токен не найден в Keychain (service: $SERVICE_NAME)." >&2
  exit 1
fi
export TBANK_API_TOKEN="$TOKEN"
unset TOKEN

UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
if [ ! -x "$UV_BIN" ]; then
  UV_BIN="$(command -v uv || true)"
fi
if [ -z "$UV_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "uv не найден. Установите uv или задайте UV_BIN." >&2
  exit 1
fi

exec "$UV_BIN" --directory "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)" run tbank-business-mcp
