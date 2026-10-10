#!/usr/bin/env bash
# build.sh — Build frontend and/or pre-compile backend Python
#
# Usage:
#   ./scripts/build.sh                 # Build everything
#   ./scripts/build.sh --frontend      # Frontend only
#   ./scripts/build.sh --backend       # backend only
#   ./scripts/build.sh --clean         # Clean build artifacts
#   ./scripts/build.sh --watch         # Watch mode (frontend)
#   ./scripts/build.sh --release       # Production build (minified)
#   ./scripts/build.sh --docker-image leap-aide:runtime   # Build the runtime container image
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── TTY-aware colors ──
if [ -t 1 ]; then
  B=$'\033[1m'   # bold
  R=$'\033[0m'   # reset
  GRN=$'\033[32m'
  YLW=$'\033[33m'
  RED=$'\033[31m'
else
  B='' R='' GRN='' YLW='' RED=''
fi

# ── Output helpers ──
say()  { printf '%s\n' "$*"; }
ok()   { printf "${GRN}✓${R} %s\n" "$*"; }
warn() { printf "${YLW}!${R} %s\n" "$*"; }
die()  { printf "${RED}✗${R} %s\n" "$*" >&2; exit 1; }

# ── Options ──
BUILD_FRONTEND=false
BUILD_BACKEND=false
CLEAN=false
WATCH=false
RELEASE=false

# Container image build (standalone mode; see build_container_image below)
BUILD_DOCKER_IMAGE=false
DOCKER_IMAGE=""
DOCKER_NO_CACHE=false

# If no arguments, build everything
if [ $# -eq 0 ]; then
  BUILD_FRONTEND=true
  BUILD_BACKEND=true
fi

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
  case $1 in
    --frontend|-f)
      BUILD_FRONTEND=true
      shift
      ;;
    --backend|-b)
      BUILD_BACKEND=true
      shift
      ;;
    --all|-a)
      BUILD_FRONTEND=true
      BUILD_BACKEND=true
      shift
      ;;
    --clean|-c)
      CLEAN=true
      shift
      ;;
    --watch|-w)
      WATCH=true
      BUILD_FRONTEND=true
      shift
      ;;
    --release|-r)
      RELEASE=true
      shift
      ;;
    --docker-image)
      [[ $# -ge 2 && "$2" != -* ]] || die "--docker-image requires a value (name:tag)"
      BUILD_DOCKER_IMAGE=true
      DOCKER_IMAGE="$2"
      shift 2
      ;;
    --docker-no-cache)
      DOCKER_NO_CACHE=true
      shift
      ;;
    --help|-h)
      echo "Usage: build.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --frontend, -f    Build frontend only"
      echo "  --backend, -b      Pre-compile backend only"
      echo "  --all, -a         Build everything (default)"
      echo "  --clean, -c       Clean build artifacts"
      echo "  --watch, -w       Watch mode (frontend)"
      echo "  --release, -r     Release build (minified)"
      echo "  --docker-image <name:tag>   Build the runtime container image (buildah)"
      echo "  --docker-no-cache           Disable layer cache for the container build"
      echo "  -h, --help        Show this help"
      echo ""
      echo "Container image build (standalone mode; everything is env-driven):"
      echo "  HERMES_AGENT_REPO_URL      hermes-agent git URL (empty = upstream)"
      echo "  HERMES_AGENT_COMMIT        commit pin (default: backend/scripts/hermes-agent.env)"
      echo "  HERMES_HOME                install prefix (default: /usr/local/lib)"
      echo "  HERMES_VERSION             version badge (default: git describe --tags --always)"
      echo "  HTTP_PROXY/HTTPS_PROXY/NO_PROXY   forwarded as build-args (either case)"
      echo "  BUILD_APT_MIRROR, BUILD_PIP_INDEX_URL, BUILD_UV_PYTHON_MIRROR"
      echo "  BUILD_NPM_REGISTRY, BUILD_PLAYWRIGHT_DOWNLOAD_HOST"
      echo "  BUILD_ELECTRON_MIRROR, BUILD_ELECTRON_BUILDER_BINARIES_MIRROR"
      echo "  BUILD_NPM_WARMUP           0|1 (default: 0)"
      echo "  BUILD_GIT_IDENTITY_KEY_FILE   SSH deploy key (ssh:// repo URLs only)"
      echo "                             default: ~/.ssh/id_ed25519"
      echo "  DOCKER_NETWORK             RUN network mode (default: host)"
      echo "  DOCKER_LOG_DIR             default: ~/.cache/leap-aide"
      echo "  BUILDAH_STORAGE_DRIVER     default: overlay"
      echo "  BUILDAH_ROOT               buildah store root (empty = buildah default)"
      echo "  BUILDAH_RUNROOT            buildah runroot (empty = buildah default)"
      echo ""
      echo "Examples:"
      echo "  ./scripts/build.sh              # Build all"
      echo "  ./scripts/build.sh -f           # Frontend only"
      echo "  ./scripts/build.sh -b           # backend only"
      echo "  ./scripts/build.sh -c -a        # Clean and rebuild all"
      echo "  ./scripts/build.sh -w           # Watch frontend for changes"
      echo "  ./scripts/build.sh --docker-image leap-aide:runtime"
      exit 0
      ;;
    *)
      die "Unknown option: $1. Use --help for usage."
      ;;
  esac
done

# ── Functions ──

clean_build() {
  say "${B}Cleaning build artifacts...${R}"

  # Frontend
  if [ -d "$PROJECT_ROOT/frontend/dist" ]; then
    rm -rf "$PROJECT_ROOT/frontend/dist"
    ok "Cleaned frontend/dist"
  fi
  if [ -d "$PROJECT_ROOT/frontend/node_modules/.vite" ]; then
    rm -rf "$PROJECT_ROOT/frontend/node_modules/.vite"
    ok "Cleaned Vite cache"
  fi

  # Static files
  if [ -d "$PROJECT_ROOT/static" ]; then
    # Clean pre-compressed files first (before removing the directory)
    find "$PROJECT_ROOT/static" -name "*.gz" -delete 2>/dev/null || true
    rm -rf "$PROJECT_ROOT/static"
    ok "Cleaned static/"
  fi

  # Python bytecode
  find "$PROJECT_ROOT" -type f -name "*.pyc" -delete 2>/dev/null || true
  find "$PROJECT_ROOT" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  ok "Cleaned Python bytecode"
}

build_frontend() {
  say "${B}Building frontend...${R}"

  if ! command -v node >/dev/null 2>&1; then
    warn "Node.js not found — skipping frontend build"
    return 0
  fi

  if [ ! -d "$PROJECT_ROOT/frontend" ]; then
    warn "frontend/ directory not found — skipping"
    return 0
  fi

  cd "$PROJECT_ROOT/frontend"

  # Install dependencies
  if [ ! -d "node_modules" ] || [ "$CLEAN" = true ]; then
    say "Installing npm dependencies..."
    npm install --silent 2>/dev/null
  fi

  # Build
  if [ "$WATCH" = true ]; then
    say "Starting watch mode..."
    npm run dev
  elif [ "$RELEASE" = true ]; then
    say "Building for production..."
    npm run build -- --minify
  else
    npm run build
  fi

  # Pre-compress text assets (.js, .css) with gzip for Content-Encoding serving.
  # This avoids runtime gzip compression which is CPU-intensive on large files.
  # -f forces overwrite of stale .gz from a previous build (e.g. when the
  #    Docker build context carries leftover static/assets/*.gz from a prior
  #    run — COPY . /build picks it up, and gzip without -f refuses to
  #    overwrite, aborting the whole build).
  #
  # Skip inside Docker: fuse-overlayfs (buildah's storage driver) can be
  # backed up by parallel layer-commit work, making per-file gzip take
  # 10-60+ seconds. The runtime server compresses on the fly if .gz files
  # are missing, so skipping here is safe — just slower at request time.
  # /.within_container is the marker written by Dockerfile:219.
  if [ -f "/.within_container" ]; then
    warn "Skipping gzip pre-compression inside container (fuse-overlayfs may stall); runtime will compress on the fly"
  elif command -v gzip >/dev/null 2>&1; then
    local count=0
    for f in "$PROJECT_ROOT"/static/assets/*.js "$PROJECT_ROOT"/static/assets/*.css; do
      [ -f "$f" ] || continue
      gzip -kf -9 "$f"
      count=$((count + 1))
    done
    if [ "$count" -gt 0 ]; then
      ok "Pre-compressed $count text assets"
    fi
  fi

  ok "Frontend built"
}

build_backend() {
  say "${B}Pre-compiling Python bytecode...${R}"

  if ! command -v python3 >/dev/null 2>&1; then
    warn "Python3 not found — skipping"
    return 0
  fi

  # Count files before
  local count_before
  count_before=$(find "$PROJECT_ROOT/backend" -name "*.pyc" 2>/dev/null | wc -l)

  # Compile
  python3 -m compileall -q "$PROJECT_ROOT/backend" 2>/dev/null || true

  # Count files after
  local count_after
  count_after=$(find "$PROJECT_ROOT/backend" -name "*.pyc" 2>/dev/null | wc -l)

  local new_files=$((count_after - count_before))
  ok "Python bytecode compiled ($new_files new .pyc files)"
}

# ── Container image build ─────────────────────────────────────────────────
# Builds the `runtime` stage of the repo-root Dockerfile with buildah.
# Ported from .lingyue-lab/scripts/test-build-runtime-image.sh.
#
# Everything site-specific (proxy, package mirrors, git host, registry tag,
# buildah store location) comes from the environment — nothing is hardcoded
# here. See --help.
#
# Every Dockerfile ARG is forwarded as a --build-arg of the same name, always
# explicitly, even when empty: the cicd stage's ARGs inherit into the runtime
# stage and must be cleared or they leak into the runtime layer's image config
# (see the comment around Dockerfile:95).
#
# Secrets: only `git_identity_key` is mounted (Dockerfile:471,483). There is
# deliberately no `git_host_key` — the build sets StrictHostKeyChecking=
# accept-new so SSH writes its own known_hosts, and a read-only host-key mount
# would only block that write (Dockerfile:459-464). Key bytes never enter an
# image layer: --mount=type=secret keeps them out of the filesystem snapshot.

# Container-build log file; assigned by build_container_image() before use.
DOCKER_LOG_FILE=""
_dlog() { printf '[%s] [build] %s\n' "$(date -Iseconds)" "$*" | tee -a "$DOCKER_LOG_FILE" >&2; }

build_container_image() {
  command -v buildah >/dev/null 2>&1 \
    || die "buildah not found — install it (e.g. 'apt-get install buildah') to build container images"

  DOCKER_LOG_FILE="${DOCKER_LOG_DIR:-$HOME/.cache/leap-aide}/runtime-build.log"
  mkdir -p "$(dirname "$DOCKER_LOG_FILE")"
  : > "$DOCKER_LOG_FILE"

  # Proxy: the Dockerfile declares uppercase ARGs only (Dockerfile:53-55) and
  # maps the lowercase variants itself (Dockerfile:56-61), but shells commonly
  # export only lowercase. Normalize so either form works.
  HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-}}"
  HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-}}"
  NO_PROXY="${NO_PROXY:-${no_proxy:-}}"

  # hermes-agent commit pin. Env wins (the contract documented in
  # backend/scripts/hermes-agent.env); otherwise read the canonical pin.
  local ha_commit="${HERMES_AGENT_COMMIT:-}"
  if [ -z "$ha_commit" ] && [ -f "$PROJECT_ROOT/backend/scripts/hermes-agent.env" ]; then
    ha_commit="$(grep '^HERMES_AGENT_COMMIT=' "$PROJECT_ROOT/backend/scripts/hermes-agent.env" \
      | cut -d= -f2- | tr -d '"' || true)"
  fi

  # Version badge baked into backend/app/api/_version.py (Dockerfile:239-240).
  local ha_version="${HERMES_VERSION:-}"
  if [ -z "$ha_version" ]; then
    ha_version="$(git -C "$PROJECT_ROOT" describe --tags --always 2>/dev/null || true)"
  fi
  ha_version="${ha_version:-unknown}"

  local hermes_repo="${HERMES_AGENT_REPO_URL:-}"

  # SSH deploy key, only for ssh:// forks. The Dockerfile mount is
  # required=false, so a public/HTTPS build needs no secret at all.
  local -a secrets=()
  if [[ "$hermes_repo" == ssh://* ]]; then
    local id_key="${BUILD_GIT_IDENTITY_KEY_FILE:-$HOME/.ssh/id_ed25519}"
    [ -f "$id_key" ] \
      || die "HERMES_AGENT_REPO_URL is ssh:// but identity key '$id_key' is missing (set BUILD_GIT_IDENTITY_KEY_FILE)"
    secrets+=(--secret "id=git_identity_key,src=$id_key")
  fi

  local -a build_args=(
    --build-arg "HTTP_PROXY=$HTTP_PROXY"
    --build-arg "HTTPS_PROXY=$HTTPS_PROXY"
    --build-arg "NO_PROXY=$NO_PROXY"
    --build-arg "HERMES_AGENT_COMMIT=$ha_commit"
    --build-arg "HERMES_AGENT_REPO_URL=$hermes_repo"
    --build-arg "HERMES_HOME=${HERMES_HOME:-/usr/local/lib}"
    --build-arg "HERMES_VERSION=$ha_version"
    --build-arg "BUILD_APT_MIRROR=${BUILD_APT_MIRROR:-}"
    --build-arg "BUILD_PIP_INDEX_URL=${BUILD_PIP_INDEX_URL:-}"
    --build-arg "BUILD_UV_PYTHON_MIRROR=${BUILD_UV_PYTHON_MIRROR:-}"
    --build-arg "BUILD_NPM_REGISTRY=${BUILD_NPM_REGISTRY:-}"
    --build-arg "BUILD_PLAYWRIGHT_DOWNLOAD_HOST=${BUILD_PLAYWRIGHT_DOWNLOAD_HOST:-}"
    --build-arg "BUILD_ELECTRON_MIRROR=${BUILD_ELECTRON_MIRROR:-}"
    --build-arg "BUILD_ELECTRON_BUILDER_BINARIES_MIRROR=${BUILD_ELECTRON_BUILDER_BINARIES_MIRROR:-}"
    --build-arg "BUILD_NPM_WARMUP=${BUILD_NPM_WARMUP:-0}"
  )

  # Optional store override. Empty = buildah's default store, so behaviour is
  # unchanged unless a site opts in. --root/--runroot are buildah global
  # (persistent) flags; cobra accepts them in any position, but they are listed
  # before `build` to match the documented buildah form.
  #
  # Why this matters: the default store is often on a filesystem the kernel
  # refuses as an overlay upperdir (e.g. a container's own overlayfs snapshot),
  # which makes layer commits fail outright. Pointing at a real ext4 mount
  # gives "Native Overlay Diff: true" and keeps the overlay driver in-kernel
  # instead of falling back to fuse-overlayfs.
  if [ -n "${BUILDAH_ROOT:-}" ] && [ "$(id -u)" -ne 0 ]; then
    die "BUILDAH_ROOT is set — please run as root"
  fi

  local -a store_args=()
  if [ -n "${BUILDAH_ROOT:-}" ]; then
    store_args+=(--root "$BUILDAH_ROOT")
  fi
  if [ -n "${BUILDAH_RUNROOT:-}" ]; then
    store_args+=(--runroot "$BUILDAH_RUNROOT")
  fi

  # Display-only prefix so the verification commands printed below hit the
  # same store the build actually used. Built from store_args, so it cannot
  # drift from the real invocation.
  local ba_display="buildah --storage-driver ${BUILDAH_STORAGE_DRIVER:-overlay}"
  if [ ${#store_args[@]} -gt 0 ]; then
    ba_display+=" ${store_args[*]}"
  fi

  # --layers reuses the image layer cache; --jobs 0 uncaps multi-arch build
  # parallelism; --isolation=oci matches the lab script. --ignorefile is
  # deliberately NOT passed: every target shares the repo-root .dockerignore.
  local -a cmd=(
    buildah --storage-driver "${BUILDAH_STORAGE_DRIVER:-overlay}"
  )
  if [ ${#store_args[@]} -gt 0 ]; then
    cmd+=("${store_args[@]}")
  fi
  cmd+=(
    build --layers --jobs 0
    --network="${DOCKER_NETWORK:-host}"
    --target runtime
  )
  if [ "$DOCKER_NO_CACHE" = true ]; then
    cmd+=(--no-cache)
  fi
  # --log-level debug prints which --mount resolved to which dst path, so a
  # secret that failed to bind is distinguishable from a bound-but-empty one.
  if buildah build --help 2>&1 | grep -q -- '--log-level'; then
    cmd+=(--log-level debug)
  fi
  cmd+=("${build_args[@]}")
  if [ ${#secrets[@]} -gt 0 ]; then
    cmd+=("${secrets[@]}")
  fi
  cmd+=(--tag "$DOCKER_IMAGE" -f "$PROJECT_ROOT/Dockerfile" "$PROJECT_ROOT")

  _dlog "buildah build --target runtime → ${DOCKER_IMAGE}"
  _dlog "  store                 = ${BUILDAH_ROOT:-<buildah default>}"
  _dlog "  runroot               = ${BUILDAH_RUNROOT:-<buildah default>}"
  _dlog "  context               = ${PROJECT_ROOT}"
  _dlog "  ignorefile            = ${PROJECT_ROOT}/.dockerignore (auto-read by buildah)"
  _dlog "  HERMES_AGENT_REPO_URL = ${hermes_repo:-<upstream>}"
  _dlog "  HERMES_AGENT_COMMIT   = ${ha_commit:-<unset>}"
  _dlog "  HERMES_VERSION        = ${ha_version}"
  _dlog "  secrets               = ${secrets[*]:-<none>}"
  _dlog "  log                   = ${DOCKER_LOG_FILE}"

  # `set -o pipefail` is on, so the pipeline status is buildah's, not tee's.
  # Capture it explicitly — a bare `cmd | tee` would otherwise report tee's
  # success and mask a failed build (a bug the lab script documented).
  local rc=0
  "${cmd[@]}" 2>&1 | tee -a "$DOCKER_LOG_FILE" || rc=$?
  if [ "$rc" -ne 0 ]; then
    die "container image build failed (exit=$rc) — full log: $DOCKER_LOG_FILE"
  fi

  ok "runtime build OK — ${DOCKER_IMAGE}"
  say ""
  say "${B}Manual verification (optional):${R}"
  say "  ${ba_display} images ${DOCKER_IMAGE} --format '{{.Name}}:{{.Tag}}\t{{.Size}}'"
  say "  ${ba_display} inspect --type image ${DOCKER_IMAGE} --format '{{.OCIv1.Config.Entrypoint}}'"
  say "  curl -s http://localhost:8787/health   # once the container is running"
}

# ── Main ──
say ""
say "${B}Leap Aide Build${R}"
say "───────────────"
say ""

# Container image build is a standalone mode: the image runs
# `build.sh --frontend` internally to bake static/, so a host-side frontend
# build here would just be thrown away.
if [ "$BUILD_DOCKER_IMAGE" = true ]; then
  if [ "$BUILD_FRONTEND" = true ] || [ "$BUILD_BACKEND" = true ] \
     || [ "$CLEAN" = true ] || [ "$WATCH" = true ] || [ "$RELEASE" = true ]; then
    warn "--docker-image is standalone; ignoring --frontend/--backend/--all/--clean/--watch/--release"
  fi
  build_container_image
  exit 0
fi

# Clean if requested
if [ "$CLEAN" = true ]; then
  clean_build
  say ""
fi

# Build frontend
if [ "$BUILD_FRONTEND" = true ]; then
  build_frontend
fi

# Build backend
if [ "$BUILD_BACKEND" = true ]; then
  build_backend
fi

# Done
say ""
ok "Build complete!"
