#!/bin/sh
set -eu

# Public test token and isolated sandbox endpoint. No production credentials.
export TBANK_API_TOKEN="TBankSandboxToken"
export TBANK_API_BASE_URL="https://business.tbank.ru/openapi/sandbox/api"
export TBANK_MCP_NAME="tbank-business-sandbox"

UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
if [ ! -x "$UV_BIN" ]; then
  UV_BIN="$(command -v uv || true)"
fi
if [ -z "$UV_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "uv не найден. Установите uv или задайте UV_BIN." >&2
  exit 1
fi

exec "$UV_BIN" --directory "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)" run tbank-business-mcp
