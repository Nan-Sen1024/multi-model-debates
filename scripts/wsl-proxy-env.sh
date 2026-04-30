#!/usr/bin/env bash

_wsl_proxy_log() {
  printf '%s\n' "$1"
}

_wsl_proxy_unset() {
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
}

_wsl_proxy_can_connect() {
  local host="$1"
  local port="$2"

  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket()
sock.settimeout(1.5)
try:
    sock.connect((host, port))
except Exception:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

_wsl_proxy_url_can_connect() {
  local proxy_url="$1"

  python3 - "$proxy_url" <<'PY'
import socket
import sys
from urllib.parse import urlparse

raw = sys.argv[1].strip()
if not raw:
    raise SystemExit(1)

parsed = urlparse(raw if "://" in raw else f"http://{raw}")
host = parsed.hostname
if not host:
    raise SystemExit(1)

port = parsed.port
if port is None:
    port = 443 if parsed.scheme == "https" else 80

sock = socket.socket()
sock.settimeout(1.5)
try:
    sock.connect((host, port))
except Exception:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

_wsl_proxy_export() {
  local proxy_url="$1"
  local no_proxy_value=""

  export HTTP_PROXY="$proxy_url"
  export HTTPS_PROXY="$proxy_url"
  export ALL_PROXY="$proxy_url"
  export http_proxy="$proxy_url"
  export https_proxy="$proxy_url"
  export all_proxy="$proxy_url"

  if [[ -n "${NO_PROXY:-}" ]]; then
    no_proxy_value="${NO_PROXY},127.0.0.1,localhost,::1"
  else
    no_proxy_value="127.0.0.1,localhost,::1"
  fi
  export NO_PROXY="$no_proxy_value"
  export no_proxy="$no_proxy_value"

  _wsl_proxy_log "[OK] proxy env exported: $proxy_url"
}

setup_wsl_proxy_env() {
  local proxy_url="${MMD_PROXY_URL:-${CLASH_PROXY_URL:-}}"
  local proxy_port="${MMD_PROXY_PORT:-${CLASH_PROXY_PORT:-7897}}"
  local gateway_ip=""
  local nameserver_ip=""

  for proxy_url in \
    "${proxy_url}" \
    "${HTTP_PROXY:-}" \
    "${HTTPS_PROXY:-}" \
    "${ALL_PROXY:-}" \
    "${http_proxy:-}" \
    "${https_proxy:-}" \
    "${all_proxy:-}"; do
    if [[ -n "$proxy_url" ]]; then
      if _wsl_proxy_url_can_connect "$proxy_url"; then
        _wsl_proxy_export "$proxy_url"
        return 0
      fi
      _wsl_proxy_log "[WARN] configured proxy is not reachable from WSL: $proxy_url"
    fi
  done

  if _wsl_proxy_can_connect 127.0.0.1 "$proxy_port"; then
    _wsl_proxy_export "http://127.0.0.1:${proxy_port}"
    return 0
  fi

  gateway_ip="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  if [[ -n "$gateway_ip" ]] && _wsl_proxy_can_connect "$gateway_ip" "$proxy_port"; then
    _wsl_proxy_export "http://${gateway_ip}:${proxy_port}"
    return 0
  fi

  nameserver_ip="$(awk '/^nameserver / {print $2; exit}' /etc/resolv.conf 2>/dev/null || true)"
  if [[ -n "$nameserver_ip" ]] && _wsl_proxy_can_connect "$nameserver_ip" "$proxy_port"; then
    _wsl_proxy_export "http://${nameserver_ip}:${proxy_port}"
    return 0
  fi

  _wsl_proxy_log "[WARN] no reachable proxy detected for port ${proxy_port}"
  _wsl_proxy_log "[WARN] Windows Clash only listening on 127.0.0.1 is not reachable from WSL NAT mode."
  _wsl_proxy_log "[WARN] Enable WSL mirrored networking or let Clash bind to the Windows host/LAN address."
  _wsl_proxy_log "[WARN] You can also set MMD_PROXY_URL manually before starting the backend."
  _wsl_proxy_unset
  _wsl_proxy_log "[INFO] cleared stale proxy env from this shell"
  return 1
}

setup_wsl_proxy_env
