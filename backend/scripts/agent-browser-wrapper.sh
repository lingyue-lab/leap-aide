#!/usr/bin/env bash
# ==============================================================================
# agent-browser wrapper
# ==============================================================================
#
# Installed at /usr/local/bin/agent-browser (earlier in PATH than npx).
# Simply resolves to the real agent-browser binary and exec's it.
#
# ── CDP URL resolution (global receptionist integration) ───────────────────
#
# Policy: pure pass-through, with ONE exception — when the global browser
# desktop is up. The shared desktop's receptionist is the only legitimate
# attach target (Chrome itself has NO TCP CDP port — it is on a
# parent-child pipe), so the wrapper strips any caller-supplied
# --cdp / --session and injects the receptionist discovery URL with this
# session's id. Everything else is deliberately none of the wrapper's
# business: whether a session may touch desktop resources at all is
# gated by the terminal approval patterns
# (app.compat.desktop_guard / BROWSER_DESKTOP_BLOCK_STRATEGY), not by
# argument rewriting here. The wrapper does not read that variable —
# under strategy=off a session's --cdp really does go through, which is
# the point of "fully open".
#
# The single source of truth for the desktop endpoint is the global
# state file written by app.api.global_desktop:
#   $HERMES_WEBUI_STATE_DIR/browser_desktop.json
#   (fallback when that env var is absent: ~/.hermes/webui/browser_desktop.json)
# When it is absent (desktop down), the wrapper is a pure pass-through
# so agent-browser falls back to its own local headless browser.
#
# LeapAide never injects BROWSER_CDP_URL into the child env — this wrapper
# resolves the endpoint straight from the state file via
# HERMES_SESSION_KEY (injected per-session by _IsolatedPopen), so there
# is no env copy that can race or go stale across concurrent sessions.

set -euo pipefail

# ── Global desktop JSON resolution ─────────────────────────────────────────
# HERMES_WEBUI_STATE_DIR is the documented contract (entrypoint + skill).
# HOME alone is wrong when the server runs with HOME=/root while the state
# lives under /home/leap-aide/.hermes — the wrapper would then never see
# the desktop as up and silently pass through to local headless.
STATE_DIR="${HERMES_WEBUI_STATE_DIR:-${HOME}/.hermes/webui}"
JSON_FILE="${STATE_DIR}/browser_desktop.json"
# Extract session_id from HERMES_SESSION_KEY (strip "agent:main:webui:dm:"
# prefix; a bare session id passes through unchanged).
SID="${HERMES_SESSION_KEY:-}"
SID="${SID##*:}"

# DESKTOP_UP=true (state file present and active) triggers the
# strip-and-inject override below; any other state (degraded, file
# missing, unreadable) leaves the wrapper as a pure pass-through so
# agent-browser falls back to its own local headless browser.
DESKTOP_UP=false
FINAL_CDP=""
if [[ -f "$JSON_FILE" ]]; then
    _state=$(python3 -c "import json; d=json.load(open('$JSON_FILE')); print(d.get('status',''), d.get('receptionist_url',''))" 2>/dev/null || echo "")
    _status=$(echo "$_state" | awk '{print $1}')
    _url=$(echo "$_state" | awk '{print $2}')
    if [[ "$_status" == "active" && -n "$_url" ]]; then
        DESKTOP_UP=true
        FINAL_CDP="${_url%/}/json/version?sid=${SID}"
    fi
fi

if [[ "$DESKTOP_UP" == "true" && -z "$SID" ]]; then
    # No session identity in this context: a sid-less discovery URL would
    # be rejected by the receptionist (sid required), so stay a pure
    # pass-through instead of injecting a guaranteed-400 endpoint.
    echo "agent-browser wrapper: HERMES_SESSION_KEY missing; not injecting receptionist URL" >&2
fi

if [[ "$DESKTOP_UP" == "true" && -n "$SID" ]]; then
    # Rebuild argument list: strip any caller-supplied --cdp / --session
    # (the receptionist is the only attach target), then inject the
    # receptionist endpoint.
    # (--cdp takes priority over --session in agent-browser >= 0.13)
    NEW_ARGS=()
    SKIP_NEXT=false
    for arg in "$@"; do
        if [[ "$SKIP_NEXT" == "true" ]]; then
            SKIP_NEXT=false
            continue
        fi
        case "$arg" in
            --cdp)
                SKIP_NEXT=true    # skip --cdp and its value
                ;;
            --cdp=*)
                ;;               # delete existing --cdp=...
            --session)
                SKIP_NEXT=true    # skip --session and its value
                ;;
            --session=*)
                ;;               # delete existing --session=...
            *)
                NEW_ARGS+=("$arg")
                ;;
        esac
    done
    NEW_ARGS+=("--cdp" "$FINAL_CDP")
    if ((${#NEW_ARGS[@]})); then
        set -- "${NEW_ARGS[@]}"
    fi
fi

# ── Recursion guard ──────────────────────────────────────────────────────────
# If already running through this wrapper, go directly to npx
# with a clean PATH to prevent infinite wrapper → npx → wrapper loops
if [[ -n "${HERMES_AB_WRAPPER_GUARD:-}" ]]; then
    _clean_path=""
    IFS=':' read -ra _path_arr <<< "$PATH"
    for _p in "${_path_arr[@]}"; do
        [[ "$_p" != "/usr/local/bin" ]] && _clean_path="${_clean_path}${_clean_path:+:}$_p"
    done
    PATH="$_clean_path" exec npx agent-browser "$@"
fi
export HERMES_AB_WRAPPER_GUARD=1

REAL_AB=""
for candidate in $(find /root/.npm/_npx -maxdepth 6 \( -type f -o -type l \) -name 'agent-browser' 2>/dev/null | head -5); do
    if head -c 100 "$candidate" 2>/dev/null | grep -q 'HERMES_SESSION_KEY'; then
        continue  # skip wrapper copies
    fi
    REAL_AB="$candidate"
    break
done

if [[ -z "$REAL_AB" ]]; then
    # Strip /usr/local/bin from PATH so npx doesn't invoke this wrapper again
    _clean_path=""
    IFS=':' read -ra _path_arr <<< "$PATH"
    for _p in "${_path_arr[@]}"; do
        [[ "$_p" != "/usr/local/bin" ]] && _clean_path="${_clean_path}${_clean_path:+:}$_p"
    done
    PATH="$_clean_path" exec npx agent-browser "$@"
fi

exec "$REAL_AB" "$@"
