#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

fail() {
  echo "[FAIL] $1" >&2
  exit 1
}

check_cmd() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "missing command: $name"
}

echo "[INFO] repo: $ROOT_DIR"
check_cmd python3
check_cmd node
check_cmd npm

[[ -x "$VENV_PYTHON" ]] || fail "missing venv python: $VENV_PYTHON"
[[ -f "$ROOT_DIR/backend/api.py" ]] || fail "backend/api.py not found"
[[ -f "$FRONTEND_DIR/package.json" ]] || fail "frontend/package.json not found"
[[ -d "$FRONTEND_DIR/node_modules" ]] || fail "frontend/node_modules not found; run 'cd frontend && npm install'"

"$VENV_PYTHON" - <<'PY'
import importlib.util
required = ["fastapi", "uvicorn", "aiosqlite"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(f"missing python deps: {', '.join(missing)}")
print("[OK] python deps")
PY

echo "[OK] node: $(node -v)"
echo "[OK] npm: $(npm -v)"
echo "[OK] frontend node_modules"
echo "[OK] project looks runnable in WSL"
