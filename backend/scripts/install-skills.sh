#!/usr/bin/env bash
# ==============================================================================
# install-skills.sh — Install LeapAide-managed agent skills
# ==============================================================================
#
# Merges the repo/image skills/ tree into $HERMES_HOME/skills so Hermes can
# discover the skills (a repo-root skills/ dir is NOT a Hermes discovery
# root) and session-skill binding can resolve them via SKILLS_DIR (issue #9).
#
# Layout: source mirrors the Hermes skills tree — top-level skill dirs
# (skills/<name>/SKILL.md) install to $HERMES_HOME/skills/<name>; category
# subdirs (skills/<category>/<name>/SKILL.md) install to
# $HERMES_HOME/skills/<category>/<name> and the panel derives the category
# from the parent dir name. Hub category dirs (no marker) are never touched.
#
# Managed-marker policy:
#   - destination missing           -> install + touch .leap-aide-managed
#   - destination has the marker    -> refresh contents (image updates
#                                      propagate on every boot; no volume
#                                      wipe needed; user edits inside a
#                                      managed dir are replaced by design)
#   - destination exists w/o marker -> skip (user-owned; never clobber)
#   - managed dir absent from source -> prune (renames/removals must not
#                                      leave ghost skills behind; only
#                                      marker'd skill dirs are pruned,
#                                      category container dirs are kept)
#
# Usage:
#   bash backend/scripts/install-skills.sh               # auto-detect source
#   bash backend/scripts/install-skills.sh --source DIR  # explicit source
#
# Source default: /app/skills (Docker image) else <repo>/skills.
# HERMES_HOME default: $HOME/.hermes (callers using sudo must pass it
# explicitly, e.g. `sudo env HERMES_HOME="$HERMES_HOME" bash ...`).

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
MARKER=".leap-aide-managed"
SRC=""

while [ $# -gt 0 ]; do
    case "$1" in
        --source) SRC="$2"; shift 2 ;;
        *) echo "[install-skills] Unknown arg: $1" >&2; exit 2 ;;
    esac
done

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "$SRC" ]; then
    if [ -d /app/skills ]; then
        SRC=/app/skills
    else
        SRC="$(cd "${SCRIPTS_DIR}/../.." && pwd)/skills"
    fi
fi

if [ ! -d "$SRC" ]; then
    echo "[install-skills] No skills source at $SRC — skipping"
    exit 0
fi

DEST_ROOT="${HERMES_HOME}/skills"
mkdir -p "$DEST_ROOT"

installed=0
refreshed=0
skipped=0
pruned=0
# Space-separated source-relative paths of every installable skill
# ("name" or "category/name"), used by the prune pass below.
src_rels=""

# Collect skill dirs: top-level, plus one level of category nesting.
# A dir with SKILL.md is a skill; otherwise scan its subdirs for skills.
skill_dirs=()
for src in "$SRC"/*/; do
    [ -d "$src" ] || continue
    if [ -f "${src}SKILL.md" ]; then
        skill_dirs+=("$src")
        continue
    fi
    for sub in "$src"*/; do
        [ -f "${sub}SKILL.md" ] && skill_dirs+=("$sub")
    done
done

for src in ${skill_dirs[@]+"${skill_dirs[@]}"}; do
    rel="${src#"$SRC"/}"
    rel="${rel%/}"
    dst="${DEST_ROOT}/${rel}"
    src_rels="${src_rels} ${rel}"

    if [ -e "$dst" ] && [ ! -f "${dst}/${MARKER}" ]; then
        echo "[install-skills] skip  ${rel} (user-owned, no ${MARKER} marker)"
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
    # Full copy, including the Chinese companion (SKILL.zh-CN.md) — agents
    # load it on demand via skill_view; Hermes only indexes SKILL.md.
    cp -a "${src}." "$dst"/
    touch "${dst}/${MARKER}"
    echo "[install-skills] ${action} ${rel} -> ${dst}"
done

# Prune marker'd skill dirs whose source disappeared (renames/removals).
# find covers depth 2 (top-level) and depth 3 (category/<name>) markers.
# Only the skill dir itself is removed — category container dirs are kept
# (hub categories never carry the marker, so they are never matched).
while IFS= read -r marker; do
    [ -f "$marker" ] || continue
    skill_dst="$(dirname "$marker")"
    rel="${skill_dst#"$DEST_ROOT"/}"
    case " ${src_rels} " in
        *" ${rel} "*) continue ;;
    esac
    rm -rf "$skill_dst"
    echo "[install-skills] prune ${rel} (managed, absent from source)"
    pruned=$((pruned + 1))
done < <(find "$DEST_ROOT" -mindepth 2 -maxdepth 3 -name "$MARKER" -type f 2>/dev/null)

echo "[install-skills] ✓ ${installed} installed, ${refreshed} refreshed, ${skipped} skipped, ${pruned} pruned"
