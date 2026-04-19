#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/.devpids"

stop_service() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "[INFO] $name not running"
    return 0
  fi

  local pid
  pid="$(cat "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid"
    echo "[OK] stopped $name: pid=$pid"
  else
    echo "[INFO] stale pid for $name: $pid"
  fi
  rm -f "$pid_file"
}

stop_service "frontend" "$PID_DIR/frontend.pid"
stop_service "backend" "$PID_DIR/backend.pid"
