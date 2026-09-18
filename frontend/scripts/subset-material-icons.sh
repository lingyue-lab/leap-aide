#!/usr/bin/env bash
# Regenerate the subsetted Material Symbols Rounded font used by the sidebar
# nav icons. The full font is ~5 MB; we ship only the 6 ligatures used by
# Chat / Memory / Tasks / Skills / Workspaces / Terminal, yielding ~180 KB.
#
# Prereq: `pip install fonttools brotli` (pyftsubset CLI must be on PATH).
#
# To add a new sidebar icon: append its Material ligature name to
# LIGATURES below and rerun this script. The Icons.tsx `MATERIAL_FONT_ICONS`
# set and MaterialFontIcon calls must be kept in sync.

set -euo pipefail

LIGATURES="calendar_monthlayersfolder_openterminalsupport_agentpsychologysettingslogout"

# Resolve repo root (this script lives in <repo>/frontend/scripts/).
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="$REPO_ROOT/frontend/public/assets"
OUT_FILE="$OUT_DIR/material-symbols-rounded-subset.woff2"

# Download the full upstream font if not cached locally.
CACHE_DIR="${TMPDIR:-/tmp}/material-symbols-cache"
mkdir -p "$CACHE_DIR"
FULL_FONT="$CACHE_DIR/material-symbols-rounded-full.woff2"

if [ ! -f "$FULL_FONT" ]; then
  echo "[subset] Downloading full Material Symbols Rounded font..."
  TMP_TGZ="$CACHE_DIR/material-symbols.tgz"
  # Pull from the official npm package (same source the backup project used).
  npm pack --silent material-symbols@0.46.0 --pack-destination "$CACHE_DIR"
  tar -xzf "$CACHE_DIR/material-symbols-0.46.0.tgz" \
      -C "$CACHE_DIR" package/material-symbols-rounded.woff2
  mv "$CACHE_DIR/package/material-symbols-rounded.woff2" "$FULL_FONT"
  rm -rf "$CACHE_DIR/package" "$CACHE_DIR/material-symbols-0.46.0.tgz"
fi

mkdir -p "$OUT_DIR"
echo "[subset] Generating $OUT_FILE..."
pyftsubset "$FULL_FONT" \
  --text="$LIGATURES" \
  --flavor=woff2 \
  --output-file="$OUT_FILE" \
  --no-hinting \
  --desubroutinize \
  --drop-tables+=gvar,fvar,HVAR,STAT,avar,VVAR,MVAR \
  --ignore-missing-glyphs

ls -lh "$OUT_FILE"
echo "[subset] Done. Update Icons.tsx MATERIAL_FONT_ICONS set if you added new ligatures."
