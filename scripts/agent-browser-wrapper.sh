#!/usr/bin/env bash
# ==============================================================================
# agent-browser wrapper
# ==============================================================================
#
# Installed at /usr/local/bin/agent-browser (earlier in PATH than npx).
# Simply resolves to the real agent-browser binary and exec's it.
#
# ── CDP URL resolution (SessionDisplay integration) ────────────────────────
#
# When BROWSER_CDP_URL is the sentinel value "http://127.0.0.1:19999", this
# wrapper resolves the real CDP URL from the per-session display config:
#   ~/.hermes/webui/sessions_display/<HERMES_SESSION_KEY>.json
#
# This sentinel mechanism is immune to cross-session os.environ races: every
# SessionDisplay session writes the *same* sentinel value, so concurrent
# overwrites are harmless.  The real CDP port is read from the session's own
# JSON file, which is session-granular and race-free.
#
# Precedence:
#   1. Global BROWSER_CDP_URL (non-sentinel) — e.g. CLI /browser connect
#   2. SessionDisplay JSON (when sentinel is set)
#   3. Neither — passthrough (agent-browser uses its default logic)

set -euo pipefail

# ── Sentinel CDP URL resolution ────────────────────────────────────────────
SENTINEL="http://127.0.0.1:19999"
SESSIONS_DIR="${HOME}/.hermes/webui/sessions_display"
# Extract session_id from HERMES_SESSION_KEY (strip "agent:main:webui:dm:" prefix)
SID="${HERMES_SESSION_KEY##*:}"

FINAL_CDP=""

if [[ -n "${BROWSER_CDP_URL:-}" && "$BROWSER_CDP_URL" != "$SENTINEL" ]]; then
    # Global real CDP URL set (e.g. CLI /browser connect) — use directly
    FINAL_CDP="$BROWSER_CDP_URL"
elif [[ "$BROWSER_CDP_URL" == "$SENTINEL" && -n "$SID" ]]; then
    # Sentinel → resolve from SessionDisplay JSON
    JSON_FILE="${SESSIONS_DIR}/${SID}.json"
    if [[ -f "$JSON_FILE" ]]; then
        FINAL_CDP=$(python3 -c "import json; print(json.load(open('$JSON_FILE')).get('cdp_url', ''))" 2>/dev/null || echo "")
    fi
fi

if [[ -n "$FINAL_CDP" ]]; then
    # Rebuild argument list: inject --cdp, strip any existing --cdp / --session
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
    set -- "${NEW_ARGS[@]}"
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
