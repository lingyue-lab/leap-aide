#!/usr/bin/env bash
# ==============================================================================
# install-plugins.sh — Install LeapAide-managed default agent plugins
# ==============================================================================
#
# Merges the image seed's plugins/ tree into $HERMES_HOME/plugins so the
# gateway can load the default plugins (issue #79). docker-compose bind-mounts
# ${HOME}/.hermes over the runtime HERMES_HOME and the entrypoint's seed copy
# skips top-level entries that already exist, so upgrade volumes would never
# receive the image's plugins — this merge runs at every boot instead.
#
# Layout: source mirrors the Hermes plugins tree — one dir per plugin
# (plugins/<name>/) installs to $HERMES_HOME/plugins/<name>. Flat, like the
# seed's plugins/ dir produced by `hermes plugins install` at image build.
#
# Managed-marker policy (mirrors install-skills.sh):
#   - destination missing           -> install + touch .leap-aide-managed
#   - destination has the marker    -> refresh contents (image updates
#                                      propagate on every boot; no volume
#                                      wipe needed; user edits inside a
#                                      managed dir are replaced by design)
#   - destination exists w/o marker -> skip (user-owned; never clobber)
#   - managed dir absent from source -> prune (renames/removals must not
#                                      leave ghost plugins behind)
#
# Usage:
#   bash backend/scripts/install-plugins.sh               # seed source default
#   bash backend/scripts/install-plugins.sh --source DIR  # explicit source
#
# Source default: ${HERMES_SEED_DIR:-/var/lib/leap-aide/hermes-seed}/plugins
# (the Docker image's build-time seed). HERMES_HOME default: $HOME/.hermes
# (callers using sudo must pass it explicitly, e.g.
# `sudo env HERMES_HOME="$HERMES_HOME" bash ...`).

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
MARKER=".leap-aide-managed"
SRC=""

while [ $# -gt 0 ]; do
    case "$1" in
        --source) SRC="$2"; shift 2 ;;
        *) echo "[install-plugins] Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$SRC" ]; then
    SRC="${HERMES_SEED_DIR:-/var/lib/leap-aide/hermes-seed}/plugins"
fi

if [ ! -d "$SRC" ]; then
    echo "[install-plugins] No plugins source at $SRC — skipping"
    exit 0
fi

DEST_ROOT="${HERMES_HOME}/plugins"
mkdir -p "$DEST_ROOT"

installed=0
refreshed=0
skipped=0
pruned=0
# Space-separated source-relative paths of every installable plugin dir
# ("name"), used by the prune pass below.
src_rels=""

# Collect plugin dirs: one level, the flat plugins/<name>/ layout the seed
# ships. A dir without a marker in the source is still a plugin dir — the
# marker only lives on the destination side.
plugin_dirs=()
for src in "$SRC"/*/; do
    [ -d "$src" ] && plugin_dirs+=("$src")
done

for src in ${plugin_dirs[@]+"${plugin_dirs[@]}"}; do
    rel="${src#"$SRC"/}"
    rel="${rel%/}"
    dst="${DEST_ROOT}/${rel}"
    src_rels="${src_rels} ${rel}"

    if [ -e "$dst" ] && [ ! -f "${dst}/${MARKER}" ]; then
        echo "[install-plugins] skip  ${rel} (user-owned, no ${MARKER} marker)"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -f "${dst}/${MARKER}" ]; then
        action="refresh"
        refreshed=$((refreshed + 1))
    else
        action="install"
        installed=$((installed + 1))
    fi

    mkdir -p "$dst"
    # Replace contents (except the marker) so deleted or renamed files do
    # not accumulate across image updates.
    find "$dst" -mindepth 1 -maxdepth 1 ! -name "$MARKER" -exec rm -rf {} +
    cp -a "${src}." "$dst"/
    touch "${dst}/${MARKER}"
    echo "[install-plugins] ${action} ${rel} -> ${dst}"
done

# Prune marker'd plugin dirs whose source disappeared (renames/removals).
while IFS= read -r marker; do
    [ -f "$marker" ] || continue
    plugin_dst="$(dirname "$marker")"
    rel="${plugin_dst#"$DEST_ROOT"/}"
    case " ${src_rels} " in
        *" ${rel} "*) continue ;;
    esac
    rm -rf "$plugin_dst"
    echo "[install-plugins] prune ${rel} (managed, absent from source)"
    pruned=$((pruned + 1))
done < <(find "$DEST_ROOT" -mindepth 2 -maxdepth 2 -name "$MARKER" -type f 2>/dev/null)

echo "[install-plugins] ✓ ${installed} installed, ${refreshed} refreshed, ${skipped} skipped, ${pruned} pruned"
