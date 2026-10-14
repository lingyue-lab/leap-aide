#!/usr/bin/env bash
# ============================================================================
# npm_warmup.sh — Pre-warm npm postinstall caches by cloning hermes-agent,
# auto-discovering versions, and curl-fetching binaries ahead of time.
# ============================================================================
#
# Why
# ---
# hermes-agent's apps/desktop workspace ships `electron@$VER` which downloads
# ~150MB on `npm install` postinstall. From China, GitHub releases are slow /
# blocked. Setting `ELECTRON_MIRROR` env var in the container is unreliable
# (verified 2026-10-08: env var non-empty but electron@40.10.2's `node
# install.js` still hangs on GitHub).
#
# Same problem for hermes-agent's playwright postinstall: it calls
# `npx playwright install` to fetch chromium (~150MB) and ffmpeg (~5MB) from
# the mirror. playwright-core has a HARDCODED 30s per-attempt timeout in
# coreBundle.js that times out from China even with mirror URLs — fails with
# "Failed to download FFmpeg (playwright ffmpeg v1013)" after 3 retries.
#
# This script avoids the postinstall download entirely by pre-populating
# the cache that electron's own `node install.js` looks at. The flow:
#   1. Clone hermes-agent (shallow, no-checkout + git checkout SHA).
#   2. Walk package.json files, find electron versions.
#   3. curl + retry the .zip AND .sha256sum for each version into
#      /root/.cache/electron/ (the path install.js checks).
#
# When install.js runs later and finds both files in the cache, it does NOT
# hit the network — all mirror env vars are bypassed.
#
# Modes
# -----
#   (default)   pre-warm: clone, discover, pre-fetch
#   --clean     remove warmup clone + pre-fetched electron cache
#               (saves ~150MB in image). Deliberately keeps the playwright
#               browsers cache — that is the installed browser, not scratch.
#
# Docker integration
# ------------------
#   The Dockerfile invokes this script only when the BUILD_NPM_WARMUP
#   build-arg is truthy (1/true/yes/on). It is OFF by default: without the
#   warmup, install.sh falls back to its own mirror downloads, which works on
#   any builder and needs no git_identity_key secret. Turn it on with
#       docker build --build-arg BUILD_NPM_WARMUP=1 ...
#   The `--clean` invocation in the Dockerfile is gated on the same flag.
#
# Non-fatal: any failure logs a warning and exits 0. The real npm install
# will still try to download via mirror.
#
# Env vars
# ---------
#   HERMES_AGENT_REPO_URL     Required (for pre-warm mode).
#   HERMES_AGENT_COMMIT       Required (for pre-warm mode).
#   BUILD_ENV_FILE            Build defaults file to load instead of the
#                             auto-discovered .build.env.
#   WARMUP_DIR                Where to clone hermes-agent. Default: /tmp/hermes-agent-warmup
#   ELECTRON_MIRROR           Mirror base URL. Default: cdn.npmmirror.com direct.
#   ELECTRON_CACHE_DIR        Where electron install.js looks. Default: /root/.cache/electron
# ============================================================================

set -euo pipefail

# ── Argument parsing ──
case "${1:-}" in
  --clean) _MODE=clean ;;
  --help|-h)
    sed -n '2,/^# ====/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "") _MODE=warm ;;
  *) echo "Unknown arg: $1" >&2; exit 1 ;;
esac

# ── Config ──
HERMES_AGENT_REPO_URL="${HERMES_AGENT_REPO_URL:-}"
HERMES_AGENT_COMMIT="${HERMES_AGENT_COMMIT:-}"
WARMUP_DIR="${WARMUP_DIR:-/tmp/hermes-agent-warmup}"
# cnpm API path that 302s to cdn.npmmirror.com/binaries/electron/.
# Verified 2026-10-08: v40.10.2/linux-x64 zip → 302 → 200 (115MB).
# Trailing slash required by @electron/get (joins `${mirror}v${ver}/...` with
# no separator). The fetch_one calls below strip it via ${ELECTRON_MIRROR%/}
# so this script also tolerates a slash-less override.
ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://registry.npmmirror.com/-/binary/electron/}"
ELECTRON_CACHE_DIR="${ELECTRON_CACHE_DIR:-/root/.cache/electron}"
# Playwright browsers (chromium + ffmpeg) — same mirror as hermes-agent's
# postinstall. Inherits from BUILD_PLAYWRIGHT_DOWNLOAD_HOST (set by the
# Dockerfile) when available, else defaults to npmmirror.
# cnpm API path that 302s to cdn.npmmirror.com/binaries/playwright/.
# Verified 2026-10-08: chromium/1187 + ffmpeg/1013 zips → 302 → 200.
# browsers.json is NOT mirrored (404) — expected, playwright-core generates
# it. Our warmup discovers revisions via this cnpm JSON API instead.
PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-${BUILD_PLAYWRIGHT_DOWNLOAD_HOST:-https://registry.npmmirror.com/-/binary/playwright}}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/root/.cache/ms-playwright}"

# ── Single source of truth ──
# Source .build.env at the repo root for version metadata (PLAYWRIGHT_FALLBACK_*
# and any future fields). The values are also hardcoded below as a safety net
# for when the file is missing (e.g. running the warmup outside the repo) — but
# that fallback is now announced, because a stale path here is otherwise
# invisible: the file is read at build time from inside the image
# (/app/.build.env), where a missing .dockerignore whitelist entry drops it.
#
# NOTE the relative candidate is ONE level up from this script's dir (repo
# root), not the two it used to be when the file lived in backend/scripts/.
_env_file=""
for _candidate in \
    "${BUILD_ENV_FILE:-}" \
    "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/../.build.env" \
    "./.build.env" \
    "/app/.build.env"; do
  if [ -n "$_candidate" ] && [ -f "$_candidate" ]; then
    _env_file="$_candidate"
    break
  fi
done
if [ -n "$_env_file" ]; then
  # Caller wins: snapshot every key the file declares, restore it afterwards.
  # Without this the source would clobber a PLAYWRIGHT_FALLBACK_* (or
  # HERMES_AGENT_COMMIT) that the build-arg exported — same idiom as
  # scripts/ctl.sh. Costs one extra read of a small file.
  _env_keep=()
  while IFS= read -r _line || [ -n "$_line" ]; do
    _stmt="${_line#export }"
    case "$_stmt" in
      *=*) ;;
      *) continue ;;
    esac
    _key="${_stmt%%=*}"
    _key="${_key#"${_key%%[![:space:]]*}"}"
    _key="${_key%"${_key##*[![:space:]]}"}"
    case "$_key" in
      ''|*[!A-Za-z0-9_]*|[0-9]*) continue ;;
    esac
    if eval "[ -n \"\${${_key}+set}\" ]"; then
      _env_keep+=("$(declare -p "$_key")")
    fi
  done < "$_env_file"
  # shellcheck disable=SC1090
  set +u; . "$_env_file"; set -u
  if [ ${#_env_keep[@]} -gt 0 ]; then
    for _stmt in "${_env_keep[@]}"; do eval "$_stmt"; done
  fi
  unset _line _stmt _key _env_keep
  echo "  → loaded version metadata from $_env_file"
else
  echo "  ! .build.env not found (looked in \$BUILD_ENV_FILE, <script-dir>/../, ./, /app/) — using hardcoded playwright revisions" >&2
fi

# Fallback playwright revisions (overridden by .build.env above).
# Update these (in .build.env) when hermes-agent bumps playwright.
PLAYWRIGHT_FALLBACK_CHROMIUM_REV="${PLAYWRIGHT_FALLBACK_CHROMIUM_REV:-1187}"
PLAYWRIGHT_FALLBACK_FFMPEG_REV="${PLAYWRIGHT_FALLBACK_FFMPEG_REV:-1013}"

# ── SSH for hermes-agent clone ──
# The Dockerfile mounts the install SSH key at
# /root/.ssh/id_ed25519_install (mode 0400, read-only). The install.sh RUN
# in the Dockerfile creates /root/.ssh/config with StrictHostKeyChecking and
# IdentityFile, but this warmup RUN does NOT touch that config — to keep
# concerns separated. So we set GIT_SSH_COMMAND inline to give git the
# same behavior: use the install key, accept the server's host key on
# first connect (TOFU), and don't try the user's other ssh-agent keys.
export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i /root/.ssh/id_ed25519_install"

# Sanity check: confirm the key file is actually mounted. Without this we
# get a generic git "access rights" error and can't tell whether the issue
# is a missing mount, a wrong key, or an auth problem on gitea's side.
if [ -r /root/.ssh/id_ed25519_install ]; then
  _key_size=$(stat -c %s /root/.ssh/id_ed25519_install 2>/dev/null || echo 0)
  _key_fp=$(ssh-keygen -lf /root/.ssh/id_ed25519_install 2>/dev/null | awk '{print $2}')
  echo "  → SSH key: /root/.ssh/id_ed25519_install (${_key_size}B, fingerprint=${_key_fp:-<unreadable>})"
else
  echo "  ! SSH key /root/.ssh/id_ed25519_install NOT mounted or unreadable" >&2
  echo "    (Dockerfile --mount=type=secret,id=git_identity_key may be missing or test-build script's --secret flag misconfigured)" >&2
  exit 0  # non-fatal; install.sh will still try
fi

# ── Platform detection ──
case "$(uname -m)" in
  x86_64)  _arch=x64 ;;
  aarch64) _arch=arm64 ;;
  *) echo "  ! pre-warm: unsupported arch $(uname -m); skipping" >&2; exit 0 ;;
esac

# ── --clean mode ──
if [ "$_MODE" = "clean" ]; then
  _size=0
  if [ -d "$WARMUP_DIR" ]; then
    _ws=$(du -sb "$WARMUP_DIR" 2>/dev/null | awk '{print $1}' || echo 0)
    _size=$((_size + _ws))
    rm -rf "$WARMUP_DIR"
  fi
  if [ -d "$ELECTRON_CACHE_DIR" ]; then
    _es=$(du -sb "$ELECTRON_CACHE_DIR" 2>/dev/null | awk '{print $1}' || echo 0)
    _size=$((_size + _es))
    rm -rf "$ELECTRON_CACHE_DIR"
  fi
  # NOTE: the playwright browsers cache (PLAYWRIGHT_BROWSERS_PATH, default
  # /root/.cache/ms-playwright) is intentionally NOT removed here. --clean
  # runs AFTER install.sh, and at that point the cache is no longer warmup
  # scratch — it is the only copy of chromium/ffmpeg in the image. install.sh
  # reuses the pre-warmed browsers (or downloads its own into the same path),
  # install-wrappers.sh hard-errors without a chrome binary under
  # /root/.cache/ms-playwright, and the runtime stage deliberately preserves
  # that directory. Deleting it would ship an image with no browser.
  _human=$(numfmt --to=iec "$_size" 2>/dev/null || echo "${_size}B")
  echo "  ✓ cleaned pre-warm artifacts (freed ${_human})"
  exit 0
fi

# ── Pre-warm mode ──
# Refuse to do anything without the required env vars (but non-fatal).
if [ -z "$HERMES_AGENT_REPO_URL" ] || [ -z "$HERMES_AGENT_COMMIT" ]; then
  echo "  ⊘ HERMES_AGENT_REPO_URL or HERMES_AGENT_COMMIT unset; skipping pre-warm"
  exit 0
fi

# Verify git is available.
command -v git >/dev/null 2>&1 || { echo "  ! git not available; skipping pre-warm" >&2; exit 0; }

# Step 1: Clone (or update existing) hermes-agent.
# Note on shallow clones: `git clone --depth 1` only fetches the tip of the
# default branch. The pinned commit (HERMES_AGENT_COMMIT) may be far from
# HEAD, so we follow up with `git fetch origin $SHA` to ensure the SHA is
# present in the local repo before checking out. Without this, `git checkout
# $SHA` fails with "not in local repo" and the warmup silently exits 0,
# leaving install.sh to fall through to the curl|bash path.
mkdir -p "$(dirname "$WARMUP_DIR")"
if [ -d "$WARMUP_DIR/.git" ]; then
  echo "  → updating existing clone at $WARMUP_DIR"
  git -C "$WARMUP_DIR" fetch --depth 1 origin "$HERMES_AGENT_COMMIT" 2>&1 | tail -3 || true
  git -C "$WARMUP_DIR" checkout "$HERMES_AGENT_COMMIT" 2>&1 | tail -3 || true
else
  echo "  → cloning $HERMES_AGENT_REPO_URL @ $HERMES_AGENT_COMMIT → $WARMUP_DIR"
  if ! git clone --no-checkout "$HERMES_AGENT_REPO_URL" "$WARMUP_DIR" 2>&1 | tail -20; then
    echo "  ! clone failed; skipping pre-warm (install will fall back to remote)" >&2
    exit 0
  fi
  # Fetch the pinned commit explicitly. This handles the case where the SHA
  # is not reachable from the default branch (which is what shallow clone
  # gets). Falls back gracefully if the SHA doesn't exist on the remote.
  git -C "$WARMUP_DIR" fetch --depth 1 origin "$HERMES_AGENT_COMMIT" 2>&1 | tail -3 || \
    { echo "  ! fetch $HERMES_AGENT_COMMIT failed; skipping pre-warm" >&2; exit 0; }
  if ! git -C "$WARMUP_DIR" checkout "$HERMES_AGENT_COMMIT" 2>&1 | tail -3; then
    echo "  ! checkout $HERMES_AGENT_COMMIT failed; skipping pre-warm" >&2
    exit 0
  fi
fi

# Step 2: Discover electron versions across all package.json files.
# Naive but sufficient: any line matching "electron" : "<ver>" extracts <ver>.
# Handles exact versions, semver ranges, and workspace paths uniformly.
echo "  → discovering electron versions..."
_versions=$(find "$WARMUP_DIR" -name "package.json" -not -path "*/node_modules/*" 2>/dev/null | \
  while read pkg; do
    grep -E '"electron"[[:space:]]*:[[:space:]]*"[^"]+"' "$pkg" 2>/dev/null | \
      sed -E 's/.*"electron"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' || true
  done | sort -u | grep -v '^$' || true)

if [ -z "$_versions" ]; then
  echo "  ⊘ no electron versions found in hermes-agent workspaces; skipping pre-fetch"
  exit 0
fi

echo "  → discovered electron versions: $_versions"

# Step 3: Pre-fetch zip + sha256sum for each version.
# Same retry pattern as Dockerfile:212 and install.sh:797 (covers
# SSL_ERROR_SYSCALL from transparent proxies). When electron's install.js
# later finds both files in $ELECTRON_CACHE_DIR, it skips the network.
#
# 4xx short-circuit: if the server returns 404/410/403 (resource doesn't
# exist), retrying won't help. Skip remaining retries immediately to
# avoid 6× "curl: (22)" noise per missing file (e.g. electron's
# .sha256sum and playwright's browsers.json are not mirrored at
# registry.npmmirror.com, so every build would otherwise log them as
# 6× 404). 5xx / network errors still get full retry+backoff.
mkdir -p "$ELECTRON_CACHE_DIR"

fetch_one() {
  local url=$1 dest=$2 min_size=${3:-1000000}
  if [ -f "$dest" ] && [ "$(stat -c %s "$dest" 2>/dev/null || echo 0)" -gt "$min_size" ]; then
    return 0
  fi
  local tmp="${dest}.part"
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if curl -fsSL --connect-timeout 15 --max-time 600 -o "$tmp" "$url"; then
      local size; size=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
      if [ "$size" -lt "$min_size" ]; then
        rm -f "$tmp"
        return 1
      fi
      mv "$tmp" "$dest"
      return 0
    fi
    # -f made curl exit 22 on 4xx/5xx. Probe the status code without -f.
    local code
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || echo "000")
    case "$code" in
      404|410|403) rm -f "$tmp"; return 1 ;;  # resource gone — don't retry
    esac
    # 5xx / network error — back off and retry
    if [ "$attempt" -lt 6 ]; then
      sleep $((attempt * 2))  # 2, 4, 6, 8, 10s
    fi
  done
  rm -f "$tmp"
  return 1
}

for ver in $_versions; do
  _archive="electron-v${ver}-linux-${_arch}.zip"
  _dest="$ELECTRON_CACHE_DIR/$_archive"

  if fetch_one "${ELECTRON_MIRROR%/}/v${ver}/$_archive" "$_dest" 1000000; then
    _size=$(stat -c %s "$_dest" 2>/dev/null || echo "?")
    _human=$(numfmt --to=iec "$_size" 2>/dev/null || echo "${_size}B")
    echo "  ✓ electron ${ver} zip (${_human})"
  else
    echo "  ! failed to pre-fetch electron ${ver} zip (non-fatal)" >&2
    continue
  fi

  # sha256sum is small (~80 bytes). electron's install.js downloads this
  # separately to verify the zip — having it pre-cached prevents that
  # secondary network round-trip (which historically also hangs from China).
  #
  # Fallback: many mirrors (incl. registry.npmmirror.com as of 2026-10-08)
  # do NOT mirror the .sha256sum sidecar. In that case, compute it locally
  # from the zip we just downloaded. This is TOFU: we trust the mirror's
  # zip bytes, but future builds verify the cached zip against this
  # sha256sum. Catches tampering with the cached file, not mirror
  # corruption on first download (same trust level as before).
  _sha_dest="${_dest}.sha256sum"
  if fetch_one "${ELECTRON_MIRROR%/}/v${ver}/${_archive}.sha256sum" "$_sha_dest" 10; then
    echo "  ✓ electron ${ver} sha256sum"
  elif [ -f "$_dest" ] && command -v sha256sum >/dev/null 2>&1; then
    # Format must match what @electron/get parses in its cache check:
    #   `readFile(checksumPath, 'utf8').trim()` then compared to the
    #   computed zip SHA256. So the file must contain ONLY the bare hash
    #   (64 hex chars), NOT `sha256sum`'s default `<hash>  <filename>` form —
    #   trimming leaves the filename suffix, which fails the comparison and
    #   forces install.js to redownload (verified 2026-10-09: the build was
    #   hanging on a 5+ minute download through the proxy's 302 redirect
    #   because our pre-warmed cache was being skipped due to format
    #   mismatch). `cut -d' ' -f1` extracts just the hash column.
    sha256sum "$_dest" | cut -d' ' -f1 > "$_sha_dest"
    echo "  ✓ electron ${ver} sha256sum (locally generated, bare-hash format)"
  else
    echo "  ! failed to pre-fetch electron ${ver} sha256sum (non-fatal)" >&2
  fi
done

# ── Pre-warm playwright browsers (chromium, ffmpeg) ───────────────────────
# Same problem as electron: hermes-agent's postinstall calls
# `npx playwright install` which downloads ~150MB of chromium + ~5MB of
# ffmpeg from the mirror. playwright-core has a HARDCODED 30s per-attempt
# timeout in coreBundle.js that times out from China even with mirror
# URLs. Result: 3 retries, 90s wasted, build fails with
# "Failed to download FFmpeg (playwright ffmpeg v1013)".
#
# Fix: pre-populate $PLAYWRIGHT_BROWSERS_PATH (default
# /root/.cache/ms-playwright) with the exact directories playwright-core
# expects, so the postinstall finds them locally and skips the network.
# Discover revisions dynamically from the mirror's browsers.json so the
# version is correct for whatever playwright version hermes-agent ships.
#
# Cache layout (matches playwright-core's expected paths):
#   ${PLAYWRIGHT_BROWSERS_PATH}/chromium-${REV}/chrome-linux/chrome
#   ${PLAYWRIGHT_BROWSERS_PATH}/ffmpeg-${REV}/ffmpeg-linux/ffmpeg-linux
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/root/.cache/ms-playwright}"

# prewarm_playwright_browser <name> <rev> <zip_name> <extract_dir> <binary_name>
# Downloads ${MIRROR}/builds/<name>/<rev>/<zip_name>, extracts into
# $PLAYWRIGHT_BROWSERS_PATH/<name>-<rev>/<extract_dir>/, idempotent on the
# extracted binary existing.
prewarm_playwright_browser() {
  local name=$1 rev=$2 zip_name=$3 extract_dir=$4 binary_name=$5
  local dest="$PLAYWRIGHT_BROWSERS_PATH/${name}-${rev}/${extract_dir}"
  local binary="$dest/$binary_name"

  # Idempotent: skip if extracted binary exists
  if [ -x "$binary" ]; then
    echo "  ✓ $name $rev already extracted"
    return 0
  fi

  echo "  → pre-warming $name $rev ($zip_name)..."
  local zip_url="${PLAYWRIGHT_DOWNLOAD_HOST}/builds/${name}/${rev}/${zip_name}"
  local zip_tmp="/tmp/${name}-${rev}-${zip_name}"
  # min_size: chromium ~150MB, ffmpeg ~5MB. Floor at 1MB to reject error pages.
  if ! fetch_one "$zip_url" "$zip_tmp" 1000000; then
    echo "  ! failed to pre-fetch $name $rev (non-fatal)" >&2
    rm -f "$zip_tmp"
    return 0
  fi

  if command -v unzip >/dev/null 2>&1; then
    mkdir -p "$dest"
    if ! unzip -q "$zip_tmp" -d "$dest" 2>/dev/null; then
      echo "  ! failed to extract $name $rev (non-fatal)" >&2
      rm -f "$zip_tmp"
      return 0
    fi
    rm -f "$zip_tmp"
    _size=$(du -sb "$dest" 2>/dev/null | awk '{print $1}' || echo 0)
    _human=$(numfmt --to=iec "$_size" 2>/dev/null || echo "${_size}B")
    echo "  ✓ $name $rev pre-warmed (${_human})"
  else
    echo "  ! unzip not available; $name $rev zip left at $zip_tmp" >&2
    return 0
  fi
}

# Discover revisions from the mirror's browsers.json. Format:
#   {"browsers":[{"name":"chromium","revision":"1187",...},{"name":"ffmpeg","revision":"1013",...}]}
# jq is the cleanest parser; fall back to grep if jq is missing.
discover_playwright_revisions() {
  local json_file="/tmp/browsers-${RANDOM}.json"
  if ! fetch_one "${PLAYWRIGHT_DOWNLOAD_HOST}/builds/browsers.json" "$json_file" 100; then
    rm -f "$json_file"
    return 1
  fi
  if command -v jq >/dev/null 2>&1; then
    eval "$(jq -r '
      (.browsers[] | select(.name == "chromium") | .revision) as $c |
      (.browsers[] | select(.name == "ffmpeg")    | .revision) as $f |
      "PLAYWRIGHT_CHROMIUM_REV=\($c // "");
      "PLAYWRIGHT_FFMPEG_REV=\($f // "")"' "$json_file" 2>/dev/null)"
  else
    # Fallback regex (handles both single-line and pretty-printed JSON)
    PLAYWRIGHT_CHROMIUM_REV=$(grep -oE '"name":[[:space:]]*"chromium"[^}]*"revision":[[:space:]]*"[0-9]+"' "$json_file" | grep -oE '"revision":[[:space:]]*"[0-9]+"' | grep -oE '[0-9]+' | head -1)
    PLAYWRIGHT_FFMPEG_REV=$(grep -oE '"name":[[:space:]]*"ffmpeg"[^}]*"revision":[[:space:]]*"[0-9]+"' "$json_file" | grep -oE '"revision":[[:space:]]*"[0-9]+"' | grep -oE '[0-9]+' | head -1)
  fi
  rm -f "$json_file"
}

if [ -n "$PLAYWRIGHT_DOWNLOAD_HOST" ]; then
  echo "  → discovering playwright browser versions..."
  PLAYWRIGHT_CHROMIUM_REV=""
  PLAYWRIGHT_FFMPEG_REV=""
  if discover_playwright_revisions; then
    echo "  → discovered: chromium=${PLAYWRIGHT_CHROMIUM_REV:-<none>} ffmpeg=${PLAYWRIGHT_FFMPEG_REV:-<none>}"
  else
    PLAYWRIGHT_CHROMIUM_REV="$PLAYWRIGHT_FALLBACK_CHROMIUM_REV"
    PLAYWRIGHT_FFMPEG_REV="$PLAYWRIGHT_FALLBACK_FFMPEG_REV"
    echo "  ! browsers.json not fetchable; using hardcoded fallbacks chromium=$PLAYWRIGHT_CHROMIUM_REV ffmpeg=$PLAYWRIGHT_FFMPEG_REV"
  fi
  [ -n "$PLAYWRIGHT_CHROMIUM_REV" ] && prewarm_playwright_browser "chromium" "$PLAYWRIGHT_CHROMIUM_REV" "chromium-linux.zip" "chrome-linux" "chrome"
  [ -n "$PLAYWRIGHT_FFMPEG_REV"    ] && prewarm_playwright_browser "ffmpeg"    "$PLAYWRIGHT_FFMPEG_REV"    "ffmpeg-linux.zip"    "ffmpeg-linux" "ffmpeg-linux"
else
  echo "  ⊘ PLAYWRIGHT_DOWNLOAD_HOST unset; skipping playwright pre-warm"
fi

echo "  → pre-warm complete"
