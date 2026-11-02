#!/usr/bin/env bash
# ==============================================================================
# install-wrappers.sh — Install Hermes browser wrappers
# ==============================================================================
#
# Installs the agent-browser wrapper that redirects browser launches to the
# global desktop's receptionist (issue #37: one shared Chrome, no per-session
# Chrome and no Playwright chrome-wrapper anymore).
#
# Usage:
#   bash backend/scripts/install-wrappers.sh               # Idempotent install
#   bash backend/scripts/install-wrappers.sh --force        # Force reinstall
#   bash backend/scripts/install-wrappers.sh --uninstall    # Remove all wrappers
#
# What gets installed:
#   1. agent-browser-wrapper.sh at /usr/local/bin/agent-browser
#
# Legacy cleanup (per-session era): restores Playwright's chrome binary
# (undoing the old chrome → chrome.real rename + wrapper install) and
# removes the old /usr/local/bin/hermes-chrome wrapper.

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_BROWSER_WRAPPER="${SCRIPTS_DIR}/agent-browser-wrapper.sh"
FORCE="${FORCE_INSTALL:-false}"

# ── legacy chrome-wrapper teardown ──────────────────────────────────────────

restore_playwright_chrome() {
    # If a previous install renamed chrome → chrome.real and dropped the
    # chrome-wrapper at the original path, restore the real binary. The
    # shared desktop launches Chrome directly (chrome.real or chrome);
    # nothing needs to intercept Playwright's binary anymore.
    local chrome_real_paths
    chrome_real_paths=$(find /root/.cache/ms-playwright -name chrome.real -type f 2>/dev/null || true)
    if [[ -z "$chrome_real_paths" ]]; then
        return 0
    fi
    local chrome_real
    chrome_real=$(echo "$chrome_real_paths" | head -1)
    local chrome_dir
    chrome_dir="$(dirname "$chrome_real")"
    local wrapper_path="${chrome_dir}/chrome"
    if [[ -f "$wrapper_path" ]] && grep -q "HERMES_SESSION_KEY" "$wrapper_path" 2>/dev/null; then
        rm "$wrapper_path"
        mv "$chrome_real" "$wrapper_path"
        echo "[install-wrappers] ✓ Restored Playwright chrome: $wrapper_path"
    fi
}

remove_legacy_chrome_wrappers() {
    local f
    for f in "/usr/local/bin/hermes-chrome"; do
        if [[ -f "$f" ]] && grep -q 'HERMES_SESSION_KEY' "$f" 2>/dev/null; then
            rm "$f"
            echo "[install-wrappers] ✓ Removed legacy wrapper: $f"
        fi
    done
}

# ── install ─────────────────────────────────────────────────────────────────

install_all() {
    local ok=true

    restore_playwright_chrome
    remove_legacy_chrome_wrappers

    # -- Install agent-browser wrapper ------------------------------------------
    local ab_wrapper="/usr/local/bin/agent-browser"
    if [[ -f "$AGENT_BROWSER_WRAPPER" ]]; then
        if [[ "$(realpath "$AGENT_BROWSER_WRAPPER")" == "$(realpath "$ab_wrapper")" ]] && [[ "$FORCE" != "true" ]]; then
            echo "[install-wrappers] agent-browser wrapper already installed, skipping"
        else
            echo "[install-wrappers] Installing agent-browser-wrapper → $ab_wrapper"
            cp "$AGENT_BROWSER_WRAPPER" "$ab_wrapper"
            chmod 755 "$ab_wrapper"
        fi
        if ! grep -q 'HERMES_SESSION_KEY' "$ab_wrapper" 2>/dev/null; then
            echo "[install-wrappers] ✗ Verification failed — $ab_wrapper" >&2
            ok=false
        fi
    else
        echo "[install-wrappers] ⚠ agent-browser-wrapper.sh not found at $AGENT_BROWSER_WRAPPER" >&2
    fi

    # -- Summary -----------------------------------------------------------------
    if $ok; then
        echo "[install-wrappers] ✓ Wrapper installed"
        echo "[install-wrappers]   Agent browser:     $ab_wrapper"
    else
        echo "[install-wrappers] ⚠ Installation completed with errors" >&2
    fi
}


# ── uninstall ───────────────────────────────────────────────────────────────

uninstall_all() {
    echo "[install-wrappers] Removing wrappers..."

    restore_playwright_chrome
    remove_legacy_chrome_wrappers

    # Remove the agent-browser wrapper (only if it is ours)
    local f="/usr/local/bin/agent-browser"
    if [[ -f "$f" ]] && grep -q 'HERMES_SESSION_KEY' "$f" 2>/dev/null; then
        rm "$f"
        echo "[install-wrappers] ✓ Removed: $f"
    fi

    echo "[install-wrappers] ✓ Uninstall complete"
}


# ── Argument dispatch ───────────────────────────────────────────────────────

case "${1:-}" in
    --force)
        FORCE_INSTALL=true install_all
        ;;
    --uninstall)
        uninstall_all
        ;;
    *)
        install_all
        ;;
esac
