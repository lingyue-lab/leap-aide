#!/usr/bin/env bash
# install.sh — One-line installer for LeapAide
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/.../scripts/install.sh | bash
#   curl -fsSL .../scripts/install.sh | bash -s -- --commit abc123 --dir /opt/leap-aide
# 
# Envs:
#   PLAYWRIGHT_DOWNLOAD_HOST - Specify to the playwright mirrors.
#   HERMES_AGENT_COMMIT      - Pin the hermes-agent SDK to a commit. Unset (and
#                              absent from .build.env) = install unpinned.
#   BUILD_ENV_FILE           - Path to the build defaults file to load instead
#                              of the auto-discovered .build.env.
#   Exported variables always win over .build.env values.
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

# ── Log file ──
LOG_FILE="/tmp/install-leap-aide-$(date +%Y%m%d-%H%M%S).log"
_log() { printf '%s\n' "$*" >> "$LOG_FILE"; }
_log "=== LeapAide Installer started at $(date) ==="
_log "Command: $0 $*"
_log ""

# ── Output helpers (write to both terminal and log file) ──
say()  { printf '%s\n' "$*"; _log "$*"; }
ok()   { printf "${GRN}✓${R} %s\n" "$*"; _log "✓ $*"; }
warn() { printf "${YLW}!${R} %s\n" "$*"; _log "! $*"; }
die()  { printf "${RED}✗${R} %s\n" "$*" >&2; _log "✗ $*"; exit 1; }
info() { printf "${DIM}%s${R}\n" "$*"; _log "  $*"; }

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
# Resolved AFTER option parsing so it always matches the hermes-agent ref being
# installed — see "Resolve the hermes-agent installer URL" below.
HERMES_AGENT_INSTALLER=""
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BRANCH="main"

# ── Options (parsed from arguments) ──
COMMIT_SHA=""
USE_VENV=true
SKIP_BUILD=false
NON_INTERACTIVE=false
FORCE=false
INSTALL_SERVICE=true
DEPS_ONLY=false
NPM_REGISTRY=""
# Each `=false` / `=""` below used to be a literal default that overrode any
# env var the caller (Dockerfile) exported — the script's local assignment
# wins over inherited env. Switch to `${VAR:-default}` so the caller's env
# always takes priority. This matters for DEBUG_INSTALL (which the Dockerfile
# sets to get @electron/get:* logs) and the hermes-agent pin (HERMES_COMMIT,
# HERMES_REPO_URL) which the Dockerfile passes via build-arg.
DEBUG_INSTALL="${DEBUG_INSTALL:-false}"
HERMES_REPO_URL="${HERMES_REPO_URL:-${HERMES_AGENT_REPO_URL:-}}"
HERMES_BRANCH="${HERMES_BRANCH:-}"
FORCE_HERMES_COMMIT="${FORCE_HERMES_COMMIT:-false}"
_HERMES_VERSION_SPECIFIED=false
USE_ALIYUN_MIRROR="${USE_ALIYUN_MIRROR:-false}"

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
      # Data/state dir for the hermes-agent installer (config, .env,
      # sessions, logs). Does NOT control where the code checkout lands:
      # root-Linux FHS installs put code at /usr/local/lib/hermes-agent
      # regardless of this value. Empty = installer default ($HOME/.hermes).
      # Pass `--hermes-home ""` to keep installer default.
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
    --deps-only)
      # Skip leap-aide self-install (clone/symlink). The caller is expected to
      # pass --dir <existing leap-aide source> so subsequent steps (Python
      # deps, wrappers) can still find leap-aide files at $DEST. systemd
      # service is already auto-skipped inside Docker.
      DEPS_ONLY=true
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
    --hermes-commit)
      HERMES_COMMIT="$2"
      _HERMES_VERSION_SPECIFIED=true
      shift 2
      ;;
    --hermes-branch)
      HERMES_BRANCH="$2"
      _HERMES_VERSION_SPECIFIED=true
      shift 2
      ;;
    --hermes-repo-url)
      # Pin to a custom hermes-agent git URL (e.g. a fork or private mirror).
      # Sets the HERMES_REPO_URL env var, which the hermes-agent installer
      # reads at startup:
      #   REPO_URL="${HERMES_REPO_URL:-https://github.com/NousResearch/hermes-agent.git}"
      # Empty = upstream NousResearch.
      HERMES_REPO_URL="$2"
      _HERMES_VERSION_SPECIFIED=true
      shift 2
      ;;
    --force-hermes-commit)
      FORCE_HERMES_COMMIT=true
      shift
      ;;
    --china-mirror|--aliyun-mirror)
      USE_ALIYUN_MIRROR=true
      shift
      ;;
    --help|-h)
      echo ""
      echo "┌─────────────────────────────────────────────────────────┐"
      echo "│                                                         │"
      echo "│                 LeapAide Installer                      │"
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
      echo "  --hermes-commit SHA Pin hermes-agent to a specific commit"
      echo "                      (default: HERMES_AGENT_COMMIT from .build.env;"
      echo "                      unset everywhere = no pin, tracks the default branch)"
      echo "  --hermes-branch NM  hermes-agent branch to install (overrides --hermes-commit)"
      echo "  --force-hermes-commit  Force hermes-agent commit even if it rolls back"
      echo "  --china-mirror      Use Tsinghua apt mirror for faster downloads in China"
      echo "  --deps-only         Skip leap-aide self-install (clone/symlink)."
      echo "                      Keeps system deps, hermes-agent, Playwright, browser-use,"
      echo "                      Python deps, command wrappers. Use --dir to point at an"
      echo "                      existing leap-aide source."
      echo "  -h, --help          Show this help"
      echo ""
      echo "Environment variables:"
      echo "  LEAP_AIDE_REPO    Git repository URL"
      echo "  LEAP_AIDE_DIR     Installation directory"
      echo "  HERMES_HOME       Data directory"
      echo "  BUILD_ENV_FILE    Build defaults file (default: .build.env beside this"
      echo "                    script, \$DEST/.build.env, or ./.build.env)"
      echo "  Exported variables always win over .build.env values."
      exit 0
      ;;
    *)
      die "Unknown option: $1. Use --help for usage."
      ;;
  esac
done

# ── Load build defaults (.build.env) ──
# Runs AFTER option parsing so --dir / LEAP_AIDE_DIR are already resolved.
# It used to sit above the parse loop, where it read the pre-parse $DEST and so
# never found the file in the Docker build (which passes --dir /app); the pin
# only reached the image through the Dockerfile's ENV.
#
# Precedence: the caller wins. Every key the file declares is snapshotted with
# `declare -p` before sourcing and restored afterwards, so an exported
# HERMES_AGENT_COMMIT (or any future field) is never clobbered by the file's
# default. Same idiom as scripts/ctl.sh — do not replace it with a hardcoded
# key list, or a field added to the file later will silently clobber callers.
_build_env_file=""
_self_dir=""
if [ -f "$0" ]; then
  _self_dir="$(cd "$(dirname "$0")" && pwd -P)"
fi
for _candidate in \
    "${BUILD_ENV_FILE:-}" \
    "$DEST/.build.env" \
    "${_self_dir:+$_self_dir/../.build.env}" \
    "./.build.env"; do
  if [ -n "$_candidate" ] && [ -f "$_candidate" ]; then
    _build_env_file="$_candidate"
    break
  fi
done
unset _candidate _self_dir
if [ -n "$_build_env_file" ]; then
  _build_env_keep=()
  while IFS= read -r _line || [ -n "$_line" ]; do
    _stmt="${_line#export }"
    case "$_stmt" in
      *=*) ;;
      *) continue ;;
    esac
    _key="${_stmt%%=*}"
    _key="${_key#"${_key%%[![:space:]]*}"}"
    _key="${_key%"${_key##*[![:space:]]}"}"
    case "$_key" in
      ''|*[!A-Za-z0-9_]*|[0-9]*) continue ;;
    esac
    if eval "[ -n \"\${${_key}+set}\" ]"; then
      _build_env_keep+=("$(declare -p "$_key")")
    fi
  done < "$_build_env_file"
  # shellcheck disable=SC1090
  set +u
  . "$_build_env_file"
  set -u
  if [ ${#_build_env_keep[@]} -gt 0 ]; then
    for _stmt in "${_build_env_keep[@]}"; do eval "$_stmt"; done
  fi
  unset _line _stmt _key _build_env_keep
  info "build defaults: $_build_env_file"
fi

# hermes-agent pin: --hermes-commit / exported HERMES_AGENT_COMMIT > .build.env.
# Empty is a valid state — no --commit is passed at all and the SDK install
# tracks the installer's own default branch. There is deliberately no
# hardcoded fallback here: the old f43eabee… default pinned every install,
# including ones that had no access to this repo, and had already drifted
# from the pin in .build.env.
HERMES_COMMIT="${HERMES_COMMIT:-${HERMES_AGENT_COMMIT:-}}"

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

  # Spinner characters (rotating line)
  local spin_chars='|/-\'
  local spin_pid
  local is_tty=false
  [ -t 1 ] && is_tty=true

  _log ""
  _log "--- Installing ${pkgs[*]} ---"

  # Start install in background, appending to main log
  case $mgr in
    apt)
      (apt-get update >>"$LOG_FILE" 2>&1 && apt-get install -y "${pkgs[@]}" >>"$LOG_FILE" 2>&1) &
      ;;
    yum)
      yum install -y "${pkgs[@]}" >>"$LOG_FILE" 2>&1 &
      ;;
    dnf)
      dnf install -y "${pkgs[@]}" >>"$LOG_FILE" 2>&1 &
      ;;
    apk)
      apk add --no-cache "${pkgs[@]}" >>"$LOG_FILE" 2>&1 &
      ;;
    pacman)
      pacman -Sy --noconfirm "${pkgs[@]}" >>"$LOG_FILE" 2>&1 &
      ;;
    *)
      return 1
      ;;
  esac
  spin_pid=$!

  # Show spinner while installing (only on TTY, otherwise just wait)
  if [ "$is_tty" = true ]; then
    local i=0
    # Build a short display name (truncate if too many packages)
    local _display_pkgs="${pkgs[*]}"
    if [ ${#_display_pkgs} -gt 50 ]; then
      _display_pkgs="${pkgs[0]} ... +$(( ${#pkgs[@]} - 1 )) more"
    fi
    while kill -0 "$spin_pid" 2>/dev/null; do
      local char="${spin_chars:i++%${#spin_chars}:1}"
      printf "\r\033[K  %s Installing %s..." "$char" "$_display_pkgs"
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

  if [ $rc -ne 0 ]; then
    _log "--- ${pkgs[*]} install FAILED (exit $rc) ---"
    warn "Installation failed — see $LOG_FILE for details"
  else
    _log "--- ${pkgs[*]} install OK ---"
  fi

  return $rc
}

# ── Step 1: Check basic prerequisites ──
say ""
say "${GRN}${B}┌─────────────────────────────────────────────────────────┐${R}"
say "${GRN}${B}│                                                         │${R}"
say "${GRN}${B}│                 LeapAide Installer                      │${R}"
say "${GRN}${B}│                                                         │${R}"
say "${GRN}${B}└─────────────────────────────────────────────────────────┘${R}"
say ""
info "Log file: $LOG_FILE"
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

# ── China mirror switch (before any package installation) ──
_SOURCES_BACKUP=""
_restore_sources() {
  # Restore original apt sources if we backed them up
  if [ -n "$_SOURCES_BACKUP" ] && [ -f "$_SOURCES_BACKUP" ]; then
    local target=""
    if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
      target="/etc/apt/sources.list.d/ubuntu.sources"
    elif [ -f /etc/apt/sources.list ]; then
      target="/etc/apt/sources.list"
    fi
    if [ -n "$target" ]; then
      cp "$_SOURCES_BACKUP" "$target"
      _log "Restored original apt sources from $_SOURCES_BACKUP"
    fi
    rm -f "$_SOURCES_BACKUP"
  fi
}
# Ensure sources are restored on script exit (normal or interrupted)
trap _restore_sources EXIT

if [ "$USE_ALIYUN_MIRROR" = true ]; then
  # Check if already using a valid China mirror
  _is_china_mirror() {
    local file="$1"
    grep -qE 'mirrors\.(tuna\.tsinghua|aliyun|ustc|163|huaweicloud)\.edu\.cn|mirrors\.(aliyun|ustc|163|huaweicloud)\.com' "$file" 2>/dev/null
  }

  _needs_mirror_switch=true
  if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
    _is_china_mirror /etc/apt/sources.list.d/ubuntu.sources && _needs_mirror_switch=false
  elif [ -f /etc/apt/sources.list ]; then
    _is_china_mirror /etc/apt/sources.list && _needs_mirror_switch=false
  fi

  if [ "$_needs_mirror_switch" = false ]; then
    info "Already using a China apt mirror — skipping mirror switch"
  else
    # Detect Ubuntu codename
    _ubuntu_codename=$(. /etc/os-release 2>/dev/null && echo "$VERSION_CODENAME" || echo "")
    if [ -z "$_ubuntu_codename" ]; then
      _ubuntu_codename=$(lsb_release -cs 2>/dev/null || echo "")
    fi

    if [ -z "$_ubuntu_codename" ]; then
      warn "Cannot detect Ubuntu codename — skipping mirror switch"
    else
      info "Switching to Tsinghua apt mirror (detected: $_ubuntu_codename)..."

      # Backup original sources
      if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
        _SOURCES_BACKUP="/tmp/ubuntu.sources.backup.$(date +%s)"
        cp /etc/apt/sources.list.d/ubuntu.sources "$_SOURCES_BACKUP"
        _log "Backed up original sources to $_SOURCES_BACKUP"

        # Write DEB822 format (Ubuntu 24.04+)
        # Source: https://mirror.tuna.tsinghua.edu.cn/help/ubuntu/
        cat > /etc/apt/sources.list.d/ubuntu.sources <<MIRROR_EOF
# Configured by LeapAide installer --china-mirror
# Source: https://mirror.tuna.tsinghua.edu.cn/help/ubuntu/
Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu
Suites: $_ubuntu_codename $_ubuntu_codename-updates $_ubuntu_codename-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# 默认注释了源码镜像以提高 apt update 速度，如有需要可自行取消注释
Types: deb-src
URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu
Suites: $_ubuntu_codename $_ubuntu_codename-updates $_ubuntu_codename-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# 以下安全更新软件源为镜像站配置
Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu
Suites: $_ubuntu_codename-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb-src
URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu
Suites: $_ubuntu_codename-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
MIRROR_EOF
        ok "Tsinghua mirror applied (DEB822 format)"
      elif [ -f /etc/apt/sources.list ]; then
        _SOURCES_BACKUP="/tmp/sources.list.backup.$(date +%s)"
        cp /etc/apt/sources.list "$_SOURCES_BACKUP"
        _log "Backed up original sources to $_SOURCES_BACKUP"

        # Write legacy format
        # Source: https://mirror.tuna.tsinghua.edu.cn/help/ubuntu/
        cat > /etc/apt/sources.list <<MIRROR_EOF
# Configured by LeapAide installer --china-mirror
# Source: https://mirror.tuna.tsinghua.edu.cn/help/ubuntu/
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $_ubuntu_codename main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $_ubuntu_codename-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $_ubuntu_codename-backports main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $_ubuntu_codename-security main restricted universe multiverse
MIRROR_EOF
        ok "Tsinghua mirror applied (legacy format)"
      else
        warn "No apt sources file found — skipping mirror switch"
      fi
    fi
  fi
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
  # Node.js 22 LTS for hermes-agent install + frontend tooling. hermes-agent
  # 1.0.0 requires Node >=22.22 and npm >=11.17 (see its package.json engines).
  # Ubuntu's stock nodejs is 18.x — too old. Install via NodeSource (signed
  # repo) so we always have a fresh LTS. Idempotent: if NodeSource setup
  # already ran (e.g. on a re-run), it's a no-op.
  _installed_node=false
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      if ! node --version 2>/dev/null | grep -qE '^v(2[2-9]|[3-9][0-9])'; then
        info "Node.js 22+ required by hermes-agent. Installing via NodeSource..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
          && apt-get install -y --no-install-recommends nodejs \
          && _installed_node=true || warn "NodeSource install failed; falling back to apt nodejs"
      fi
    fi
  fi
  # git is required by both hermes-agent install and Dockerfile build-arg
  # commit consistency checks. Always install (it's tiny and harmless if
  # already present).
  if ! command -v git >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} git"
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
  # xterm for tool call visualization in observer
  if ! command -v xterm >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} xterm"
  fi
  # CJK font for xterm tool-call windows and Chrome pages (wqy-microhei is
  # ~5MB vs fonts-noto-cjk ~90MB)
  if ! fc-list 2>/dev/null | grep -qiE 'wqy|cjk|uming|ukai'; then
    _sys_deps_to_install="${_sys_deps_to_install} fonts-wqy-microhei"
  fi
  # Chinese locale support
  if ! locale -a 2>/dev/null | grep -q 'zh_CN.UTF-8'; then
    _sys_deps_to_install="${_sys_deps_to_install} locales"
  fi
  # Fcitx5 input method for Chinese input (fcitx5-config-qt is the real GUI
  # behind the desktop's "Fcitx 5 Configuration" menu entry — without it the
  # menu click silently does nothing)
  if ! command -v fcitx5 >/dev/null 2>&1; then
    _sys_deps_to_install="${_sys_deps_to_install} fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 fcitx5-config-qt dbus-x11 python3-dbus"
  fi
  if [ -n "$_sys_deps_to_install" ]; then
    warn "Installing system dependencies:${_sys_deps_to_install}"
    # Install packages one by one for resilience — one failure won't block others
    _deps_failed=0
    for _pkg in $_sys_deps_to_install; do
      if ! install_package "$_pkg"; then
        warn "Failed to install $_pkg (continuing)"
        _deps_failed=$((_deps_failed + 1))
      fi
    done
    if [ "$_deps_failed" -eq 0 ]; then
      ok "System dependencies installed"
    else
      warn "$_deps_failed package(s) failed to install"
    fi
    # Refresh shell command cache after installing new packages
    hash -r 2>/dev/null || true
  fi

  # Configure Chinese locale
  if ! locale -a 2>/dev/null | grep -q 'zh_CN.UTF-8'; then
    warn "Configuring Chinese locale..."
    if [ -f /etc/locale.gen ]; then
      sed -i 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen
    fi
    locale-gen zh_CN.UTF-8 2>&1 || warn "Failed to generate zh_CN.UTF-8 locale"
    ok "Chinese locale configured"
  fi

  # fcitx5 trigger key is configured at runtime via D-Bus SetConfig
  # (see backend/app/api/session_display.py _configure_fcitx5_trigger_key)
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
fi

# Default ELECTRON_MIRROR for the China npmmirror registry. Only set when the
# caller didn't already provide one (Dockerfile sets it via BUILD_ELECTRON_MIRROR,
# so this no-ops there). Use the cnpm binary search API (302 →
# cdn.npmmirror.com/binaries/) rather than the older /mirrors/ alias — the
# cnpm API URL works through HTTP_PROXY (the /mirrors/ URL's cross-domain
# redirect to cdn breaks over proxy; verified 2026-10-08).
if [ -z "${ELECTRON_MIRROR:-}" ] && [[ "${NPM_REGISTRY:-}" == *"npmmirror"* ]]; then
  # Trailing slash is REQUIRED: @electron/get joins `${mirror}v${version}/...`
  # with no separator — without it the URL becomes `.../electronv40.10.2/...`
  # and 404s (verified 2026-10-09).
  export ELECTRON_MIRROR="https://registry.npmmirror.com/-/binary/electron/"
  info "Electron mirror defaulted to cnpm API (302→cdn.npmmirror.com); override by setting ELECTRON_MIRROR"
fi

# Enable debug logging if requested
if [ "$DEBUG_INSTALL" = true ]; then
  export DEBUG="${DEBUG:+$DEBUG,}@electron/get:*"
  info "Debug logging enabled (electron download details)"
fi

# ── Resolve the hermes-agent installer URL ─────────────────────────────────────
# Resolved here — after option parsing and env loading — so it always matches
# the hermes-agent version being installed. Rules:
#   1. HERMES_AGENT_INSTALLER_URL set → use as-is (full override)
#   2. HERMES_BRANCH set             → raw.githubusercontent.com/.../<branch>/scripts/install.sh
#   3. HERMES_COMMIT set             → raw.githubusercontent.com/.../<commit>/scripts/install.sh
#   4. neither ref pinned            → https://hermes-agent.nousresearch.com/install.sh (tracks main)
# Rules 2/3 fetch the installer from the same ref that gets checked out, so the
# installer and the tree can never skew. Rule 4 is what an unpinned install gets
# (no HERMES_AGENT_COMMIT in .build.env and none exported): the SDK then tracks
# upstream main. That is a documented opt-out, not an accident — but be aware
# the floating hosted URL tracks main and silently broke pinned installs when
# main moved to the pm/ dependency manager (pre-pm trees have no pm/lock.json,
# so `pm install` failed and no Python venv was ever created).
# HERMES_INSTALLER_MIRROR (optional prefix, include trailing slash) points at a
# raw.githubusercontent.com accelerator when direct access is blocked — it is
# applied to rules 2/3 only, see the guard below. Site-specific mirrors stay in
# env vars, not here.
if [ -n "${HERMES_AGENT_INSTALLER_URL:-}" ]; then
  HERMES_AGENT_INSTALLER="$HERMES_AGENT_INSTALLER_URL"
elif [ -n "${HERMES_BRANCH:-}" ]; then
  HERMES_AGENT_INSTALLER="https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_BRANCH}/scripts/install.sh"
elif [ -n "${HERMES_COMMIT:-}" ]; then
  HERMES_AGENT_INSTALLER="https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_COMMIT}/scripts/install.sh"
else
  HERMES_AGENT_INSTALLER="https://hermes-agent.nousresearch.com/install.sh"
fi
# HERMES_INSTALLER_MIRROR is a raw.githubusercontent.com accelerator, so it is
# only valid for the two ref-pinned URLs above. Rule 4 is reachable for the
# first time now that the pin is optional — prefixing the floating hosted
# installer there would silently produce an invalid URL, so warn and use it
# as-is instead.
if [ -n "${HERMES_INSTALLER_MIRROR:-}" ]; then
  case "$HERMES_AGENT_INSTALLER" in
    https://raw.githubusercontent.com/*)
      HERMES_AGENT_INSTALLER="${HERMES_INSTALLER_MIRROR}${HERMES_AGENT_INSTALLER}" ;;
    *)
      warn "HERMES_INSTALLER_MIRROR is set but the resolved installer is not a raw.githubusercontent.com URL — using it unmirrored" ;;
  esac
fi
if [ -z "${HERMES_BRANCH:-}${HERMES_COMMIT:-}" ]; then
  info "no hermes-agent ref pinned — the SDK install will track the default branch"
fi
info "hermes-agent installer: $HERMES_AGENT_INSTALLER"

# Build hermes-agent installer args
_ha_args=("--skip-browser")
[ "$NON_INTERACTIVE" = true ] && _ha_args+=("--non-interactive" "--skip-setup")
[ -n "$HERMES_BRANCH" ] && _ha_args+=("--branch" "$HERMES_BRANCH")
[ -n "$HERMES_COMMIT" ] && _ha_args+=("--commit" "$HERMES_COMMIT")
# Forward --hermes-home to the installer. HERMES_HOME is always populated
# above (line ~120 defaults it to $HOME/.hermes), so this always passes —
# which equals the installer's own default on host installs and lets Docker
# point it at a seed dir at build time.
[ -n "$HERMES_HOME" ] && _ha_args+=("--hermes-home" "$HERMES_HOME")
[ "$FORCE_HERMES_COMMIT" = true ] && _ha_args+=("--force-commit")

AGENT_DIR=$(_hermes_agent_found || true)
if [ -n "$AGENT_DIR" ] && [ "$_HERMES_VERSION_SPECIFIED" = false ]; then
  ok "hermes-agent found at ${B}$AGENT_DIR${R}"
elif command -v hermes >/dev/null 2>&1 && [ "$_HERMES_VERSION_SPECIFIED" = false ]; then
  ok "hermes command found in PATH"
else
  if [ -n "$AGENT_DIR" ] && [ "$_HERMES_VERSION_SPECIFIED" = true ]; then
    warn "hermes-agent version override requested — reinstalling..."
  else
    warn "hermes-agent not found — installing..."
  fi
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
  #
  # Also add --unsafe-perm when running as root. npm ≥7 refuses to run as
  # root at all unless --unsafe-perm is set. The testing Dockerfile runs
  # install.sh as root so apt-get + /usr/local/bin wrappers work; we need
  # this --unsafe-perm to let hermes-agent's npm install succeed in that
  # context. Host usage is unaffected: --unsafe-perm is a no-op for
  # non-root users.
  npm() {
    local retries=3
    local unsafe=()
    if [ "$(id -u)" -eq 0 ]; then
      unsafe+=(--unsafe-perm)
    fi
    for ((i = 1; i <= retries; i++)); do
      if command npm "${unsafe[@]}" "$@" --loglevel=verbose; then
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
  # Export HERMES_REPO_URL so the hermes-agent installer can pick it up
  # (the installer reads this env var at startup; see its source). Empty
  # value (the default) means use upstream NousResearch/hermes-agent.
  [ -n "$HERMES_REPO_URL" ] && export HERMES_REPO_URL || unset HERMES_REPO_URL
  # --retry 5 --retry-all-errors: covers the SSL_ERROR_SYSCALL / TLS handshake
  # failures seen in testbox builds routed through the transparent proxy.
  # Same pattern as the uv install at Dockerfile:212 — without
  # --retry-all-errors, curl would not retry on TLS handshake failures and
  # the build would fail immediately.
  #
  # HERMES_REPO_URL is left as-is (set from HERMES_AGENT_REPO_URL build-arg).
  # We previously overrode this to file://${WARMUP_DIR} to make the
  # installer's git clone a local copy, but that path hits the container's
  # RLIMIT_NOFILE hard cap ("Too many open files" / "fetch-pack: invalid
  # index-pack output") and ulimit can't raise it. The trade-off:
  #   - file:// clone: fast, but hits FD limit, can't raise it
  #   - ssh:// clone (default): network round-trip, slow from China, but
  #     works. The warmup's electron/playwright pre-fetch still applies,
  #     so the only network call the installer adds is the hermes-agent
  #     repo clone itself.
  # Fetch the installer to a file first so a 404 (bad ref / unreachable
  # mirror) produces a clear error instead of piping an error page into bash.
  _ha_installer="$(mktemp)"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors \
         "$HERMES_AGENT_INSTALLER" -o "$_ha_installer" \
      || die "Failed to fetch hermes-agent installer from $HERMES_AGENT_INSTALLER — check that the ref exists upstream (fork-only SHAs need a HERMES_AGENT_INSTALLER_URL override); HERMES_INSTALLER_MIRROR accelerates raw.githubusercontent.com URLs only"
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=5 --waitretry=10 -qO "$_ha_installer" \
         "$HERMES_AGENT_INSTALLER" \
      || die "Failed to fetch hermes-agent installer from $HERMES_AGENT_INSTALLER — check that the ref exists upstream (fork-only SHAs need a HERMES_AGENT_INSTALLER_URL override); HERMES_INSTALLER_MIRROR accelerates raw.githubusercontent.com URLs only"
  else
    die "curl or wget is required to download hermes-agent."
  fi
  # The pinned installer hardcodes GitHub upstream clone URLs (REPO_URL_SSH/
  # REPO_URL_HTTPS) and predates HERMES_REPO_URL support — without this rewrite
  # it silently clones NousResearch instead of the configured mirror. Rewrite
  # both URL forms so SSH goes straight to the fork (deploy key applies) and the
  # HTTPS fallback also lands there; if both fail, the installer exits loudly.
  # Pure text substitution, zero intrusion into the installer's stages (clone →
  # venv → deps → node → path → config) — it survives installer refactors. When
  # a future installer natively honors HERMES_REPO_URL, the patterns simply do
  # not match (harmless no-op). sed delimiter is `|` because URLs contain `/`
  # and `:`; a `|` inside HERMES_REPO_URL is not escaped (documented limitation).
  # ${:-} tolerates the unset done above when the URL is empty (upstream).
  if [ -n "${HERMES_REPO_URL:-}" ]; then
    sed -e "s|git@github.com:NousResearch/hermes-agent.git|${HERMES_REPO_URL}|g" \
        -e "s|https://github.com/NousResearch/hermes-agent.git|${HERMES_REPO_URL}|g" \
        "$_ha_installer" > "$_ha_installer.patched" \
      && mv "$_ha_installer.patched" "$_ha_installer" \
      || die "Failed to rewrite hermes-agent installer clone URLs to $HERMES_REPO_URL"
    grep -q "$HERMES_REPO_URL" "$_ha_installer" \
      || warn "hermes-agent installer no longer hardcodes NousResearch clone URLs — clone source unverified (installer may honor HERMES_REPO_URL natively)"
  fi
  # The installer may return non-zero for optional components (e.g. npm), so
  # we don't fail on its exit code — the venv check below verifies the core.
  bash "$_ha_installer" "${_ha_args[@]}" || true
  rm -f "$_ha_installer"
  AGENT_DIR=$(_hermes_agent_found || true)
  if [ -n "$AGENT_DIR" ]; then
    ok "hermes-agent installed at ${B}$AGENT_DIR${R}"
  elif command -v hermes >/dev/null 2>&1; then
    ok "hermes command installed"
  else
    die "hermes-agent installation failed. Check the output above for errors."
  fi
  # Verify commit pin (warn-only — catches "I bumped hermes-agent on host but
  # forgot to rebuild the image"). Skip when --hermes-commit was not specified.
  # Prefix match on the full HEAD sha: the pin may itself be a short sha, so
  # comparing equal-length abbreviations could never match.
  if [ -n "$HERMES_COMMIT" ] && [ -n "$AGENT_DIR" ] && [ -d "$AGENT_DIR/.git" ]; then
    _actual_commit=$(git -C "$AGENT_DIR" rev-parse HEAD 2>/dev/null || true)
    if [ -n "$_actual_commit" ]; then
      case "$_actual_commit" in
        "$HERMES_COMMIT"*) ;;
        *) warn "hermes-agent commit mismatch: expected ${HERMES_COMMIT}, got ${_actual_commit}" ;;
      esac
    fi
  fi
fi

# ── Verify the agent Python venv (the server's interpreter) ────────────────────
# The agent installer owns the dependency environment ($AGENT_DIR/venv: uv venv
# + uv sync at install time). The container entrypoint has no runtime fallback
# install — a missing venv is fatal at startup — so fail here, at install/build
# time, instead of shipping a broken image. (Historically the floating hosted
# installer could skew from the pinned commit and silently fail its dependency
# stage, leaving a bare source tree.)
AGENT_DIR=$(_hermes_agent_found || true)
if [ -z "$AGENT_DIR" ] || [ ! -x "$AGENT_DIR/venv/bin/python" ]; then
  die "hermes-agent Python venv not found at ${AGENT_DIR:-<unknown>}/venv — the installer's dependency stage failed; see the installer output above"
fi
ok "hermes-agent venv at ${B}$AGENT_DIR/venv${R}"

# ── Fix playwright-core version & install browser ──
# hermes-agent bundles playwright-core <1.60.0 which hangs on Node.js v26+
# (nodejs/node#63487, microsoft/playwright#40998).  Upgrade before downloading.
AGENT_DIR=$(_hermes_agent_found || true)
if [ -n "$AGENT_DIR" ] && command -v node >/dev/null 2>&1; then
  # Install playwright packages if missing (--skip-browser may have skipped them)
  if [ ! -d "$AGENT_DIR/node_modules/playwright-core" ]; then
    info "Playwright not found — installing..."
    npm install --prefix "$AGENT_DIR" \
      playwright-core@'^1.60.0' playwright@'^1.60.0' @playwright/test@'^1.60.0' \
      --save --quiet 2>&1 || warn "Playwright install failed"
  fi
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

# ── Step 2b: Apply LeapAide defaults to hermes config ──
say ""
say "${B}Configuring hermes defaults...${R}"

HERMES_CONFIG="${HERMES_HOME}/config.yaml"
if [ -f "$HERMES_CONFIG" ]; then
  # Ensure display.show_reasoning is true — LeapAide displays reasoning
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
# Skipped when --deps-only — caller provides leap-aide source via --dir <path>.
# Steps 4 (Python deps) and 5 (wrappers) still run because they read from
# $DEST/backend/{requirements.txt,scripts/install-wrappers.sh}.
if [ "$DEPS_ONLY" = false ]; then
  say ""
  say "${B}Installing LeapAide to ${DEST}...${R}"

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
  # Auto-accept SSH host keys in non-interactive mode
  if [[ "$REPO_URL" == ssh://* || "$REPO_URL" == git@* ]] && [ "$NON_INTERACTIVE" = true ]; then
    export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"
  fi
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
  # Auto-accept SSH host keys in non-interactive mode
  _git_ssh_extra=""
  if [[ "$REPO_URL" == ssh://* || "$REPO_URL" == git@* ]] && [ "$NON_INTERACTIVE" = true ]; then
    _git_ssh_extra="-o StrictHostKeyChecking=accept-new"
    export GIT_SSH_COMMAND="ssh $_git_ssh_extra"
  fi
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
fi  # DEPS_ONLY=false guard around Step 3 (clone/update)

# ── Step 4: Install Python dependencies ──
# WebUI deps go into the hermes-agent venv — the interpreter the server runs
# under (bootstrap.py's discover_launcher_python() prefers
# agent_dir/venv/bin/python). One venv only: the old $DEST/.venv was a second,
# redundant environment (and a bootstrap.py fallback path that hid breakage).
say ""
say "${B}Setting up Python environment...${R}"

AGENT_DIR=$(_hermes_agent_found || true)
if [ -z "$AGENT_DIR" ] || [ ! -x "$AGENT_DIR/venv/bin/python" ]; then
  die "hermes-agent Python venv not found at ${AGENT_DIR:-<unknown>}/venv — cannot install WebUI dependencies"
fi

# Trusted-host handling for China mirrors (pip path only): pip refuses to talk
# to non-PyPI HTTPS hosts unless we explicitly mark them trusted. Keyed off
# PIP_INDEX_URL (which the Dockerfile exports) so we don't depend on
# --china-mirror being passed. The uv path (agent venv) accepts the configured
# index as-is — no TLS-verification bypass flags there.
_pip_trusted="--trusted-host pypi.org --trusted-host files.pythonhosted.org"
case "${PIP_INDEX_URL:-}" in
  *tuna.tsinghua*) _pip_trusted="$_pip_trusted --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host files.tuna.tsinghua.edu.cn" ;;
  *aliyun*)        _pip_trusted="$_pip_trusted --trusted-host mirrors.aliyun.com" ;;
esac

if [ "$USE_VENV" = true ]; then
  # The agent venv is created by uv and ships no pip — install via uv into it.
  _uv_bin="$(command -v uv || true)"
  if [ -z "$_uv_bin" ] && [ -x "${HERMES_HOME:-$HOME/.hermes}/bin/uv" ]; then
    _uv_bin="${HERMES_HOME:-$HOME/.hermes}/bin/uv"
  fi
  [ -n "$_uv_bin" ] || die "uv not found — needed to install WebUI dependencies into $AGENT_DIR/venv"
  "$_uv_bin" pip install --python "$AGENT_DIR/venv/bin/python" -r "$DEST/backend/requirements.txt" \
    || die "Failed to install WebUI dependencies into $AGENT_DIR/venv"
  ok "Python dependencies installed into ${B}$AGENT_DIR/venv${R}"
else
  info "Skipping virtual environment (--no-venv)"
  pip3 install --quiet --upgrade pip $_pip_trusted
  pip3 install --quiet -r "$DEST/backend/requirements.txt" $_pip_trusted
  ok "Python dependencies installed (system-wide)"
fi

# ── Core lazy dependencies (issue #8) ────────────────────────────────────────
# The first chat's tool-definition path probes feature availability, and the
# SDK answers a missing package with a ~26s runtime `uv pip install` (which
# fails with silent EACCES in read-only container venvs). Pre-install the two
# lazy deps that path always touches so the probe collapses to a fast import
# check. Specs are read from the pinned hermes checkout's tools/lazy_deps.py
# so they track HERMES_AGENT_COMMIT; the fallback literals below must be kept
# in sync with tools/lazy_deps.py:141 ("tts.edge") and :190 ("image.fal").
# Failure is non-fatal (unlike requirements.txt): a missing lazy dep only
# disables its feature and the first chat falls back to the lazy-install path.
_lazy_specs="$( (cd "$AGENT_DIR" && "$AGENT_DIR/venv/bin/python" -c \
  'from tools.lazy_deps import LAZY_DEPS; print(*(LAZY_DEPS["tts.edge"] + LAZY_DEPS["image.fal"]))') \
  2>/dev/null || true)"
if [ -z "$_lazy_specs" ]; then
  _lazy_specs="edge-tts==7.2.7 fal-client==0.13.1"
  warn "Could not read LAZY_DEPS from the hermes checkout — using fallback specs (keep in sync with tools/lazy_deps.py)"
fi
# shellcheck disable=SC2086  # specs are intentionally word-split
if [ "$USE_VENV" = true ]; then
  if "$_uv_bin" pip install --python "$AGENT_DIR/venv/bin/python" $_lazy_specs; then
    if "$AGENT_DIR/venv/bin/python" -c "import edge_tts" 2>/dev/null; then
      ok "Core lazy dependencies installed (edge-tts importable)"
    else
      warn "Lazy deps installed but 'import edge_tts' failed — first chat may retry a lazy install"
    fi
  else
    warn "Core lazy-dep install failed — first chat will retry the lazy install (issue #8 timings may persist)"
  fi
else
  if pip3 install --quiet $_lazy_specs $_pip_trusted; then
    if python3 -c "import edge_tts" 2>/dev/null; then
      ok "Core lazy dependencies installed (edge-tts importable)"
    else
      warn "Lazy deps installed but 'import edge_tts' failed — first chat may retry a lazy install"
    fi
  else
    warn "Core lazy-dep install failed — first chat will retry the lazy install (issue #8 timings may persist)"
  fi
fi

# ── Step 5: Install wrappers ──
say ""
say "${B}Installing command wrappers...${R}"

if [ -f "$DEST/backend/scripts/install-wrappers.sh" ]; then
  bash "$DEST/backend/scripts/install-wrappers.sh" --force 2>&1
  ok "Command wrappers installed"
else
  warn "install-wrappers.sh not found — skipping"
fi

# ── Step 5.5: Install LeapAide-managed agent skills ──
say ""
say "${B}Installing agent skills...${R}"

if [ -f "$DEST/backend/scripts/install-skills.sh" ]; then
  if HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
      bash "$DEST/backend/scripts/install-skills.sh" --source "$DEST/skills"; then
    ok "Agent skills installed"
  else
    warn "install-skills.sh failed — agents will not see LeapAide-managed skills"
  fi
else
  warn "install-skills.sh not found — skipping"
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
Description=LeapAide Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory=$DEST
Environment="PATH=$DEST/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$DEST/.venv/bin/python backend/app/bootstrap.py --no-browser
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

# ── Restore apt sources if we switched to China mirror ──
_restore_sources

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
