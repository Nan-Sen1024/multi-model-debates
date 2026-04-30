#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.devlogs"
PID_DIR="$ROOT_DIR/.devpids"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

mkdir -p "$LOG_DIR" "$PID_DIR"

"$ROOT_DIR/scripts/dev-selfcheck-wsl.sh"

log_proxy_env() {
  echo "[INFO] proxy env snapshot:"
  for key in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy MMD_PROXY_URL CLASH_PROXY_URL; do
    local value="${!key:-unset}"
    echo "[INFO]   $key=${value}"
  done
}

log_proxy_env

if ! source "$ROOT_DIR/scripts/wsl-proxy-env.sh"; then
  echo "[WARN] WSL proxy env was not configured automatically."
  echo "[WARN] If Clash only listens on Windows 127.0.0.1, enable mirrored networking or allow LAN binding."
fi

log_proxy_env

BACKEND_RELOAD="${MMD_BACKEND_RELOAD:-0}"
BACKEND_CMD=("$ROOT_DIR/.venv/bin/python" -m uvicorn backend.api:app --host 0.0.0.0 --port 8000)
if [[ "$BACKEND_RELOAD" == "1" || "$BACKEND_RELOAD" == "true" ]]; then
  BACKEND_CMD+=(--reload)
else
  echo "[INFO] backend reload disabled; set MMD_BACKEND_RELOAD=1 for manual hot reload."
fi

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "[INFO] $name already running: pid=$pid"
      return 0
    fi
    rm -f "$pid_file"
  fi

  nohup "$@" >"$log_file" 2>&1 &
  local pid=$!
  echo "$pid" >"$pid_file"
  echo "[OK] started $name: pid=$pid log=$log_file"
}

start_service \
  "backend" \
  "$BACKEND_PID_FILE" \
  "$LOG_DIR/backend.log" \
  "${BACKEND_CMD[@]}"

start_service \
  "frontend" \
  "$FRONTEND_PID_FILE" \
  "$LOG_DIR/frontend.log" \
  env HOST=0.0.0.0 npm --prefix "$FRONTEND_DIR" start

echo "[INFO] backend:  http://127.0.0.1:8000"
echo "[INFO] frontend: http://127.0.0.1:3000"
echo "[INFO] tail logs:"
echo "       tail -f '$LOG_DIR/backend.log'"
echo "       tail -f '$LOG_DIR/frontend.log'"
