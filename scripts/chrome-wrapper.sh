#!/usr/bin/env bash
# ==============================================================================
# chrome-wrapper.sh — Virtual display Chrome interceptor
# ==============================================================================
#
# Intercepts Chrome binary calls and redirects to an Xvfb virtual display
# so the browser is visible through noVNC observer windows.
#
# When SessionDisplay is NOT active, this script is transparent passthrough
# — Chrome runs in its default headless mode unchanged.
#
# ── Installation ───────────────────────────────────────────────────────────
# Use install-wrappers.sh to deploy this and related wrappers:
#
#   bash scripts/install-wrappers.sh           # Idempotent install
#   bash scripts/install-wrappers.sh --force   # Force reinstall
#   bash scripts/install-wrappers.sh --uninstall  # Remove all wrappers
#
# ── Filesystem layout ───────────────────────────────────────────────────────
#
# This script replaces the 'chrome' binary at multiple locations:
#
#   /root/.cache/ms-playwright/<chromium-XXXX>/chrome  ← Playwright path
#   /usr/local/bin/hermes-chrome                       ← agent-browser path
#
# Both point to the real binary at:
#   /root/.cache/ms-playwright/<chromium-XXXX>/chrome.real
#
# ── Runtime behavior ────────────────────────────────────────────────────────
#
# HERMES_SESSION_KEY is injected into the subprocess environment by
# api/streaming.py.  This wrapper reads it and looks up
# ~/.hermes/webui/sessions_display/<sid>.json to decide whether to
# enable virtual display rendering.
#
# Three-layer defensive fallback:
#   1. $HERMES_SESSION_KEY empty → passthrough
#   2. JSON config not found       → passthrough
#   3. JSON missing required fields → passthrough
#
# Main path (all checks passed):
#   - DISPLAY=:<display_num>        (renders on Xvfb virtual screen)
#   - Strips --headless* flags      (visible window instead of headless)
#   - --start-maximized --window-position=0,0
#   - exec chrome.real with modified arguments

set -euo pipefail

# ── Find the real Chrome binary ────────────────────────────────────────────
# Priority:
#   1. Playwright cache search       — always works, binary in original dir + resources
#   2. $SCRIPT_DIR/chrome.real       — when wrapper installed alongside Chrome (backup)
#   3. Unwrapped chrome binary       — if not yet renamed to .real (unusual)

_real_chrome=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Priority 1: find in Playwright cache (binary stays with its resource files)
_real_chrome=$(find /root/.cache/ms-playwright -name 'chrome.real' -type f -executable 2>/dev/null | head -1)

# Priority 2: $SCRIPT_DIR/chrome.real (only if SCRIPT_DIR is inside ms-playwright)
if [[ -z "$_real_chrome" ]]; then
    if [[ -x "${SCRIPT_DIR}/chrome.real" ]] && [[ "$SCRIPT_DIR" == */ms-playwright/* ]]; then
        _real_chrome="${SCRIPT_DIR}/chrome.real"
    fi
fi

# Priority 3: unwrapped chrome binary (not yet renamed)
if [[ -z "$_real_chrome" ]]; then
    _candidates=$(find /root/.cache/ms-playwright -name 'chrome' -type f 2>/dev/null || true)
    for _c in $_candidates; do
        if ! grep -q 'HERMES_SESSION_KEY' "$_c" 2>/dev/null; then
            _real_chrome="$_c"
            break
        fi
    done
fi

if [[ -z "$_real_chrome" || ! -x "$_real_chrome" ]]; then
    echo "[chrome-wrapper] FATAL: Cannot find real Chrome binary" >&2
    exit 1
fi
REAL_CHROME="$_real_chrome"

SESSIONS_DIR="${HOME}/.hermes/webui/sessions_display"
SID="${HERMES_SESSION_KEY:-}"

# ── Fallback 1: No session key ────────────────────────────────────────────
if [[ -z "$SID" ]]; then
    exec "${REAL_CHROME}" "$@"
fi

JSON_FILE="${SESSIONS_DIR}/${SID}.json"

# ── Fallback 2: JSON config not found ─────────────────────────────────────
if [[ ! -f "$JSON_FILE" ]]; then
    exec "${REAL_CHROME}" "$@"
fi

# ── Parse JSON ────────────────────────────────────────────────────────────
DISPLAY_NUM=$(python3 -c "import json; print(json.load(open('$JSON_FILE')).get('display', ''))" 2>/dev/null || echo "")
VNC_PORT=$(python3 -c "import json; print(json.load(open('$JSON_FILE')).get('vnc_port', ''))" 2>/dev/null || echo "")

# ── Fallback 3: JSON missing required fields ──────────────────────────────
if [[ -z "$DISPLAY_NUM" || -z "$VNC_PORT" ]]; then
    exec "${REAL_CHROME}" "$@"
fi

# ── Main path: Run Chrome on the virtual display ──────────────────────────
export DISPLAY=":${DISPLAY_NUM}"

# Build filtered argument list: strip --headless variants
ARGS=()
HAS_CDP_PORT=false
for arg in "$@"; do
    case "$arg" in
        --headless|--headless=new|--headless=*)
            ;;  # skip — we want visible rendering on Xvfb
        --no-startup-window)
            ;;  # skip — we want a window
        --remote-debugging-port*)
            HAS_CDP_PORT=true
            ARGS+=("$arg")
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

# Inject --remote-debugging-port if not already set
if [[ "$HAS_CDP_PORT" == "false" ]]; then
    CDP_PORT=$((VNC_PORT + 1000))
    ARGS+=(--remote-debugging-port="${CDP_PORT}")
fi

# Ensure Chrome creates a visible window occupying left half of the screen
ARGS+=(--no-first-run --no-default-browser-check --disable-features=TranslateUI)
ARGS+=(--window-size=960,1080 --window-position=0,0 --disable-backgrounding-occluded-windows)

exec "${REAL_CHROME}" "${ARGS[@]}"
