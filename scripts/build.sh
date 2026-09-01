#!/usr/bin/env bash
# build.sh — Build frontend and/or pre-compile Python
#
# Usage:
#   ./scripts/build.sh                 # Build everything
#   ./scripts/build.sh --frontend      # Frontend only
#   ./scripts/build.sh --python        # Python only
#   ./scripts/build.sh --clean         # Clean build artifacts
#   ./scripts/build.sh --watch         # Watch mode (frontend)
#   ./scripts/build.sh --release       # Production build (minified)
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
BUILD_PYTHON=false
CLEAN=false
WATCH=false
RELEASE=false

# If no arguments, build everything
if [ $# -eq 0 ]; then
  BUILD_FRONTEND=true
  BUILD_PYTHON=true
fi

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
  case $1 in
    --frontend|-f)
      BUILD_FRONTEND=true
      shift
      ;;
    --python|-p)
      BUILD_PYTHON=true
      shift
      ;;
    --all|-a)
      BUILD_FRONTEND=true
      BUILD_PYTHON=true
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
    --help|-h)
      echo "Usage: build.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --frontend, -f    Build frontend only"
      echo "  --python, -p      Pre-compile Python only"
      echo "  --all, -a         Build everything (default)"
      echo "  --clean, -c       Clean build artifacts"
      echo "  --watch, -w       Watch mode (frontend)"
      echo "  --release, -r     Release build (minified)"
      echo "  -h, --help        Show this help"
      echo ""
      echo "Examples:"
      echo "  ./scripts/build.sh              # Build all"
      echo "  ./scripts/build.sh -f           # Frontend only"
      echo "  ./scripts/build.sh -p           # Python only"
      echo "  ./scripts/build.sh -c -a        # Clean and rebuild all"
      echo "  ./scripts/build.sh -w           # Watch frontend for changes"
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
  if command -v gzip >/dev/null 2>&1; then
    local count=0
    for f in "$PROJECT_ROOT"/static/assets/*.js "$PROJECT_ROOT"/static/assets/*.css; do
      [ -f "$f" ] || continue
      gzip -k -9 "$f"
      count=$((count + 1))
    done
    if [ "$count" -gt 0 ]; then
      ok "Pre-compressed $count text assets"
    fi
  fi

  ok "Frontend built"
}

build_python() {
  say "${B}Pre-compiling Python bytecode...${R}"

  if ! command -v python3 >/dev/null 2>&1; then
    warn "Python3 not found — skipping"
    return 0
  fi

  # Count files before
  local count_before
  count_before=$(find "$PROJECT_ROOT" -name "*.pyc" 2>/dev/null | wc -l)

  # Compile
  python3 -m compileall -q "$PROJECT_ROOT" 2>/dev/null || true

  # Count files after
  local count_after
  count_after=$(find "$PROJECT_ROOT" -name "*.pyc" 2>/dev/null | wc -l)

  local new_files=$((count_after - count_before))
  ok "Python bytecode compiled ($new_files new .pyc files)"
}

# ── Main ──
say ""
say "${B}Leap Aide Build${R}"
say "───────────────"
say ""

# Clean if requested
if [ "$CLEAN" = true ]; then
  clean_build
  say ""
fi

# Build frontend
if [ "$BUILD_FRONTEND" = true ]; then
  build_frontend
fi

# Build Python
if [ "$BUILD_PYTHON" = true ]; then
  build_python
fi

# Done
say ""
ok "Build complete!"
