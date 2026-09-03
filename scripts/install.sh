#!/usr/bin/env bash
# install.sh — One-line installer for Leap Aide
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/.../scripts/install.sh | bash
#   curl -fsSL .../scripts/install.sh | bash -s -- --commit abc123 --dir /opt/leap-aide
# 
# Envs:
#   PLAYWRIGHT_DOWNLOAD_HOST - Specify to the playwright mirrors.
#
set -euo pipefail

# ── Guard against environment leakage ──
if [ -n "${PYTHONPATH:-}" ]; then
  echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
  unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
  echo "⚠ Ignoring inherited PYTHONHOME during install"
  unset PYTHONHOME
fi

# ── TTY-aware colors ──
if [ -t 1 ]; then
  B=$'\033[1m'   # bold
  DIM=$'\033[2m' # dim
  R=$'\033[0m'   # reset
  GRN=$'\033[32m'
  YLW=$'\033[33m'
  RED=$'\033[31m'
else
  B='' DIM='' R='' GRN='' YLW='' RED=''
fi

# ── Output helpers ──
say()  { printf '%s\n' "$*"; }
ok()   { printf "${GRN}✓${R} %s\n" "$*"; }
warn() { printf "${YLW}!${R} %s\n" "$*"; }
die()  { printf "${RED}✗${R} %s\n" "$*" >&2; exit 1; }
info() { printf "${DIM}%s${R}\n" "$*"; }

# ── prompt_yes_no helper ──
prompt_yes_no() {
  local question="$1"
  local default="${2:-yes}"
  local prompt

  if [ "$default" = "yes" ]; then
    prompt="$question [Y/n] "
  else
    prompt="$question [y/N] "
  fi

  # Non-interactive mode: return default
  if [ "${NON_INTERACTIVE:-false}" = true ]; then
    if [ "$default" = "yes" ]; then
      return 0
    else
      return 1
    fi
  fi

  # Try reading from /dev/tty (works when stdin is piped)
  if [ -t 0 ]; then
    read -p "$prompt" -n 1 -r
    echo
  elif [ -e /dev/tty ]; then
    read -p "$prompt" -n 1 -r < /dev/tty
    echo
  else
    # No terminal available, use default
    info "$question (defaulting to $default)"
    if [ "$default" = "yes" ]; then
      return 0
    else
      return 1
    fi
  fi

  if [[ $REPLY =~ ^[Yy]$ ]]; then
    return 0
  elif [[ $REPLY =~ ^[Nn]$ ]]; then
    return 1
  else
    # Empty reply (just pressed Enter) — use default
    if [ "$default" = "yes" ]; then
      return 0
    else
      return 1
    fi
  fi
}

# ── Docker detection ──
is_docker() {
  # Check for Docker-specific files
  if [ -f /.dockerenv ] || [ -f /.within_container ]; then
    return 0
  fi
  # Check cgroup (covers older Docker and containerd)
  if grep -qE 'docker|containerd' /proc/1/cgroup 2>/dev/null; then
    return 0
  fi
  return 1
}

# ── Configuration (defaults) ──
REPO_URL="${LEAP_AIDE_REPO:-https://github.com/nousresearch/leap-aide.git}"
DEST="${LEAP_AIDE_DIR:-$HOME/leap-aide}"
HERMES_AGENT_INSTALLER="https://hermes-agent.nousresearch.com/install.sh"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BRANCH="main"

# ── Options (parsed from arguments) ──
COMMIT_SHA=""
USE_VENV=true
SKIP_BUILD=false
NON_INTERACTIVE=false
FORCE=false
INSTALL_SERVICE=true
NPM_REGISTRY=""
DEBUG_INSTALL=false

# Detect non-interactive mode (e.g. curl | bash)
if [ -t 0 ]; then
  IS_INTERACTIVE=true
else
  IS_INTERACTIVE=false
  NON_INTERACTIVE=true
fi

# Detect Docker environment
IN_DOCKER=false
if is_docker; then
  IN_DOCKER=true
fi

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
  case $1 in
    --dir)
      DEST="$2"
      shift 2
      ;;
    --repo)
      REPO_URL="$2"
      shift 2
      ;;
    --hermes-home)
      HERMES_HOME="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --commit)
      COMMIT_SHA="$2"
      shift 2
      ;;
    --no-venv)
      USE_VENV=false
      shift
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    --no-service)
      INSTALL_SERVICE=false
      shift
      ;;
    --npm-registry)
      NPM_REGISTRY="$2"
      shift 2
      ;;
    --debug)
      DEBUG_INSTALL=true
      shift
      ;;
    --help|-h)
      echo ""
      echo "┌─────────────────────────────────────────────────────────┐"
      echo "│                                                         │"
      echo "│                 Leap Aide Installer                     │"
      echo "│                                                         │"
      echo "└─────────────────────────────────────────────────────────┘"
      echo ""
      echo "Usage: install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --dir PATH          Installation directory (default: ~/leap-aide)"
      echo "  --repo URL          Git repository URL (default: GitHub). Local directory uses symlink install."
      echo "  --hermes-home PATH  Data directory (default: ~/.hermes)"
      echo "  --branch NAME       Git branch to install (default: main)"
      echo "  --commit SHA        Pin to specific commit after clone"
      echo "  --no-venv           Skip virtual environment creation"
      echo "  --skip-build        Skip build step (frontend + Python)"
      echo "  --non-interactive   Skip prompts (for scripted installs)"
      echo "  --force             Force reinstall even if exists"
      echo "  --no-service        Skip systemd service installation"
      echo "  --npm-registry URL  npm registry (default: npmjs.org; use https://registry.npmmirror.com for China mirror)"
      echo "  --debug             Enable verbose logging (electron download, npm, etc.)"
      echo "  -h, --help          Show this help"
      echo ""
      echo "Environment variables:"
      echo "  LEAP_AIDE_REPO    Git repository URL"
      echo "  LEAP_AIDE_DIR     Installation directory"
      echo "  HERMES_HOME       Data directory"
      exit 0
      ;;
    *)
      die "Unknown option: $1. Use --help for usage."
      ;;
  esac
done

# ── Detect local repo (for symlink install) ──
LOCAL_REPO=""
if [[ "$REPO_URL" == /* || "$REPO_URL" == file://* ]]; then
  LOCAL_REPO="${REPO_URL#file://}"
fi

# ── Helper: detect package manager ──
detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v apk >/dev/null 2>&1; then
    echo "apk"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman"
  else
    echo "unknown"
  fi
}

# ── Helper: install package with spinner ──
install_package() {
  local pkgs=("$@")
  local mgr
  mgr=$(detect_pkg_manager)
  local log_file="/tmp/install-${pkgs[0]}.log"

  # Spinner characters (rotating line)
  local spin_chars='|/-\'
  local spin_pid
  local is_tty=false
  [ -t 1 ] && is_tty=true

  # Start install in background
  case $mgr in
    apt)
      (apt-get update -qq >>"$log_file" 2>&1 && apt-get install -y -qq "${pkgs[@]}" >>"$log_file" 2>&1) &
      ;;
    yum)
      yum install -y -q "${pkgs[@]}" >>"$log_file" 2>&1 &
      ;;
    dnf)
      dnf install -y -q "${pkgs[@]}" >>"$log_file" 2>&1 &
      ;;
    apk)
      apk add --no-cache "${pkgs[@]}" >>"$log_file" 2>&1 &
      ;;
    pacman)
      pacman -Sy --noconfirm "${pkgs[@]}" >>"$log_file" 2>&1 &
      ;;
    *)
      return 1
      ;;
  esac
  spin_pid=$!

  # Show spinner while installing (only on TTY, otherwise just wait)
  if [ "$is_tty" = true ]; then
    local i=0
    while kill -0 "$spin_pid" 2>/dev/null; do
      local char="${spin_chars:i++%${#spin_chars}:1}"
      printf "\r  %s Installing %s..." "$char" "${pkgs[*]}"
      sleep 0.1
    done
  else
    # Non-TTY: just print a message and wait
    printf "  Installing %s...\n" "${pkgs[*]}"
    wait "$spin_pid" 2>/dev/null || true
  fi

  # Wait for actual result
  wait "$spin_pid"
  local rc=$?

  # Clear the spinner line (only on TTY)
  if [ "$is_tty" = true ]; then
    printf "\r\033[K"
  fi

  # Show error log on failure
  if [ $rc -ne 0 ] && [ -f "$log_file" ]; then
    warn "Installation failed. Log:"
    cat "$log_file" >&2
    rm -f "$log_file"
  fi

  return $rc
}

# ── Step 1: Check basic prerequisites ──
say ""
say "${GRN}${B}┌─────────────────────────────────────────────────────────┐${R}"
say "${GRN}${B}│                                                         │${R}"
say "${GRN}${B}│                 Leap Aide Installer                     │${R}"
say "${GRN}${B}│                                                         │${R}"
say "${GRN}${B}└─────────────────────────────────────────────────────────┘${R}"
say ""

# Show environment info
if [ "$IN_DOCKER" = true ]; then
  info "Docker environment detected"
fi

# Detect and propagate proxy settings
for _var in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY; do
  if [ -n "${!_var:-}" ]; then
    export "$_var"
  fi
done
if [ -n "${http_proxy:-}" ] || [ -n "${HTTP_PROXY:-}" ]; then
  ok "Proxy detected"
fi

# git — try to install if missing
if ! command -v git >/dev/null 2>&1; then
  warn "git not found — attempting to install..."
  if install_package git; then
    ok "git installed"
    hash -r 2>/dev/null || true
  else
    die "git is required but could not be installed automatically. Install it manually."
  fi
fi
ok "git $(git --version | sed 's/git version //')"

# curl — hermes-agent installer requires curl specifically (not wget)
if ! command -v curl >/dev/null 2>&1; then
  warn "curl not found — attempting to install..."
  if install_package curl; then
    ok "curl installed"
    hash -r 2>/dev/null || true
  elif command -v wget >/dev/null 2>&1; then
    warn "curl install failed, wget available as fallback for our scripts"
  else
    die "curl is required but could not be installed automatically. Install it manually: apt-get install curl"
  fi
fi
if command -v curl >/dev/null 2>&1; then
  ok "curl $(curl --version | head -1 | awk '{print $2}')"
elif command -v wget >/dev/null 2>&1; then
  warn "curl not available — wget will be used where possible"
fi

# System libraries needed by Node.js (libatomic) and general build tools
if [ "$IN_DOCKER" = true ]; then
  _sys_deps_to_install=""
  # libatomic1 is required by Node.js 26+
  if ! ldconfig -p 2>/dev/null | grep -q libatomic; then
    _sys_deps_to_install="${_sys_deps_to_install} libatomic1"
  fi
  # xz-utils is required for .tar.xz extraction (Node.js downloads)
  if ! command -v xz >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} xz-utils"
  fi
  # python3-venv for virtual environment creation (ensurepip is often missing in Docker)
  # Always try to install — apt-get is idempotent and skips already-installed packages
  if command -v python3 >/dev/null 2>&1; then
    _python_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    _sys_deps_to_install="${_sys_deps_to_install} python${_python_ver}-venv"
  fi
  # build-essential for native Node.js addons (node-pty etc.)
  if ! command -v make >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} build-essential"
  fi
  # ca-certificates for HTTPS
  if [ ! -f /etc/ssl/certs/ca-certificates.crt ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ]; then
    _sys_deps_to_install="${_sys_deps_to_install} ca-certificates"
  fi
  # VNC observation dependencies (Xvfb, x11vnc, openbox, wmctrl)
  if ! command -v Xvfb >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} xvfb"
  fi
  if ! command -v x11vnc >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} x11vnc"
  fi
  if ! command -v openbox >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} openbox"
  fi
  if ! command -v wmctrl >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} wmctrl"
  fi
  # Chinese locale support
  if ! locale -a 2>/dev/null | grep -q 'zh_CN.UTF-8'; then
    _sys_deps_to_install="${_sys_deps_to_install} locales"
  fi
  # Fcitx5 input method for Chinese input
  if ! command -v fcitx5 >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 dbus-x11 python3-dbus"
  fi
  if [ -n "$_sys_deps_to_install" ]; then
    warn "Installing system dependencies:${_sys_deps_to_install}"
    # shellcheck disable=SC2086
    if install_package $_sys_deps_to_install; then
      ok "System dependencies installed"
      # Refresh shell command cache after installing new packages
      hash -r 2>/dev/null || true
    else
      warn "Some system deps failed to install"
    fi
  fi

  # Configure Chinese locale
  if ! locale -a 2>/dev/null | grep -q 'zh_CN.UTF-8'; then
    warn "Configuring Chinese locale..."
    sed -i 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen zh_CN.UTF-8 2>&1 || warn "Failed to generate zh_CN.UTF-8 locale"
    ok "Chinese locale configured"
  fi

  # fcitx5 trigger key is configured at runtime via D-Bus SetConfig
  # (see api/session_display.py _configure_fcitx5_trigger_key)
  if command -v fcitx5 >/dev/null 2>&1; then
    ok "fcitx5 installed (trigger key set at session start via D-Bus)"
  fi
fi

# ── Step 2: Ensure hermes-agent (handles node, python deps, etc.) ──
say ""
say "${B}Checking hermes-agent...${R}"

_hermes_agent_found() {
  # Check common locations for hermes-agent
  local candidates=(
    "${HERMES_WEBUI_AGENT_DIR:-}"
    "/usr/local/lib/hermes-agent"
    "$HERMES_HOME/hermes-agent"
    "$HOME/hermes-agent"
  )
  for dir in "${candidates[@]}"; do
    if [ -n "$dir" ] && [ -d "$dir" ] && [ -f "$dir/run_agent.py" ]; then
      echo "$dir"
      return 0
    fi
  done
  return 1
}

# Set PLAYWRIGHT_DOWNLOAD_HOST and no_proxy BEFORE hermes-agent install
# so that hermes-agent's internal Playwright download uses the mirror directly
if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST:-}" ]; then
  info "Using Playwright mirror: $PLAYWRIGHT_DOWNLOAD_HOST"
  # Extract host:port from mirror URL and add to no_proxy so Node.js doesn't route through proxy
  _mirror_host=$(echo "$PLAYWRIGHT_DOWNLOAD_HOST" | sed -E 's|^https?://||;s|/.*||')
  if [ -n "$_mirror_host" ]; then
    export no_proxy="${no_proxy:+$no_proxy,}$_mirror_host"
    export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$_mirror_host"
  fi
fi

# Set npm registry if specified (useful for China mirror).
# Use env vars so hermes-agent's managed npm also picks it up.
if [ -n "$NPM_REGISTRY" ]; then
  export npm_config_registry="$NPM_REGISTRY"
  export NPM_CONFIG_REGISTRY="$NPM_REGISTRY"
  if command -v npm >/dev/null 2>&1; then
    npm config set registry "$NPM_REGISTRY"
  fi
  ok "npm registry set to $NPM_REGISTRY"
  # Auto-set Electron mirror for China registry
  if [[ "$NPM_REGISTRY" == *"npmmirror"* ]]; then
    export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
    info "Electron mirror set to npmmirror.com"
  fi
fi

# Enable debug logging if requested
if [ "$DEBUG_INSTALL" = true ]; then
  export DEBUG="${DEBUG:+$DEBUG,}@electron/get:*"
  info "Debug logging enabled (electron download details)"
fi

AGENT_DIR=$(_hermes_agent_found || true)
if [ -n "$AGENT_DIR" ]; then
  ok "hermes-agent found at ${B}$AGENT_DIR${R}"
elif command -v hermes >/dev/null 2>&1; then
  ok "hermes command found in PATH"
else
  warn "hermes-agent not found — installing..."
  # Use curl or wget whichever is available
  # hermes-agent installer may return non-zero for optional components (e.g. npm)
  # so we don't fail on exit code — we verify the core files instead
  # --skip-browser: don't run `npx playwright install chromium` inside the
  # third-party installer.  playwright-core <1.60.0 hangs on Node.js v26+
  # (nodejs/node#63487).  We upgrade playwright-core ourselves below, then
  # download the browser with the fixed version.
  #
  # Override npm to force --loglevel=verbose and retry on network errors.
  # hermes-agent's installer uses `npm install --silent` which hides all output.
  # Network flakiness (ECONNRESET) can cause transient failures.
  npm() {
    local retries=3
    for ((i = 1; i <= retries; i++)); do
      if command npm "$@" --loglevel=verbose; then
        return 0
      fi
      if ((i < retries)); then
        echo "[npm retry] Attempt $i/$retries failed, retrying in 5s..." >&2
        sleep 5
      fi
    done
    echo "[npm retry] All $retries attempts failed" >&2
    return 1
  }
  export -f npm
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$HERMES_AGENT_INSTALLER" | bash -s -- --skip-browser || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$HERMES_AGENT_INSTALLER" | bash -s -- --skip-browser || true
  else
    die "curl or wget is required to download hermes-agent."
  fi
  AGENT_DIR=$(_hermes_agent_found || true)
  if [ -n "$AGENT_DIR" ]; then
    ok "hermes-agent installed at ${B}$AGENT_DIR${R}"
  elif command -v hermes >/dev/null 2>&1; then
    ok "hermes command installed"
  else
    die "hermes-agent installation failed. Check the output above for errors."
  fi
fi

# ── Fix playwright-core version & install browser ──
# hermes-agent bundles playwright-core <1.60.0 which hangs on Node.js v26+
# (nodejs/node#63487, microsoft/playwright#40998).  Upgrade before downloading.
AGENT_DIR=$(_hermes_agent_found || true)
if [ -n "$AGENT_DIR" ] && [ -d "$AGENT_DIR/node_modules/playwright-core" ] && command -v node >/dev/null 2>&1; then
  _pw_ver=$(node -e "try{console.log(require('$AGENT_DIR/node_modules/playwright-core/package.json').version)}catch(e){}" 2>/dev/null || true)
  if [ -n "$_pw_ver" ]; then
    _pw_major=${_pw_ver%%.*}
    _pw_minor=${_pw_ver#*.}; _pw_minor=${_pw_minor%%.*}
    if [ "$_pw_major" -eq 1 ] && [ "$_pw_minor" -lt 60 ] 2>/dev/null; then
      warn "playwright-core $_pw_ver has Node.js v26 hang bug — upgrading..."
      # Upgrade all three playwright packages — npx resolves to @playwright/test,
      # so upgrading only playwright-core is not enough.
      npm install --prefix "$AGENT_DIR" \
        playwright-core@'^1.60.0' playwright@'^1.60.0' @playwright/test@'^1.60.0' \
        --save --quiet 2>&1 || true
      _pw_new=$(node -e "try{console.log(require('$AGENT_DIR/node_modules/playwright-core/package.json').version)}catch(e){}" 2>/dev/null || true)
      ok "playwright-core: $_pw_ver → ${_pw_new:-done}"
    fi
  fi
  # Download Chromium if not already cached
  if ! find "$HOME/.cache/ms-playwright" -name "chrome" -o -name "chromium" 2>/dev/null | head -1 | grep -q .; then
    info "Downloading Playwright Chromium..."
    (cd "$AGENT_DIR" && npx playwright install chromium 2>&1) || warn "Chromium download failed — browser tools may not work"
    # Install system dependencies for Chromium on apt-based systems
    if command -v apt-get >/dev/null 2>&1; then
      (cd "$AGENT_DIR" && npx playwright install-deps chromium 2>&1) || true
    fi
  fi
  if find "$HOME/.cache/ms-playwright" -name "chrome" -o -name "chromium" 2>/dev/null | head -1 | grep -q .; then
    ok "Playwright Chrome verified"
  fi
  # Install Browser Use CLI (hermes-agent's default browser backend).
  # --skip-browser skipped this; install it now since it has no Node.js bug.
  if [ -x "${HERMES_HOME:-$HOME/.hermes}/bin/uv" ] && [ ! -x "${HERMES_HOME:-$HOME/.hermes}/bin/browser-use" ]; then
    info "Installing Browser Use CLI..."
    env UV_NO_CONFIG=1 UV_TOOL_BIN_DIR="${HERMES_HOME:-$HOME/.hermes}/bin" \
      "${HERMES_HOME:-$HOME/.hermes}/bin/uv" tool install browser-use 2>&1 || warn "Browser Use CLI install failed — fallback to built-in tools"
  fi
fi

# ── Step 2b: Apply Leap Aide defaults to hermes config ──
say ""
say "${B}Configuring hermes defaults...${R}"

HERMES_CONFIG="${HERMES_HOME}/config.yaml"
if [ -f "$HERMES_CONFIG" ]; then
  # Ensure display.show_reasoning is true — Leap Aide displays reasoning
  # blocks in the chat UI, so the backend must persist them.
  # Use python3 + pyyaml to safely modify YAML (preserves comments/structure).
  # Fall back to a simple text patch if pyyaml is not yet installed.
  python3 - "$HERMES_CONFIG" <<'PYEOF'
import sys, os
cfg = sys.argv[1]
try:
    import yaml
except ImportError:
    # pyyaml not installed — fall back to text patch
    with open(cfg) as f:
        lines = f.readlines()
    found_display = False
    patched = False
    out = []
    for line in lines:
        out.append(line)
        stripped = line.rstrip()
        if stripped == "display:":
            found_display = True
        elif found_display and "show_reasoning:" in stripped:
            indent = len(line) - len(line.lstrip())
            out[-1] = " " * indent + "show_reasoning: true\n"
            patched = True
            found_display = False
        elif found_display and not stripped.startswith(" ") and stripped and not stripped.startswith("#"):
            found_display = False
    if not patched:
        new_out = []
        for line in out:
            new_out.append(line)
            if line.rstrip() == "display:":
                new_out.append("  show_reasoning: true\n")
        out = new_out
    with open(cfg, "w") as f:
        f.writelines(out)
    print("  ✓ show_reasoning: true (text patch)")
    sys.exit(0)

# Full YAML path
with open(cfg) as f:
    data = yaml.safe_load(f) or {}
display = data.get("display")
if not isinstance(display, dict):
    display = {}
    data["display"] = display
old = display.get("show_reasoning")
if old is True:
    print("  ✓ show_reasoning already true")
else:
    display["show_reasoning"] = True
    with open(cfg, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    print(f"  ✓ show_reasoning: {old!r} → true")
PYEOF
  ok "Hermes defaults applied"
else
  info "No hermes config found — will use defaults on first run"
fi

# ── Step 3: Check our own dependencies ──
say ""
say "${B}Checking dependencies...${R}"

# python3 (3.11+) — try to install if missing
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found — attempting to install..."
  if install_package python3; then
    ok "python3 installed"
  else
    die "python3 is required but could not be installed automatically. Install Python 3.11+ manually."
  fi
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJ=${PYVER%%.*}
PYMIN=${PYVER#*.}
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 11 ]; }; then
  die "Python 3.11+ required, found $PYVER"
fi
ok "Python $PYVER"

# node (optional for runtime, required for build)
if command -v node >/dev/null 2>&1; then
  NODEVER=$(node -v | sed 's/^v//' | cut -d. -f1)
  if [ "$NODEVER" -lt 18 ]; then
    warn "Node.js 18+ recommended for frontend build, found v$NODEVER"
  else
    ok "Node.js $(node -v)"
  fi
else
  warn "Node.js not found — frontend will not be built (pre-built static/ will be used if present)"
fi

# ── Step 3: Clone or update repo ──
say ""
say "${B}Installing Leap Aide to ${DEST}...${R}"

# Handle git safe.directory for cross-user/local mounts
if [ -n "$REPO_URL" ] && [[ "$REPO_URL" == /* || "$REPO_URL" == file://* ]]; then
  # Local repo — resolve the actual path and add to safe.directory
  local_repo="${REPO_URL#file://}"
  if [ -d "$local_repo/.git" ]; then
    git config --global --add safe.directory "$local_repo" 2>/dev/null || true
    git config --global --add safe.directory "$local_repo/.git" 2>/dev/null || true
    # Auto-detect default branch if not explicitly set
    if [ "$BRANCH" = "main" ]; then
      # For local repos, prefer current working branch
      detected_branch=$(git -C "$local_repo" branch --show-current 2>/dev/null || true)
      if [ -z "$detected_branch" ]; then
        detected_branch=$(git -C "$local_repo" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || true)
      fi
      if [ -n "$detected_branch" ]; then
        BRANCH="$detected_branch"
        info "Auto-detected branch: $BRANCH"
      fi
    fi
  fi
fi

# Force remove if requested (only for git clones, not local symlinks)
if [ "$FORCE" = true ] && [ -d "$DEST" ] && [ -z "$LOCAL_REPO" ]; then
  warn "Force mode: removing existing installation at $DEST"
  rm -rf "$DEST"
fi

if [ -n "$LOCAL_REPO" ]; then
  # ── Local repo: symlink install ──
  if [ ! -d "$LOCAL_REPO/.git" ]; then
    die "$LOCAL_REPO is not a git repository (missing .git directory)"
  fi

  _real_local_repo="$(cd "$LOCAL_REPO" && pwd -P)"

  if [ -L "$DEST" ]; then
    _existing_target="$(readlink "$DEST")"
    if [ "$_existing_target" = "$_real_local_repo" ]; then
      ok "Symlink already exists: $DEST → $_real_local_repo"
    elif [ "$FORCE" = true ]; then
      rm -f "$DEST"
      ln -s "$_real_local_repo" "$DEST"
      ok "Symlink updated: $DEST → $_real_local_repo"
    else
      die "$DEST is a symlink to $_existing_target (expected $_real_local_repo). Use --force to update."
    fi
  elif [ -d "$DEST" ]; then
    die "$DEST exists as a regular directory. Remove it first or use --force."
  else
    ln -s "$_real_local_repo" "$DEST"
    ok "Symlink created: $DEST → $_real_local_repo"
  fi
  cd "$DEST"
elif [ -d "$DEST/.git" ]; then
  # ── Remote repo: update existing clone ──
  say "Repository exists, updating..."
  cd "$DEST"

  # Fetch latest
  git fetch origin "$BRANCH" --depth 1 2>/dev/null || {
    warn "Could not fetch from origin — trying full fetch..."
    git fetch origin "$BRANCH"
  }

  if [ -n "$COMMIT_SHA" ]; then
    # Checkout specific commit
    if git cat-file -e "$COMMIT_SHA" 2>/dev/null; then
      git checkout "$COMMIT_SHA"
      ok "Checked out commit: $COMMIT_SHA"
    else
      warn "Commit $COMMIT_SHA not found in shallow clone — fetching full history..."
      git fetch --unshallow 2>/dev/null || git fetch
      git checkout "$COMMIT_SHA"
      ok "Checked out commit: $COMMIT_SHA"
    fi
  else
    # Fast-forward to latest
    if git reset --hard "origin/$BRANCH" 2>/dev/null; then
      ok "Repository updated to latest $BRANCH"
    else
      warn "Could not update — leaving $DEST as-is"
    fi
  fi
elif [ -d "$DEST" ]; then
  die "$DEST exists but is not a git repository. Remove it or set LEAP_AIDE_DIR to a different path."
else
  # ── Remote repo: fresh clone ──
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST"
  cd "$DEST"
  ok "Repository cloned (branch: $BRANCH)"

  # Checkout specific commit if provided
  if [ -n "$COMMIT_SHA" ]; then
    git fetch --unshallow 2>/dev/null || git fetch
    git checkout "$COMMIT_SHA"
    ok "Checked out commit: $COMMIT_SHA"
  fi
fi

# ── Step 4: Install Python dependencies ──
say ""
say "${B}Setting up Python environment...${R}"

if [ "$USE_VENV" = true ]; then
  VENV_DIR="$DEST/.venv"
  if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created"
  else
    info "Virtual environment already exists"
  fi

  VENV_PIP="$VENV_DIR/bin/pip"
  VENV_PYTHON="$VENV_DIR/bin/python"

  "$VENV_PIP" install --quiet --upgrade pip
  "$VENV_PIP" install --quiet -r "$DEST/requirements.txt"
  ok "Python dependencies installed"
else
  info "Skipping virtual environment (--no-venv)"
  pip3 install --quiet --upgrade pip
  pip3 install --quiet -r "$DEST/requirements.txt"
  ok "Python dependencies installed (system-wide)"
fi

# ── Step 5: Install wrappers ──
say ""
say "${B}Installing command wrappers...${R}"

if [ -f "$DEST/scripts/install-wrappers.sh" ]; then
  bash "$DEST/scripts/install-wrappers.sh" --force 2>&1
  ok "Command wrappers installed"
else
  warn "install-wrappers.sh not found — skipping"
fi

# ── Step 6: Build ──
if [ "$SKIP_BUILD" = false ]; then
  say ""
  say "${B}Building...${R}"
  bash "$DEST/scripts/build.sh" --all
else
  info "Skipping build (--skip-build)"
fi

# ── Step 8: Install systemd service (optional) ──
if [ "$INSTALL_SERVICE" = true ] && [ "$IN_DOCKER" = false ]; then
  if command -v systemctl &>/dev/null; then
    say ""
    say "${B}Systemd service${R}"

    if prompt_yes_no "Install leap-aide as a systemd user service?" "yes"; then
      SERVICE_DIR="$HOME/.config/systemd/user"
      mkdir -p "$SERVICE_DIR"

      cat > "$SERVICE_DIR/leap-aide.service" <<EOF
[Unit]
Description=Leap Aide Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory=$DEST
Environment="PATH=$DEST/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$DEST/.venv/bin/python bootstrap.py --no-browser
Restart=on-failure
RestartSec=5
EnvironmentFile=$DEST/.env

[Install]
WantedBy=default.target
EOF

      # Reload and enable
      systemctl --user daemon-reload 2>/dev/null || true
      systemctl --user enable leap-aide.service 2>/dev/null || true
      ok "Systemd service installed and enabled"

      if prompt_yes_no "Start leap-aide now?" "yes"; then
        systemctl --user start leap-aide.service 2>/dev/null || true
        ok "Service started"
      fi

      say ""
      say "Service commands:"
      say "  Start:   ${B}systemctl --user start leap-aide${R}"
      say "  Stop:    ${B}systemctl --user stop leap-aide${R}"
      say "  Status:  ${B}systemctl --user status leap-aide${R}"
      say "  Logs:    ${B}journalctl --user -u leap-aide -f${R}"
    fi
  else
    info "systemctl not found — skipping service installation"
  fi
elif [ "$IN_DOCKER" = true ]; then
  say ""
  info "Docker environment — systemd service skipped"
  info "Run with: docker compose up -d"
fi

# ── Done ──
say ""
say "${GRN}${B}✓ Installation complete!${R}"
say ""

if [ "$IN_DOCKER" = true ]; then
  say "${B}Docker commands:${R}"
  say "  Start:   ${B}docker compose up -d${R}"
  say "  Stop:    ${B}docker compose down${R}"
  say "  Update:  ${B}docker compose pull && docker compose up -d${R}"
else
  say "${B}Quick commands:${R}"
  say "  Start:   ${B}cd $DEST && ./scripts/ctl.sh start${R}"
  say "  Stop:    ${B}cd $DEST && ./scripts/ctl.sh stop${R}"
  say "  Status:  ${B}cd $DEST && ./scripts/ctl.sh status${R}"
  say "  Update:  ${B}cd $DEST && git pull && ./scripts/ctl.sh restart${R}"
  say "  Build:   ${B}cd $DEST && ./scripts/build.sh${R}"
fi
say ""
