#!/usr/bin/env bash
# ctl.sh — leap-aide unified control script
# Usage: ./ctl.sh <command> [command...]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Load .env ──
# Only fills in variables that are not already set. Values already present in
# the environment (docker-compose `environment:`, an exported shell variable)
# win over .env; .env is skipped for those keys. Pre-existing values are
# snapshotted and restored after sourcing so we reuse bash's own .env parsing
# without its override side effect.
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  _dotenv_keep=()
  while IFS= read -r _line || [[ -n "${_line}" ]]; do
    _stmt="${_line#export }"
    case "${_stmt}" in
      *=*) ;;
      *) continue ;;
    esac
    _key="${_stmt%%=*}"
    _key="${_key#"${_key%%[![:space:]]*}"}"
    _key="${_key%"${_key##*[![:space:]]}"}"
    case "${_key}" in
      ''|*[!A-Za-z0-9_]*|[0-9]*) continue ;;
    esac
    if eval "[ -n \"\${${_key}+set}\" ]"; then
      _dotenv_keep+=("$(declare -p "${_key}")")
    fi
  done < "${PROJECT_ROOT}/.env"
  set -a; source "${PROJECT_ROOT}/.env"; set +a
  if [[ ${#_dotenv_keep[@]} -gt 0 ]]; then
    for _stmt in "${_dotenv_keep[@]}"; do eval "${_stmt}"; done
  fi
  unset _line _stmt _key _dotenv_keep
fi

# WEBUI_PORT names the pid/log files below. Prefer an explicit WEBUI_PORT,
# then the server's bind port (HERMES_WEBUI_PORT), then the default. Resolved
# AFTER the .env load so a port configured in .env names the files correctly.
WEBUI_PORT=${WEBUI_PORT:-${HERMES_WEBUI_PORT:-8787}}
PORT_HEX=$(printf '%04X' "$WEBUI_PORT")
# Mirror bootstrap.py, which writes server-<port>.pid and bootstrap-<port>.log
# into $HERMES_WEBUI_STATE_DIR (default ~/.hermes/webui).
STATE_DIR="${HERMES_WEBUI_STATE_DIR:-$HOME/.hermes/webui}"
PID_FILE="$STATE_DIR/server-${WEBUI_PORT}.pid"
LOG_FILE="$STATE_DIR/bootstrap-${WEBUI_PORT}.log"
SHUTDOWN_TIMEOUT=8

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
  #    Bare binary names use `pkill -x` (exact process-name match). A `-f`
  #    substring match on e.g. "openbox" also hits any command whose argv
  #    merely contains that word — notably the runtime image build's
  #    `apt-get install ... x11vnc openbox ...`, which a concurrent
  #    ctl.sh stop/restart would SIGTERM mid-build (exit 143).
  pkill -f "chrome.*remote-debugging" 2>/dev/null || true
  pkill -x openbox 2>/dev/null || true
  pkill -x Xvfb 2>/dev/null || true
  pkill -x x11vnc 2>/dev/null || true
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
  OLD_LOGS_DIR="$STATE_DIR/old_logs"
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

# ── Command: log-path ──
# Print the resolved server log path and exit. Used by docker-entrypoint.sh to
# follow the server log for `docker logs`. Exits immediately so stdout carries
# only the path (no "[ctl] Total:" trailer) and is safe to capture with $( ).
cmd_log_path() {
  printf '%s\n' "$LOG_FILE"
  exit 0
}

# ── Container helpers (host side) ────────────────────────────────────────────
# start-docker / stop-docker drive a compose implementation; they never build a
# `podman run` argv themselves, so docker-compose.yml stays the single source of
# truth for ports, volumes and environment. The wrapper only decides *how* to
# run it: which engine, which compose implementation, and which host uid/gid the
# container must map itself onto.
#
# Why a wrapper at all: compose interpolation cannot run `id -u`, and YAML
# cannot branch on "rootless vs rootful". Both decisions need runtime state, so
# they belong here.
WITHIN_CONTAINER_MARKER="${WITHIN_CONTAINER_MARKER:-/.within_container}"

_ENGINE=""
_COMPOSE_KIND=""
_COMPOSE=()
_CTL_TMP_FILES=()
_OVERRIDE_FILE=""

_ctl_cleanup() {
  local f
  for f in "${_CTL_TMP_FILES[@]:-}"; do
    if [[ -n "$f" ]]; then
      rm -f "$f"
    fi
  done
  # An EXIT trap must not leak its own status: bash adopts the trap's return
  # value when the script ends normally, so a "nothing to clean" no-op would
  # turn a successful run into exit code 1.
  return 0
}
trap _ctl_cleanup EXIT

_container_guard() {
  if [[ -e "$WITHIN_CONTAINER_MARKER" ]]; then
    die "$1 is a host-side command and cannot run inside the container"
  fi
}

# Can this engine actually be talked to? `podman info` fails on a broken
# storage root and `docker info` fails when the daemon is down; both mean
# "unusable", not "not installed".
_engine_usable() {
  command -v "$1" >/dev/null 2>&1 || return 1
  case "$1" in
    podman) podman info >/dev/null 2>&1 ;;
    docker) docker info >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

_set_compose() {
  _ENGINE="$1"
  _COMPOSE_KIND="$2"
  shift 2
  _COMPOSE=("$@")
}

# Detection order: podman-compose → podman compose → docker compose.
# podman-compose wins on purpose: it is the only implementation that can
# express `userns_mode: keep-id`, which rootless podman needs (see
# _prepare_userns_override). `podman compose` merely forwards to an external
# provider that may be docker-compose, which rejects keep-id.
_detect_compose() {
  local forced="${1:-${LEAP_AIDE_ENGINE:-}}"
  case "$forced" in
    ""|podman|docker) ;;
    *) die "unknown engine '$forced' (expected podman or docker)" ;;
  esac

  if [[ "$forced" == "podman" ]]; then
    _engine_usable podman || die "engine 'podman' requested but not usable"
    if command -v podman-compose >/dev/null 2>&1; then
      _set_compose podman podman-compose podman-compose
    elif podman compose version >/dev/null 2>&1; then
      _set_compose podman "podman compose" podman compose
    else
      die "engine 'podman' requested but no compose implementation found (install podman-compose)"
    fi
    return 0
  fi

  if [[ "$forced" == "docker" ]]; then
    _engine_usable docker || die "engine 'docker' requested but not usable"
    docker compose version >/dev/null 2>&1 \
      || die "engine 'docker' requested but 'docker compose' is not available"
    _set_compose docker "docker compose" docker compose
    return 0
  fi

  local podman_seen=false
  if _engine_usable podman; then
    podman_seen=true
    if command -v podman-compose >/dev/null 2>&1; then
      _set_compose podman podman-compose podman-compose
      return 0
    fi
    if podman compose version >/dev/null 2>&1; then
      _set_compose podman "podman compose" podman compose
      return 0
    fi
  fi
  if _engine_usable docker && docker compose version >/dev/null 2>&1; then
    if [[ "$podman_seen" == "true" ]]; then
      warn "podman found but no usable compose implementation; falling back to docker compose"
      info "install podman-compose (or pass --engine podman) if the image exists only in podman storage"
    fi
    _set_compose docker "docker compose" docker compose
    return 0
  fi
  die "no usable container engine: need podman (+podman-compose) or docker (+docker compose)"
}

_engine_rootless() {
  case "$_ENGINE" in
    podman) [[ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo false)" == "true" ]] ;;
    docker) docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless ;;
    *) return 1 ;;
  esac
}

# Every compose call runs from the repo root with an absolute -f, so the command
# behaves the same no matter which directory the caller is in (project name,
# .env lookup). We deliberately avoid `--project-directory`: podman-compose only
# grew that flag in recent releases, while `cd` works everywhere.
_compose_files() {
  printf '%s\n' "-f" "$PROJECT_ROOT/docker-compose.yml"
}

_compose_run() {
  ( cd "$PROJECT_ROOT" && "${_COMPOSE[@]}" "$@" )
}

# Resolve the container compose just created. Compose labels are stable across
# implementations (docker compose v2 and podman-compose both set them), which is
# more reliable than guessing "<project>_<service>_1" — and it lets the
# postflight check use `<engine> exec` directly instead of `compose exec`,
# whose behaviour differs between providers.
_container_id() {
  local cid=""
  cid=$("$_ENGINE" ps -q \
      --filter "label=com.docker.compose.service=leap-aide" \
      --filter "label=com.docker.compose.project.working_dir=${PROJECT_ROOT}" \
      2>/dev/null | head -1 || true)
  if [[ -z "$cid" ]]; then
    cid=$("$_ENGINE" ps -q \
        --filter "label=com.docker.compose.service=leap-aide" \
        2>/dev/null | head -1 || true)
  fi
  printf '%s\n' "$cid"
}

_container_running() {
  [[ "$("$_ENGINE" inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" == "true" ]]
}

# Rootless podman maps container uid 0 to the invoking host user and everything
# else into the subuid range (e.g. container 1000 → host 100999), so a plain
# `WANTED_UID=$(id -u)` would write files the host user cannot modify.
# userns_mode=keep-id pins the invoking uid 1:1 instead. Only podman-compose
# understands keep-id; docker-compose accepts just `host`.
_prepare_userns_override() {
  _OVERRIDE_FILE=""
  if [[ "$_COMPOSE_KIND" != "podman-compose" ]] || ! _engine_rootless; then
    return 0
  fi
  local f
  f=$(mktemp "${TMPDIR:-/tmp}/leap-aide-userns.XXXXXX") || return 1
  printf 'services:\n  leap-aide:\n    userns_mode: keep-id\n' >"$f" || return 1
  _CTL_TMP_FILES+=("$f")
  _OVERRIDE_FILE="$f"
}

# ── Command: start-docker ──
cmd_start_docker() {
  local image="" engine_opt="" print_only=false pull=false follow=false
  local -a extra_files=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --engine)
        [[ -n "${2:-}" ]] || die "--engine needs a value (podman|docker)"
        engine_opt="$2"; shift 2 ;;
      --engine=*) engine_opt="${1#*=}"; [[ -n "$engine_opt" ]] || die "--engine needs a value (podman|docker)"; shift ;;
      -f|--file)
        [[ -n "${2:-}" ]] || die "$1 needs a compose file argument"
        extra_files+=("$2"); shift 2 ;;
      --print)    print_only=true; shift ;;
      --pull)     pull=true; shift ;;
      --logs)     follow=true; shift ;;
      -h|--help)
        cat <<'USAGE'
Usage: ctl.sh start-docker [IMAGE] [OPTIONS]
  IMAGE            container image (default: $LEAP_AIDE_IMAGE or the compose default)
  --engine ENGINE  podman | docker (default: auto-detect, podman first)
  --print          print the resolved command instead of running it
  --pull           pull the image before starting
  --logs           follow the container log after start
USAGE
        return 0 ;;
      -*) die "start-docker: unknown option '$1'" ;;
      *)
        [[ -z "$image" ]] || die "start-docker: unexpected argument '$1'"
        image="$1"; shift ;;
    esac
  done

  _container_guard start-docker
  _detect_compose "$engine_opt"

  if [[ -n "$image" ]]; then
    export LEAP_AIDE_IMAGE="$image"
  fi

  # Bind mounts are not uid-translated by the kernel: the container's numeric
  # uid must equal the owner of the host directories, or the service writes
  # files the host user cannot modify. Callers may override; otherwise use the
  # invoking user, which is who owns $HOME (and therefore the mounts).
  export WANTED_UID="${WANTED_UID:-$(id -u)}"
  export WANTED_GID="${WANTED_GID:-$(id -g)}"
  if [[ "$WANTED_UID" != "$(id -u)" || "$WANTED_GID" != "$(id -g)" ]]; then
    warn "WANTED_UID:WANTED_GID ${WANTED_UID}:${WANTED_GID} differs from the invoking user $(id -u):$(id -g); mounted files will be owned by the container uid"
  fi
  if [[ $EUID -eq 0 ]]; then
    warn "running as root (HOME=$HOME): the mounts come from root's home and the service runs as uid ${WANTED_UID}"
  fi

  local -a files
  mapfile -t files < <(_compose_files)
  if [[ ${#extra_files[@]} -gt 0 ]]; then
    local ef
    for ef in "${extra_files[@]}"; do
      files+=(-f "$ef")
    done
  fi
  _prepare_userns_override || die "could not create the userns override file"
  if [[ -n "$_OVERRIDE_FILE" ]]; then
    files+=(-f "$_OVERRIDE_FILE")
    info "rootless podman detected: adding userns_mode=keep-id (container uid == host uid)"
  elif _engine_rootless; then
    if [[ "$_ENGINE" == "docker" ]]; then
      warn "rootless docker detected: ownership follows the daemon's userns-remap; the postflight check will verify"
    else
      die "rootless podman with '${_COMPOSE_KIND}' cannot express userns_mode=keep-id; install podman-compose or pass --engine docker"
    fi
  fi

  if [[ "$print_only" == "true" ]]; then
    printf 'WANTED_UID=%s WANTED_GID=%s' "$WANTED_UID" "$WANTED_GID"
    if [[ -n "${LEAP_AIDE_IMAGE:-}" ]]; then
      printf ' LEAP_AIDE_IMAGE=%s' "$LEAP_AIDE_IMAGE"
    fi
    printf ' (cd %s && %s %s up -d)\n' "$PROJECT_ROOT" "${_COMPOSE[*]}" "${files[*]}"
    return 0
  fi

  if [[ "$pull" == "true" ]]; then
    info "pulling the image for service leap-aide"
    _compose_run "${files[@]}" pull
  fi

  info "starting leap-aide (engine: ${_COMPOSE_KIND}, uid: ${WANTED_UID}, gid: ${WANTED_GID})"
  _compose_run "${files[@]}" up -d

  # ── postflight: prove the mapping instead of assuming it ──
  # A successful `up -d` says nothing about file ownership, and nothing about
  # whether the container is even still alive (the entrypoint may have exited
  # immediately — e.g. a uid collision). Write one file from inside the
  # container, check who owns it on the host, and bail out the moment the
  # container dies.
  local probe="$HOME/.hermes_workspace/.uid_probe"
  local expected="$WANTED_UID"
  local actual="" cid=""

  if [[ "$(uname -s)" != "Linux" ]]; then
    warn "postflight ownership check skipped on $(uname -s) (no stat -c)"
  else
    cid=$(_container_id)
    if [[ -z "$cid" ]]; then
      warn "postflight: no container found for service leap-aide"
      warn "  check: $_ENGINE ps -a --filter label=com.docker.compose.service=leap-aide"
      exit 1
    fi

    local deadline=$((SECONDS + 30))
    while (( SECONDS < deadline )); do
      # Exec as WANTED_UID — the uid the service actually runs as — not the
      # image's default USER (leap-aide-init). The default user cannot write a
      # 755 /workspace owned by another uid, so the probe file never appears and
      # the check times out even when the service itself runs fine.
      if "$_ENGINE" exec -u "$WANTED_UID" "$cid" sh -c 'touch /workspace/.uid_probe' >/dev/null 2>&1; then
        actual=$(stat -c '%u' "$probe" 2>/dev/null || true)
        if [[ -n "$actual" ]]; then
          break
        fi
      elif ! _container_running "$cid"; then
        warn "container exited during startup — recent logs:"
        "$_ENGINE" logs --tail 40 "$cid" >&2 || true
        warn "the container is left in place for inspection; remove it with: $0 stop-docker"
        exit 1
      fi
      sleep 1
    done
    rm -f "$probe" 2>/dev/null || true

    if [[ -z "$actual" ]]; then
      warn "postflight: could not verify /workspace ownership within 30s (container ${cid})"
      warn "  check: $_ENGINE exec ${cid} stat -c '%u' /workspace"
      exit 1
    elif [[ "$actual" != "$expected" ]]; then
      warn "ownership mismatch: container writes files as host uid ${actual}, expected ${expected}"
      warn "rootless podman needs userns_mode=keep-id (install podman-compose); rootless docker needs daemon-level userns-remap"
      warn "container left running for inspection; remove it with: $0 stop-docker"
      exit 1
    else
      info "postflight OK: files written by the container are owned by host uid ${actual}"
    fi
  fi

  if [[ "$follow" == "true" ]]; then
    _compose_run "${files[@]}" logs -f
  fi
}

# ── Command: stop-docker ──
cmd_stop_docker() {
  local engine_opt=""
  local -a extra_files=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --engine)
        [[ -n "${2:-}" ]] || die "--engine needs a value (podman|docker)"
        engine_opt="$2"; shift 2 ;;
      --engine=*) engine_opt="${1#*=}"; [[ -n "$engine_opt" ]] || die "--engine needs a value (podman|docker)"; shift ;;
      -f|--file)
        [[ -n "${2:-}" ]] || die "$1 needs a compose file argument"
        extra_files+=("$2"); shift 2 ;;
      -h|--help) echo "Usage: ctl.sh stop-docker [--engine podman|docker] [-f file]"; return 0 ;;
      *) die "stop-docker: unknown option '$1'" ;;
    esac
  done
  _container_guard stop-docker
  _detect_compose "$engine_opt"

  local -a files
  mapfile -t files < <(_compose_files)
  if [[ ${#extra_files[@]} -gt 0 ]]; then
    local ef
    for ef in "${extra_files[@]}"; do
      files+=(-f "$ef")
    done
  fi
  _prepare_userns_override || die "could not create the userns override file"
  if [[ -n "$_OVERRIDE_FILE" ]]; then
    files+=(-f "$_OVERRIDE_FILE")
  fi
  info "stopping leap-aide (engine: ${_COMPOSE_KIND})"
  _compose_run "${files[@]}" down --remove-orphans
}

# ── Main entry ──
_usage() {
  cat <<'USAGE'
Usage: ctl.sh <command> [args...]
       (one command per invocation)

Server (inside the container / local dev):
  start       Start leap-aide (server + gateway)
  stop        Gracefully stop all services
  restart     Restart (stop + start)
  status      Show running status
  log-path    Print the server log path (for log following/tailing)

Container (host side, drives compose):
  start-docker [IMAGE] [--engine podman|docker] [--print] [--pull] [--logs]
              Start the container; podman is preferred over docker
  stop-docker [--engine podman|docker]
              Stop and remove the container (compose down)

For build operations, use: ./scripts/build.sh
USAGE
}

if [[ $# -eq 0 ]]; then
  _usage
  exit 1
fi

cmd="$1"
shift

case "$cmd" in
  start)        cmd_start ;;
  stop)         cmd_stop ;;
  restart)      cmd_restart ;;
  status)       cmd_status ;;
  log-path)     cmd_log_path ;;
  start-docker) cmd_start_docker "$@" ;;
  stop-docker)  cmd_stop_docker "$@" ;;
  build|install)
    echo "[ctl] WARNING: '$cmd' command is deprecated."
    echo "[ctl] Use './scripts/build.sh' for build operations."
    echo "[ctl] Use './install.sh' for installation."
    exit 1
    ;;
  *) die "Unknown command: $cmd" ;;
esac

echo "[ctl] Total: $((SECONDS - _CTL_START))s"
