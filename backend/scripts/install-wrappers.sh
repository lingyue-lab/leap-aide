#!/usr/bin/env bash
# ==============================================================================
# install-wrappers.sh — Install Hermes browser wrappers
# ==============================================================================
#
# Installs wrapper scripts that intercept Chrome and agent-browser launches
# to enable VNC observation and cookie persistence on Xvfb virtual displays.
#
# Usage:
#   bash backend/scripts/install-wrappers.sh               # Idempotent install
#   bash backend/scripts/install-wrappers.sh --force        # Force reinstall
#   bash backend/scripts/install-wrappers.sh --uninstall    # Remove all wrappers
#
# What gets installed:
#   1. chrome-wrapper.sh at Playwright's Chromium path
#   2. chrome-wrapper.sh at /usr/local/bin/hermes-chrome
#   3. agent-browser-wrapper.sh at /usr/local/bin/agent-browser

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHROME_WRAPPER="${SCRIPTS_DIR}/chrome-wrapper.sh"
AGENT_BROWSER_WRAPPER="${SCRIPTS_DIR}/agent-browser-wrapper.sh"
FORCE="${FORCE_INSTALL:-false}"

# ── install ─────────────────────────────────────────────────────────────────

install_all() {
    local ok=true

    # -- 1. Find Playwright Chrome -----------------------------------------------
    echo "[install-wrappers] Finding Playwright Chromium binary..."
    local chrome_paths
    chrome_paths=$(find /root/.cache/ms-playwright -name chrome -type f 2>/dev/null || true)

    if [[ -z "$chrome_paths" ]]; then
        echo "[install-wrappers] ERROR: No Playwright Chrome binary found under /root/.cache/ms-playwright" >&2
        echo "[install-wrappers] Make sure 'npx playwright install chromium' has been run." >&2
        return 1
    fi

    local chrome_bin
    chrome_bin=$(echo "$chrome_paths" | head -1)
    local chrome_dir
    chrome_dir="$(dirname "$chrome_bin")"
    local chrome_real="${chrome_dir}/chrome.real"
    local pw_wrapper_dest="${chrome_dir}/chrome"

    echo "[install-wrappers] Found Chromium at: $chrome_dir"
    [[ "$FORCE" == "true" ]] && echo "[install-wrappers] Force mode: will reinstall even if already present"

    # -- 2. Rename chrome → chrome.real ------------------------------------------
    if [[ -f "$chrome_real" ]]; then
        echo "[install-wrappers] chrome.real already exists, skipping rename"
    else
        echo "[install-wrappers] Renaming chrome → chrome.real"
        mv "$chrome_bin" "$chrome_real"
    fi

    # -- 3. Install at Playwright path -------------------------------------------
    if [[ "$(realpath "$CHROME_WRAPPER")" == "$(realpath "$pw_wrapper_dest")" ]] && [[ "$FORCE" != "true" ]]; then
        echo "[install-wrappers] Already installed at Playwright path, skipping"
    else
        echo "[install-wrappers] Installing chrome-wrapper → $pw_wrapper_dest"
        cp "$CHROME_WRAPPER" "$pw_wrapper_dest"
        chmod 755 "$pw_wrapper_dest"
    fi
    if ! grep -q "HERMES_SESSION_KEY" "$pw_wrapper_dest" 2>/dev/null; then
        echo "[install-wrappers] ✗ Verification failed — $pw_wrapper_dest" >&2
        ok=false
    fi

    # -- 4. Install hermes-chrome ------------------------------------------------
    local hermes_chrome="/usr/local/bin/hermes-chrome"
    if [[ "$(realpath "$CHROME_WRAPPER")" == "$(realpath "$hermes_chrome")" ]] && [[ "$FORCE" != "true" ]]; then
        echo "[install-wrappers] Already installed at $hermes_chrome, skipping"
    else
        echo "[install-wrappers] Installing chrome-wrapper → $hermes_chrome"
        cp "$CHROME_WRAPPER" "$hermes_chrome"
        chmod 755 "$hermes_chrome"
    fi
    if ! grep -q "HERMES_SESSION_KEY" "$hermes_chrome" 2>/dev/null; then
        echo "[install-wrappers] ✗ Verification failed — $hermes_chrome" >&2
        ok=false
    fi

    # -- 5. Install agent-browser wrapper ----------------------------------------
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
        echo "[install-wrappers] ✓ All wrappers installed"
        echo "[install-wrappers]   Playwright chrome: $pw_wrapper_dest"
        echo "[install-wrappers]   Hermes chrome:     $hermes_chrome"
        echo "[install-wrappers]   Agent browser:     $ab_wrapper"
    else
        echo "[install-wrappers] ⚠ Installation completed with errors" >&2
    fi
}


# ── uninstall ───────────────────────────────────────────────────────────────

uninstall_all() {
    echo "[install-wrappers] Removing wrappers..."

    # Restore Playwright chrome
    local chrome_real_paths
    chrome_real_paths=$(find /root/.cache/ms-playwright -name chrome.real -type f 2>/dev/null || true)
    if [[ -n "$chrome_real_paths" ]]; then
        local chrome_real
        chrome_real=$(echo "$chrome_real_paths" | head -1)
        local chrome_dir
        chrome_dir="$(dirname "$chrome_real")"
        local wrapper_path="${chrome_dir}/chrome"
        if [[ -f "$wrapper_path" ]] && grep -q "HERMES_SESSION_KEY" "$wrapper_path" 2>/dev/null; then
            rm "$wrapper_path"
            mv "$chrome_real" "$wrapper_path"
            echo "[install-wrappers] ✓ Restored: $wrapper_path"
        fi
    fi

    # Remove standalone wrappers
    for f in "/usr/local/bin/hermes-chrome" "/usr/local/bin/agent-browser"; do
        if [[ -f "$f" ]] && grep -q 'HERMES_SESSION_KEY' "$f" 2>/dev/null; then
            rm "$f"
            echo "[install-wrappers] ✓ Removed: $f"
        fi
    done

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
