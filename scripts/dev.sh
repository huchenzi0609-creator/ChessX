#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken"
if [ ! -x .venv/bin/python ]; then
  echo 'Run uv sync first.' >&2
  exit 1
fi
.venv/bin/python -m uvicorn chessx.main:app --host 127.0.0.1 --port 8001 --no-access-log &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT INT TERM
pnpm --dir frontend dev --host 127.0.0.1
