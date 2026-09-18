#!/usr/bin/env bash
# ctl.sh — leap-aide unified control script
# Usage: ./ctl.sh <command> [command...]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEBUI_PORT=${WEBUI_PORT:-8787}
PORT_HEX=$(printf '%04X' "$WEBUI_PORT")
PID_FILE="$HOME/.hermes/webui/server-${WEBUI_PORT}.pid"
LOG_FILE="$HOME/.hermes/webui/bootstrap-${WEBUI_PORT}.log"
SHUTDOWN_TIMEOUT=8

# ── Load .env ──
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a; source "${PROJECT_ROOT}/.env"; set +a
fi

# ── Timing helpers ──
_CTL_T0=$SECONDS
_CTL_START=$SECONDS
_CTL_STAGE=""

_stage() {
  _CTL_T0=$SECONDS
  _CTL_STAGE="<$1>"
  echo "[ctl] ${_CTL_STAGE} begin"
}

_ok() {
  printf "[ctl] %s take %ds. OK\n\n" "${_CTL_STAGE}" "$((SECONDS - _CTL_T0))"
}

_fail() {
  printf "[ctl] %s take %ds. %s\n\n" "${_CTL_STAGE}" "$((SECONDS - _CTL_T0))" "$1" >&2
}

_warn() {
  printf "[ctl] %s take %ds. %s\n\n" "${_CTL_STAGE}" "$((SECONDS - _CTL_T0))" "$1" >&2
}

# ── Utilities ──
info()  { echo "[ctl] $*"; }
warn()  { echo "[ctl] WARNING: $*" >&2; }
die()   { echo "[ctl] ERROR: $*" >&2; exit 1; }

_find_python() {
  local python="${HERMES_WEBUI_PYTHON:-}"
  if [[ -z "$python" ]]; then
    python="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
  fi
  [[ -n "$python" ]] || die "Python 3 is required"
  echo "$python"
}

_server_pid() {
  # Prefer PID file, fallback to process table
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return
    fi
  fi
  # Fallback: find from process table (port is in env, not cmdline).
  # Match both legacy ``python backend/app/server.py`` and the FastAPI-style
  # ``python -m app.server`` invocation.
  pgrep -f "python.*backend/app/server\.py" 2>/dev/null | head -1 || true
  pgrep -f "python.*-m app\.server" 2>/dev/null | head -1 || true
}

_port_in_use() {
  grep -qi ":${PORT_HEX}[[:space:]]\\+00000000:0000[[:space:]]\\+0A" /proc/net/tcp 2>/dev/null
}

# ── Command: stop ──
cmd_stop() {
  _stage "server_stop"

  # 1. PID file first → SIGTERM
  local pid
  pid=$(_server_pid)
  if [[ -n "$pid" ]]; then
    info "Sending SIGTERM to server.py (PID $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 $((SHUTDOWN_TIMEOUT * 2))); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "Server did not exit in ${SHUTDOWN_TIMEOUT}s, sending SIGKILL..."
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"

  # 2. Fallback cleanup. Match both the old ``python backend/app/server.py``
  # and the new ``python -m app.server`` invocations so restart always kills
  # the right process regardless of which entry point started it.
  pkill -f "backend/app/bootstrap.py.*--no-browser" 2>/dev/null || true
  pkill -f "python.*backend/app/server\.py" 2>/dev/null || true
  pkill -f "python.*-m app\.server" 2>/dev/null || true
  pkill -f "hermes gateway" 2>/dev/null || true
  sleep 1

  # 3. Wait for port release
  for _ in $(seq 1 12); do
    _port_in_use || break
    sleep 0.5
  done

  # 4. Clean up browser-related processes
  pkill -f "chrome.*remote-debugging" 2>/dev/null || true
  pkill -f "openbox" 2>/dev/null || true
  pkill -f "Xvfb" 2>/dev/null || true
  pkill -f "x11vnc" 2>/dev/null || true
  pkill -f "agent-browser" 2>/dev/null || true

  _ok
}

# ── Command: start ──
cmd_start() {
  # Check if already running
  if [[ -n "$(_server_pid)" ]]; then
    die "Server already running (PID $(_server_pid)). Use 'restart' or 'stop' first."
  fi

  _stage "server_start"

  # 1. Rotate old log to old_logs/
  OLD_LOGS_DIR="$HOME/.hermes/webui/old_logs"
  mkdir -p "$OLD_LOGS_DIR"
  if [[ -f "$LOG_FILE" ]]; then
    _seq=1
    while [[ -f "${OLD_LOGS_DIR}/bootstrap-${WEBUI_PORT}.${_seq}.log" ]]; do
      ((_seq++))
    done
    mv "$LOG_FILE" "${OLD_LOGS_DIR}/bootstrap-${WEBUI_PORT}.${_seq}.log" 2>/dev/null || true
  fi
  # Keep only last 10 rotated logs
  ls -t "${OLD_LOGS_DIR}"/bootstrap-${WEBUI_PORT}.*.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true

  # 2. Start server (foreground, NOT exec — we need to start gateway after)
  local python
  python=$(_find_python)
  unset ANTHROPIC_BASE_URL
  HOST=0.0.0.0 LOG_LEVEL=DEBUG "${python}" "${PROJECT_ROOT}/backend/app/bootstrap.py" --no-browser
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    _fail "Server failed to start (exit code $rc)"
    return 1
  fi
  _ok

  # 3. Start gateway AFTER server health check passes.
  #    Starting gateway before server causes a race condition: both load
  #    hermes-agent modules concurrently (plugin scanning, logging setup),
  #    which can deadlock the server's import chain at stage 8.
  _stage "gateway_start"
  hermes gateway 1>/dev/null 2>&1 &
  info "Gateway started (PID $!)"

  _ok
}

# ── Command: restart ──
cmd_restart() {
  cmd_stop
  cmd_start
}

# ── Command: status ──
cmd_status() {
  _stage "Checking status..."

  local pid
  pid=$(_server_pid)
  if [[ -n "$pid" ]]; then
    info "Server is running (PID $pid)"
    if _port_in_use; then
      info "Port ${WEBUI_PORT} is listening"
    else
      warn "Port ${WEBUI_PORT} is NOT listening (server may be starting)"
    fi
    # Health check
    local scheme="http"
    [[ -n "${HERMES_WEBUI_TLS_CERT:-}" ]] && scheme="https"
    if curl -sk "${scheme}://localhost:${WEBUI_PORT}/health" 2>/dev/null | grep -q '"status"'; then
      info "Health check: OK"
    else
      warn "Health check: FAILED"
    fi
  else
    info "Server is NOT running"
  fi

  # Gateway status
  if pgrep -f "hermes gateway" >/dev/null 2>&1; then
    info "Gateway is running (PID $(pgrep -f 'hermes gateway' | head -1))"
  else
    info "Gateway is NOT running"
  fi

  _ok
}

# ── Main entry ──
if [[ $# -eq 0 ]]; then
  echo "Usage: $0 <command> [command...]"
  echo ""
  echo "  start     Start leap-aide (server + gateway)"
  echo "  stop      Gracefully stop all services"
  echo "  restart   Restart (stop + start)"
  echo "  status    Show running status"
  echo ""
  echo "For build operations, use: ./scripts/build.sh"
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    start)    cmd_start ;;
    stop)     cmd_stop ;;
    restart)  cmd_restart ;;
    status)   cmd_status ;;
    build|install)
      echo "[ctl] WARNING: '$arg' command is deprecated."
      echo "[ctl] Use './scripts/build.sh' for build operations."
      echo "[ctl] Use './install.sh' for installation."
      exit 1
      ;;
    *)        die "Unknown command: $arg" ;;
  esac
done

echo "[ctl] Total: $((SECONDS - _CTL_START))s"
