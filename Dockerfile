# syntax=docker/dockerfile:1.7
# ============================================================================
# LeapAide — multi-stage image, builds with both docker buildx and buildah.
#
# Targets:
#   * testing           — test + build image. No ENTRYPOINT/CMD — caller
#                         (Tekton, scripts/run-test.sh, scripts/build.sh)
#                         drives the command. Provides pytest + Node +
#                         frontend deps + scripts/run-test.sh +
#                         scripts/build.sh.
#   * runtime (default) — production image, trimmed FROM testing to drop
#                         dev/test artifacts. ENTRYPOINT=[/leap-aide-entrypoint.sh].
#
# Build contexts:
#   docker buildx build --target runtime -t leap-aide:runtime -f Dockerfile .
#   docker buildx build --target testing -t leap-aide:testing -f Dockerfile .
#   buildah bud --target runtime --layers -t leap-aide:runtime -f Dockerfile .
#   buildah bud --target testing --layers -t leap-aide:testing -f Dockerfile .
#
# Every target shares one context filter, .dockerignore at the repo root —
# do not pass --ignore-file/--ignorefile.
#
# Why these engines: docker buildx is the most battle-tested path for multi-arch
# GHA builds (release.yml uses it). buildah (Step 7 of the plan) is used by CI
# for the ci target so we cross-validate Dockerfile portability.
#
# Portability constraints (avoid BuildKit-only features):
#   - No heredoc RUN. No RUN --mount=type=cache|ssh|bind. No --output.
#   - No RUN --mount=type=secret: EXEMPTED. Required for SSH credential
#     injection during the hermes-agent installer clone (host key + identity
#     key). Supported by docker buildx 23+ (Feb 2023) and buildah 1.24+
#     (Sep 2021); podman inherits buildah. The security trade-off (zero
#     layer residue vs an extra BuildKit dependency) is justified by the
#     fact that the testing image itself is open-source and we cannot leak key
#     bytes into any published layer. See README "Builder requirements".
#   - Standard COPY/COPY --chown/COPY --chmod (Dockerfile 1.6+, both engines).
#   - Multi-arch via caller --platform; buildah also needs QEMU+binfmt.
# ============================================================================

# ---- base: Ubuntu 24.04 + Python 3.12 + leap-aide users + uv ----------------
FROM ubuntu:24.04 AS base

LABEL maintainer="yinfangxi"
LABEL description="LeapAide — browser interface for Hermes Agent"

ENV DEBIAN_FRONTEND=noninteractive

# Optional proxy (build-arg controlled). Empty = direct connect.
# These are the standard HTTP_PROXY / HTTPS_PROXY / NO_PROXY env vars; npm,
# curl, uv, pip, hermes-agent installer etc. all read them. Set via
#   --build-arg HTTP_PROXY=http://proxy.example.com:8080
# or pass via host's http_proxy (BuildKit auto-forwards it).
# scripts/build.sh feeds these from BUILD_HTTP_PROXY/BUILD_HTTPS_PROXY/
# BUILD_NO_PROXY and blocks host-env injection (buildah --http-proxy=false),
# so a host proxy never leaks into the build by accident.
#
# ARG, NOT ENV — deliberately. An ARG is injected into this stage's RUN
# environments but never written into the image config, so the finished image
# carries no HTTP_PROXY keys at all. The old `ENV HTTP_PROXY=` blanking in the
# runtime stage could only empty the VALUE — the key stayed in the image Env
# list (visible in `docker exec env`) because Dockerfile has no unset.
# ARG scope does not cross FROM: app/testing re-declare theirs.
# All six variants are forwarded explicitly by scripts/build.sh (some tools
# only read the lowercase names); declare all six here so RUN sees either case.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

# Optional mirror sources (build-arg controlled). Empty = use upstream.
#   BUILD_APT_MIRROR        APT source root, e.g. http://mirrors.tuna.tsinghua.edu.cn/ubuntu
#                                                    (use https only if the registry cert is trusted)
#   BUILD_PIP_INDEX_URL     pip/uv index,   e.g. https://pypi.tuna.tsinghua.edu.cn/simple
#   BUILD_UV_PYTHON_MIRROR  uv Python dl,   e.g. https://mirrors.tuna.tsinghua.edu.cn/python
ARG BUILD_APT_MIRROR=
ARG BUILD_PIP_INDEX_URL=
ARG BUILD_UV_PYTHON_MIRROR=

# Forward to runtime ENV so uv picks them up. uv honors UV_INDEX_URL
# (pip-equivalent index) and UV_PYTHON_INSTALL_MIRROR (Python download).
# APT mirror must be applied with a separate RUN below (uv has no APT equivalent).
# UV_BREAK_SYSTEM_PACKAGES lets `uv pip install --system` work on Debian's
# PEP 668 externally-managed Python (Ubuntu 24.04 default).
ENV UV_INDEX_URL=${BUILD_PIP_INDEX_URL} \
    PIP_INDEX_URL=${BUILD_PIP_INDEX_URL} \
    UV_PYTHON_INSTALL_MIRROR=${BUILD_UV_PYTHON_MIRROR} \
    UV_BREAK_SYSTEM_PACKAGES=1

# Apply APT mirror if requested. Writes DEB822 (Ubuntu 24.04+) or legacy
# sources.list. Must run BEFORE the first apt-get update in this stage.
RUN if [ -n "${BUILD_APT_MIRROR}" ]; then \
        set -e; \
        _codename=$(. /etc/os-release && echo "${VERSION_CODENAME}"); \
        if [ -z "$_codename" ]; then \
            echo "ERROR: cannot detect VERSION_CODENAME for APT mirror"; \
            exit 1; \
        fi; \
        if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then \
            cp /etc/apt/sources.list.d/ubuntu.sources \
               /etc/apt/sources.list.d/ubuntu.sources.bak; \
            printf 'Types: deb\nURIs: %s\nSuites: %s %s-updates %s-backports\nComponents: main restricted universe multiverse\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\nTypes: deb\nURIs: %s\nSuites: %s-security\nComponents: main restricted universe multiverse\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
                "${BUILD_APT_MIRROR}" "$_codename" "$_codename" "$_codename" \
                "${BUILD_APT_MIRROR}" "$_codename" \
                > /etc/apt/sources.list.d/ubuntu.sources; \
        elif [ -f /etc/apt/sources.list ]; then \
            cp /etc/apt/sources.list /etc/apt/sources.list.bak; \
            printf 'deb %s %s main restricted universe multiverse\ndeb %s %s-updates main restricted universe multiverse\ndeb %s %s-backports main restricted universe multiverse\ndeb %s %s-security main restricted universe multiverse\n' \
                "${BUILD_APT_MIRROR}" "$_codename" \
                "${BUILD_APT_MIRROR}" "$_codename" \
                "${BUILD_APT_MIRROR}" "$_codename" \
                "${BUILD_APT_MIRROR}" "$_codename" \
                > /etc/apt/sources.list; \
        else \
            echo "ERROR: no APT sources file found"; \
            exit 1; \
        fi; \
        echo "APT mirror applied: ${BUILD_APT_MIRROR} (codename=${_codename})"; \
    fi

# Make use of apt-cacher-ng if available (both engines honor RUN chaining identically)
RUN if [ "A${BUILD_APT_PROXY:-}" != "A" ]; then \
        echo "Using APT proxy: ${BUILD_APT_PROXY}"; \
        printf 'Acquire::http::Proxy "%s";\n' "$BUILD_APT_PROXY" > /etc/apt/apt.conf.d/01proxy; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Match scripts/install.sh's IN_DOCKER dependency list (line 526-609):
#   - base: apt-utils locales sudo curl rsync openssh-client
#   - python: python3.12 python3.12-venv python3.12-dev
#   - native build tools: libatomic1 xz-utils build-essential
#   - X11/VNC observation: xvfb x11vnc openbox wmctrl xterm
#   - Chinese input: fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3
#                    fcitx5-frontend-gtk4 dbus-x11 python3-dbus
#
# Note: these intentionally overlap with install.sh's system-deps section.
# install.sh is idempotent (checks `command -v X` before installing) so the
# testing stage's later `bash install.sh --deps-only` will skip them. We keep
# both because:
#   - runtime stage (FROM builder) needs these deps and never runs install.sh
#   - install.sh needs to work standalone on hosts without this Dockerfile
# `git` is needed by scripts/npm_warmup.sh (which clones hermes-agent via
# SSH BEFORE install.sh runs). Without it pre-installed here, warmup
# silently skips and install.sh ends up doing the slow work uncached.
RUN apt-get update -y --fix-missing --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        apt-utils locales sudo curl rsync openssh-client git \
        python3.12 python3.12-venv python3.12-dev \
        libatomic1 xz-utils build-essential \
        xvfb x11vnc openbox wmctrl xterm \
        fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 \
        dbus-x11 python3-dbus \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 LTS is NOT installed here — install.sh handles it via
# NodeSource in its system-deps section (idempotent, see install.sh
# line 574-590). Keeping it there keeps the canonical-installer promise.

# UTF-8 + Chinese locale
RUN localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 \
    && sed -i 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen zh_CN.UTF-8 \
    && update-locale LANG=en_US.utf8 LC_ALL=C
ENV LANG=en_US.utf8
ENV LC_ALL=C

# Python env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# Build directory used by all later stages; runtime will rsync this into /app.
WORKDIR /app

# Sudo without password (same as install.sh)
RUN echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Users: leap-aide runs the server (UID/GID dynamically adjusted at runtime);
# leap-aide-init is the intermediate UID-swap user (fixed UID/GID 1025).
RUN groupadd -g 1024 leap-aide \
    && groupadd -g 1025 leap-aide-init \
    && useradd -u 1024 -d /home/leap-aide -g leap-aide -s /bin/bash -m leap-aide \
    && usermod -G users leap-aide \
    && adduser leap-aide sudo \
    && useradd -u 1025 -d /home/leap-aide-init -g leap-aide-init -s /bin/bash -m leap-aide-init \
    && usermod -G users leap-aide-init \
    && adduser leap-aide-init sudo

USER root

# Pre-install uv system-wide so the container doesn't need internet access at
# runtime. Installing as root places uv in /usr/local/bin, available to all users.
# docker-entrypoint.sh will skip the download when uv is already on PATH.
#
# Defense in depth:
#   * `set -euo pipefail` — exit on any error, unset var, or pipeline failure.
#     The naive `curl ... | sh` form silently swallows curl errors (sh reads
#     EOF and exits 0), producing an image with no uv that breaks later
#     stages with cryptic "uv: command not found" failures.
#   * Download to /tmp/uv-install.sh first, then exec the file. This makes
#     the failure surface as "curl couldn't fetch" rather than "sh ran with
#     empty stdin" — easier to diagnose.
#   * `curl --retry 5 --retry-delay 10 --retry-all-errors` — covers the
#     SSL_ERROR_SYSCALL / connection reset cases seen in testbox builds
#     routed through an intercepting HTTP proxy (transparent TLS
#     interception occasionally fails the handshake).
#
# Why `/bin/bash -c` instead of just `RUN`:
#   * Default SHELL for RUN is /bin/sh (dash on Ubuntu 24.04), which doesn't
#     support `set -o pipefail` (bash-only option). Plain RUN fails with
#     "/bin/sh: 1: set: Illegal option -o pipefail".
#   * SHELL ["/bin/bash", "-c"] directive before this RUN is recognized by
#     buildah as a step but doesn't actually switch the executor for the
#     following RUN under --isolation=oci (verified on testbox). Explicit
#     /bin/bash invocation sidesteps both issues and works identically
#     across docker buildx and buildah.
RUN /bin/bash -c 'set -euo pipefail && \
    curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors \
        -o /tmp/uv-install.sh \
        https://astral.sh/uv/install.sh && \
    env UV_INSTALL_DIR=/usr/local/bin sh /tmp/uv-install.sh && \
    rm -f /tmp/uv-install.sh'

# Marker file used by install.sh is_docker() detection
RUN touch /.within_container

# Cleanup
RUN rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy \
    && apt-get clean

# ---- app: source + baked deps -------------------------------------------
FROM base AS app

# Build-time proxy (see base stage): ARG-only so RUN steps can reach the
# network without the keys ever landing in the image config. Re-declared here
# because ARG scope stops at FROM. Lowercase mirrors the uppercase values
# (build.sh forwards all six --build-args explicitly).
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

USER root

# Source tree (filtered by repo-root .dockerignore: .git, hidden dirs, node_modules, static/, .env*, ...)
COPY --chown=leap-aide-init:leap-aide-init . /app

# Bake the git version tag into the image so the settings badge works even
# when .git is not present (it is excluded by .dockerignore).
# CI passes: --build-arg HERMES_VERSION=$(git describe --tags --always)
ARG HERMES_VERSION=unknown
RUN echo "__version__ = '${HERMES_VERSION}'" > /app/backend/app/api/_version.py

# hermes-agent pin (commit SHA). See backend/scripts/hermes-agent.env for
# rationale and the silent-fallback caveat when the SHA isn't reachable from
# the default branch.
ARG HERMES_AGENT_COMMIT=
ENV HERMES_AGENT_COMMIT=${HERMES_AGENT_COMMIT}

# Install backend Python deps (system-wide, hermes-agent stays mounted at runtime)
RUN _trusted="--trusted-host pypi.org --trusted-host files.pythonhosted.org"; \
    case "${UV_INDEX_URL:-}" in \
        *tuna.tsinghua*) _trusted="$_trusted --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host files.tuna.tsinghua.edu.cn" ;; \
    esac; \
    uv pip install --system \
        -r /app/backend/requirements.txt \
        $_trusted \
    && uv pip install --system \
        "hindsight-client>=0.4.22" \
        $_trusted

# ---- testing: test + build image -----------------------------------------------
FROM app AS testing

# Build-time proxy (see base stage): ARG-only, re-declared at this FROM
# boundary so every network RUN here (install.sh, npm ci, npm_warmup) sees the
# proxy without persisting any HTTP_PROXY keys into the image config.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

# Optional mirrors (mirror of the BUILD_APT_MIRROR / BUILD_PIP_INDEX_URL /
# BUILD_UV_PYTHON_MIRROR pattern). Empty = upstream.
ARG BUILD_NPM_REGISTRY=
ARG BUILD_PLAYWRIGHT_DOWNLOAD_HOST=
# Persist npm registry mirror as ENV (both lower- and uppercase variants —
# some npm forks only check one). Without this, install.sh's
# `npm config set registry` only writes to /root/.npmrc; USER leap-aide-init
# has empty /home/leap-aide-init/.npmrc and no env var, so npm falls back
# to registry.npmjs.org for frontend ci + playwright installs.
#
# Also persist fetch timeout/retry as ENV so the values apply to ALL npm/npx
# calls in the image (install.sh's npm deps, npx playwright install, frontend
# npm ci) — not just the immediate `npm ci` invocation. Defaults: 5-min fetch-
# timeout × 2 retries is too lenient for flaky CDNs (npmmirror cache miss
# observed at ~43s/pack); 60s × 5 retries with 10–60s backoff surfaces
# transient failures quickly instead of stacking into minutes of total wait.
ENV npm_config_registry="${BUILD_NPM_REGISTRY}" \
    NPM_CONFIG_REGISTRY="${BUILD_NPM_REGISTRY}" \
    npm_config_fetch_timeout=60000 \
    npm_config_fetch_retries=5 \
    npm_config_fetch_retry_mintimeout=10000 \
    npm_config_fetch_retry_maxtimeout=60000

# electron / electron-builder postinstall downloads (electron's `node install.js`,
# electron-builder's `code-sign` helper) fetch binaries from GitHub releases by
# default. From China this routinely hangs/fails for the full ~150MB Electron
# zip. Mirror URLs are build-args (overridable from outside, same pattern as
# BUILD_NPM_REGISTRY / BUILD_PLAYWRIGHT_DOWNLOAD_HOST) so test-build-*.sh can
# set them per build. We set both the canonical env var (ELECTRON_MIRROR) and
# npm's auto-prefixed variant (npm_config_electron_mirror) to cover all
# electron versions and downstream forks.
ARG BUILD_ELECTRON_MIRROR=
ARG BUILD_ELECTRON_BUILDER_BINARIES_MIRROR=
ENV ELECTRON_MIRROR="${BUILD_ELECTRON_MIRROR}" \
    ELECTRON_BUILDER_BINARIES_MIRROR="${BUILD_ELECTRON_BUILDER_BINARIES_MIRROR}" \
    npm_config_electron_mirror="${BUILD_ELECTRON_MIRROR}" \
    npm_config_electron_builder_binaries_mirror="${BUILD_ELECTRON_BUILDER_BINARIES_MIRROR}"

# npm postinstall pre-warm toggle (scripts/npm_warmup.sh). OFF by default.
#   --build-arg BUILD_NPM_WARMUP=1   → run the warmup before install.sh
#   --build-arg BUILD_NPM_WARMUP=0   → skip entirely (default)
# Accepted truthy values: 1 / true / yes / on. Anything else = off.
#
# When ON, the warmup RUN below clones hermes-agent and pre-fetches the
# electron + playwright binaries into the caches the real `npm install`
# postinstall checks, so those ~300MB never touch the network (matters from
# China, where the GitHub / playwright CDNs hang or time out). When OFF the
# warmup RUN is a no-op echo and install.sh falls back to its own mirror
# download path — slower, but requires no git_identity_key secret and no SSH
# clone, so it works on any builder.
#
# The `npm_warmup.sh --clean` at the end of the install.sh RUN is gated on the
# same flag: running --clean without a warmup would only delete caches
# install.sh legitimately produced.
ARG BUILD_NPM_WARMUP=0

# Pre-warm scratch dir. Used by scripts/npm_warmup.sh to clone
# hermes-agent and curl pre-fetch electron binaries ahead of the install.sh
# RUN. The same path is read by install.sh's WARMUP_DIR detection so the
# warmup clone is also reused as the installer source (eliminates the
# `curl https://hermes-agent.nousresearch.com/install.sh` network call).
# The actual directory is removed at the end of the install.sh RUN via
# `npm_warmup.sh --clean`, so it doesn't bloat the image.
ENV WARMUP_DIR=/tmp/hermes-agent-warmup

# Custom hermes-agent git URL (e.g. private mirror / fork). Empty = upstream
# NousResearch/hermes-agent. Read by hermes-agent installer as
# HERMES_REPO_URL env var at startup; install.sh passes through via
# --hermes-repo-url flag for explicitness.
ARG HERMES_AGENT_REPO_URL=
ENV HERMES_AGENT_REPO_URL=${HERMES_AGENT_REPO_URL}

# Optional accelerator prefix for the hermes-agent installer fetch (prepended
# to the resolved raw.githubusercontent.com URL by scripts/install.sh). Empty =
# direct access. Site-specific — pass at build time, never hardcode:
#   --build-arg HERMES_INSTALLER_MIRROR="https://<gh-proxy>/"
ARG HERMES_INSTALLER_MIRROR=
ENV HERMES_INSTALLER_MIRROR=${HERMES_INSTALLER_MIRROR}

# HERMES_HOME is the hermes-agent DATA/state dir (config.yaml, .env, SOUL.md,
# sessions/, logs/, skills/, managed bin/) — NOT the code checkout. During the
# build it points at a SEED location the hermes-agent installer populates (via
# install.sh forwarding --hermes-home). Agent code always lands at
# /usr/local/lib/hermes-agent through the installer's root-Linux FHS layout,
# independent of this value (see resolve_install_layout in the hermes-agent
# installer). At runtime the entrypoint copies the seed into
# /home/leap-aide/.hermes (the compose volume) and the runtime stage re-points
# HERMES_HOME there. The seed path must NOT be the runtime path: the compose
# bind mount would shadow anything baked at /home/leap-aide/.hermes.
ARG HERMES_HOME=/var/lib/leap-aide/hermes-seed
ENV HERMES_HOME=${HERMES_HOME}

USER root

# install-wrappers.sh and chrome-wrapper.sh hardcode /root/.cache/ms-playwright
# (they were designed for a root-owned install). Now that install.sh runs
# as root (USER root, below), Chromium lands at /root/.cache/ms-playwright
# directly — no symlink needed. We do need chmod 755 /root though, so
# leap-aide-init (the user tests run as) can traverse /root to invoke
# chrome-wrapper / hermes-chrome / agent-browser.
RUN chmod 755 /root

# Re-COPY backend/tests/ on top of the /app/backend/tests/ already populated
# by the earlier COPY . /app, so the testing image always has tests/ even if
# .dockerignore ever grows a rule that would drop them. Path mirrors the repo
# layout (backend/tests/) so conftest.py's REPO_ROOT discovery resolves the
# server.py location correctly.
COPY --chown=leap-aide-init:leap-aide-init backend/tests /app/backend/tests

# ---- DEBUG: verify mirror + connectivity before install.sh runs ----
# Temporary debug block — confirms proxy + registry reachability. Once the
# build is stable, this block can be removed (or kept as a guard that fails
# the build loudly if the mirror goes down).
RUN echo "=== proxy + registry config ===" \
    && echo "HTTP_PROXY=${HTTP_PROXY:-<unset>}" \
    && echo "BUILD_NPM_REGISTRY=${BUILD_NPM_REGISTRY:-<unset>}" \
    && echo "NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY:-<unset>}" \
    && echo "npm_config_registry=${npm_config_registry:-<unset>}" \
    && echo "" \
    && echo "=== proxy reachable? (curl through proxy to npmjs.org) ===" \
    && (curl -sS --max-time 10 -o /dev/null \
         -w "HTTP %{http_code} in %{time_total}s\n" \
         https://registry.npmjs.org/ \
         || echo "FAIL: proxy can't reach npmjs.org") \
    && echo "" \
    && echo "=== mirror reachable? (curl to npmmirror via proxy) ===" \
    && (curl -sS --max-time 10 -o /dev/null \
         -w "HTTP %{http_code} in %{time_total}s\n" \
         https://registry.npmmirror.com/ \
         || echo "FAIL: proxy can't reach npmmirror") \
    && echo "" \
    && echo "=== mirror has playwright-core metadata? ===" \
    && (curl -sS --max-time 15 \
         https://registry.npmmirror.com/playwright-core \
         | head -c 800 \
         || echo "FAIL: mirror metadata not fetchable")

# ---- Delegate dependency install to scripts/install.sh --deps-only ----------
# install.sh --deps-only is the canonical installer for everything: apt-get
# system deps (nodejs, npm, git, locales, …), hermes-agent SDK + Node deps,
# Playwright Chromium + browser-use, leap-aide Python deps, command wrappers.
# We delegate to it instead of duplicating ~150 lines of install logic in
# the Dockerfile — any drift would silently break tests (e.g. a hermes-agent
# upgrade that needs a new system dep).
#
# Run as USER root so install.sh's apt-get and /usr/local/bin wrapper writes
# work. install.sh's npm() function adds --unsafe-perm when EUID==0 to bypass
# npm ≥7's root refusal (host usage is unaffected: --unsafe-perm is a no-op
# for non-root users).
#
# install.sh in --deps-only mode:
#   - SKIPS leap-aide self-install (clone/symlink) — provided by /app already
#   - RUNS apt-get system deps (nodejs, npm, git, locales, …)
#   - RUNS hermes-agent install (curl|bash installer, pinned to
#     HERMES_AGENT_COMMIT — see env file)
#   - RUNS Playwright Chromium download (separate from hermes-agent's bundled
#     playwright-core to avoid the Node v26 hang bug — matches install.sh:776)
#   - RUNS browser-use CLI install
#   - RUNS leap-aide Python deps (uv pip install -r /app/backend/requirements.txt)
#   - RUNS command wrappers (chrome-wrapper, agent-browser-wrapper)
# systemd is auto-skipped inside Docker (IN_DOCKER=true).
#
# Don't override HOME here — install.sh runs as USER root, and HOME only
# influences $HOME-relative fallbacks. Code placement is decided by the
# installer's root-Linux FHS layout (/usr/local/lib/hermes-agent, independent
# of HERMES_HOME); state lands in $HERMES_HOME (the seed dir). Setting
# HOME=/home/leap-aide-init would redirect $HOME/.local style paths (uv tools,
# caches) to leap-aide-init's home, which is not what we want.
# Pass --hermes-commit (preferred for stability — see
# backend/scripts/hermes-agent.env) and --hermes-repo-url (custom fork/mirror,
# empty = upstream NousResearch) to install.sh. Uses POSIX `set --` so the
# RUN works under /bin/sh (dash on Ubuntu) — Docker's default SHELL doesn't
# support bash arrays.
# ── install.sh via hermes-agent installer ─────────────────────────────────
# The hermes-agent installer (curl|bash black box, scripts/install.sh:797)
# clones $HERMES_REPO_URL when set to an internal SSH URL. To prevent layer
# leakage of SSH credentials, host key + identity key are passed via
# BuildKit --mount=type=secret and bound directly to /root/.ssh/*. These
# mountpoints exist only for this RUN's mount namespace — they do NOT
# persist into the layer's filesystem snapshot, so the resulting image
# cannot leak key bytes regardless of where it is pushed.
#
# required=false: when no --secret is passed (default upstream NousResearch
# HTTPS clone), the dst paths don't exist and the if-guard skips ssh setup
# entirely. The /root/.ssh parent dir is created unconditionally below;
# if both secrets are absent, the dir is empty after RUN — no leak.
#
# See Dockerfile:26-32 for the --mount=type=secret exemption rationale.
# Debug echo below (between the config-write block and the hermes-pin/-repo
# assembly) confirms the BuildKit secret mounts landed in the container
# before install.sh's git clone runs. ~50ms, no network. If the build later
# fails with "No RSA host key is known", the missing/mismatched entry here
# is the root cause; if everything below looks right, the failure is
# downstream (e.g. install.sh overwriting known_hosts). Safe to keep: output
# is non-sensitive (fingerprint + first line of known_hosts only).
#
# Pre-warm: clone hermes-agent and curl-fetch its postinstall binaries
# (currently just electron) into the cache that electron's `node install.js`
# checks. Doing this BEFORE install.sh runs means the real `npm install`'s
# electron postinstall finds the cache populated and skips the network
# round-trip entirely. The warmup script is non-fatal — any failure logs
# a warning and continues; install.sh then falls back to the existing
# curl https://hermes-agent.nousresearch.com/install.sh path.
#
# WARMUP_DIR is the same path install.sh's WARMUP_DIR detection checks for,
# so the warmup's clone is also reused as the installer source (avoids a
# second clone and a `curl https://hermes-agent.nousresearch.com/install.sh`
# network call from inside the container).
#
# Note: no known_hosts mount here. The git server's host
# key seems to rotate / have multiple keys not captured by ssh-keyscan, and
# StrictHostKeyChecking=accept-new (in install.sh's SSH config below)
# needs to WRITE the accepted key to known_hosts — which a read-only mount
# would prevent. Letting SSH create its own /root/.ssh/known_hosts in the
# writable container layer avoids the conflict.
# Gated on BUILD_NPM_WARMUP (default 0). When off, the whole step is a
# no-op echo — no clone, no cache pre-fetch, no secret mount needed.
# The verification block confirms the warmup populated electron's cache dir:
# the real npm install's electron postinstall looks there and skips the
# network round-trip when both files are present. Non-empty `ls` + non-zero
# size = warmup took effect; absence = install.sh will hit the mirror.
RUN --mount=type=secret,id=git_identity_key,dst=/root/.ssh/id_ed25519_install,mode=0400,required=false \
    case "${BUILD_NPM_WARMUP:-0}" in 1|true|yes|on) _warmup=1 ;; *) _warmup=0 ;; esac; \
    if [ "$_warmup" = "1" ]; then \
        bash /app/scripts/npm_warmup.sh; \
        echo "===DEBUG warmup cache state==="; \
        (ls -la /root/.cache/electron/ 2>/dev/null || echo "  (no electron cache dir)"); \
        (du -sb /root/.cache/electron/ 2>/dev/null || echo "  (du failed)"); \
        echo "===END DEBUG==="; \
    else \
        echo "=== npm_warmup skipped (BUILD_NPM_WARMUP=${BUILD_NPM_WARMUP:-0}; pass --build-arg BUILD_NPM_WARMUP=1 to enable) ==="; \
    fi

RUN --mount=type=secret,id=git_identity_key,dst=/root/.ssh/id_ed25519_install,mode=0400,required=false \
    export PLAYWRIGHT_DOWNLOAD_HOST="${BUILD_PLAYWRIGHT_DOWNLOAD_HOST}" \
    && mkdir -p -m 700 /root/.ssh \
    && [ -f /root/.ssh/id_ed25519_install ] && chmod 600 /root/.ssh/id_ed25519_install || true \
    && if [ -f /root/.ssh/id_ed25519_install ]; then \
         # StrictHostKeyChecking accept-new: trust the server's host key on
         # first connect and persist it to /root/.ssh/known_hosts. The
         # The git server's host key seems to rotate / have
         # multiple keys not captured by ssh-keyscan, so the strict
         # `yes` mode fails with "Host key verification failed". accept-new
         # is the TOFU (trust on first use) variant — first connect
         # accepts and stores, subsequent connects verify against the
         # stored key. Still safer than `no` (which would silently accept
         # any key, including MITM).
         printf 'Host *\n    StrictHostKeyChecking accept-new\n    IdentitiesOnly yes\n    IdentityFile /root/.ssh/id_ed25519_install\n' \
             > /root/.ssh/config; \
         chmod 600 /root/.ssh/config; \
       fi \
    && echo "===DEBUG ssh secret mount===" \
    && ls -la /root/.ssh/ \
    && (head -1 /root/.ssh/known_hosts 2>/dev/null | cut -c1-120 || echo "known_hosts: <missing>") \
    && (ssh-keygen -lf /root/.ssh/id_ed25519_install 2>/dev/null \
        || echo "id_ed25519_install: <missing or unreadable>") \
    && (cat /root/.ssh/config 2>/dev/null || echo "config: <missing>") \
    && echo "===END DEBUG===" \
    # Verify the critical env vars are in scope BEFORE invoking install.sh.
    # Previously install.sh had `DEBUG_INSTALL=false` as a literal default
    # which overrode the inherited env (verified 2026-10-09: the build.log
    # never showed "Debug logging enabled" until we switched to
    # `${DEBUG_INSTALL:-false}`). This echo catches the same class of bug
    # for any other env vars we set in this RUN.
    && echo "===DEBUG env vars before install.sh===" \
    && echo "DEBUG_INSTALL=${DEBUG_INSTALL:-<unset>}" \
    && echo "ELECTRON_MIRROR=${ELECTRON_MIRROR:-<unset>}" \
    && echo "PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST:-<unset>}" \
    && echo "NPM_REGISTRY=${NPM_REGISTRY:-<unset>}" \
    && echo "HERMES_AGENT_REPO_URL=${HERMES_AGENT_REPO_URL:-<unset>}" \
    && echo "HERMES_AGENT_COMMIT=${HERMES_AGENT_COMMIT:-<unset>}" \
    && echo "no_proxy=${no_proxy:-<unset>}" \
    && echo "NO_PROXY=${NO_PROXY:-<unset>}" \
    && echo "===END DEBUG===" \
    && _hermes_pin= \
    && _hermes_repo= \
    && if [ -n "${HERMES_AGENT_COMMIT}" ]; then \
         _hermes_pin="--hermes-commit ${HERMES_AGENT_COMMIT}"; \
       fi \
    && if [ -n "${HERMES_AGENT_REPO_URL}" ]; then \
         _hermes_repo="--hermes-repo-url ${HERMES_AGENT_REPO_URL}"; \
       fi \
    && set -- --deps-only --dir /app \
              --skip-build --non-interactive --china-mirror \
              --npm-registry "${BUILD_NPM_REGISTRY}" \
              ${_hermes_pin} ${_hermes_repo} \
    && export DEBUG_INSTALL=true \
    && bash /app/scripts/install.sh "$@" 2>&1 \
    # Post-install verification: did electron install.js hit the cache (good,
    # warmup worked) or did it download (warmup missed for this version)?
    # With DEBUG_INSTALL=true, @electron/get logs the URL it tried and
    # "Found locally: ..." on cache hit. Combined with the warmup ls above,
    # we can tell exactly which code path was taken.
    && echo "===DEBUG post-install cache state===" \
    && (ls -la /root/.cache/electron/ 2>/dev/null || echo "  (no electron cache dir)") \
    && (du -sb /root/.cache/electron/ 2>/dev/null || echo "  (du failed)") \
    && echo "===END DEBUG===" \
    # Clean up the warmup artifacts — only when the warmup actually ran
    # (BUILD_NPM_WARMUP, default off). WARMUP_DIR clone (~hundreds of MB of
    # hermes-agent source) and the pre-fetched electron zip (~150MB) are
    # no longer needed — install.sh has used the clone and electron's
    # install.js has extracted the zip. Removing them keeps the image lean.
    # When the warmup is off there is nothing of ours to clean, and running
    # --clean anyway would just delete caches install.sh legitimately made.
    && case "${BUILD_NPM_WARMUP:-0}" in 1|true|yes|on) bash /app/scripts/npm_warmup.sh --clean ;; esac \
    # install.sh's apt-get install leaves /var/lib/apt/lists/* in the layer
    # (~30-100MB of metadata for already-installed packages). Strip it — the
    # testing image doesn't need apt after this point and BuildKit will re-cache
    # if a later stage needs apt-get update.
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    || { echo "++ install.sh --deps-only failed"; exit 1; }

# Post-install layout assertions: code at the FHS default, no state pollution
# under /usr/local/lib, and the seed at $HERMES_HOME is complete. Fails the
# build loudly if the upstream installer layout drifts.
RUN set -e; \
    test -f /usr/local/lib/hermes-agent/run_agent.py; \
    test ! -e /usr/local/lib/.env && test ! -e /usr/local/lib/config.yaml \
      && test ! -d /usr/local/lib/sessions && test ! -d /usr/local/lib/logs \
      && test ! -d /usr/local/lib/memories && test ! -d /usr/local/lib/bin; \
    test -f "$HERMES_HOME/config.yaml" && test -f "$HERMES_HOME/.env" \
      && test -f "$HERMES_HOME/SOUL.md" && test -d "$HERMES_HOME/skills" \
      && test -x "$HERMES_HOME/bin/uv"; \
    python3 -c "import yaml; c=yaml.safe_load(open('$HERMES_HOME/config.yaml')); \
      assert c.get('display',{}).get('show_reasoning') is True, 'show_reasoning patch missing'"; \
    chmod 644 "$HERMES_HOME/.env"; \
    ls -la "$HERMES_HOME/bin"; \
    for _l in "$HERMES_HOME"/bin/*; do \
      [ -L "$_l" ] || continue; \
      _t=$(readlink -f "$_l") || { echo "dangling symlink: $_l"; exit 1; }; \
      [ -e "$_t" ] || { echo "dangling symlink: $_l -> $_t"; exit 1; }; \
    done; \
    echo "layout assertions OK"

# install.sh installs backend/requirements.txt (pyyaml) but NOT pytest —
# pytest is a test framework, not a runtime dep. Add it on top, system-wide.
USER root
RUN _trusted="--trusted-host pypi.org --trusted-host files.pythonhosted.org"; \
    case "${UV_INDEX_URL:-}" in \
        *tuna.tsinghua*) _trusted="$_trusted --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host files.tuna.tsinghua.edu.cn" ;; \
    esac; \
    uv pip install --system pytest pytest-timeout \
        $_trusted

# static/ is excluded from the build context (.dockerignore), so nothing
# copies it in — the frontend build below has to create it. That needs write
# permission on /app, and /app is root:root 755: it was created by
# `WORKDIR /app` (line ~171) running as root, and `COPY --chown` only
# chowns the entries it copies, not a destination directory that already
# exists. So `mkdir /app/static` from leap-aide-init fails with EACCES.
# Create it here, as root, and hand it over. (Before static/ was excluded
# this never showed up: the copied static/ entry was chowned correctly.)
RUN mkdir -p /app/static && chown leap-aide-init:leap-aide-init /app/static
USER leap-aide-init

# Frontend deps (vitest, vite, tsc, postcss, tailwindcss) for tests + static
# build. install.sh doesn't touch frontend. Drop to leap-aide-init since npm ≥7
# refuses root installs. Timeout/retry is set as testing-stage ENV (above) so it
# applies here AND to install.sh's npm/npx calls; no per-invocation flags needed.
RUN cd /app/frontend \
    && if [ -n "${BUILD_NPM_REGISTRY:-}" ]; then npm config set registry "${BUILD_NPM_REGISTRY}"; fi \
    && npm ci --no-audit --no-fund \
    && npm cache clean --force \
    && rm -rf node_modules/.cache

# Build the frontend once, bake the artifacts (static/) into the image.
# Doing this in testing (not runtime) because:
#   * testing already has node + npm + frontend/node_modules (from `npm ci`
#     above); runtime stage purges them (Dockerfile:521-545), so it can't
#     run `npm run build` on its own.
#   * runtime stage derives FROM testing, so /app/static/ is inherited
#     automatically. One bake, both images.
# Without this step, runtime's `COPY . /app` (Dockerfile:231) would pick
# up whatever stale static/ happens to be in the build context — empty on
# clean CI, stale on local devs — and the served frontend would 404 or
# load JS with hashes that no longer match index.html.
#
# Drop the vite cache + tsbuildinfo after build (rebuildable on demand);
# keep frontend/src/ — testing still runs vitest on it. Keep node_modules —
# runtime trims it later.
#
# static/ is excluded from the build context (.dockerignore), so a stale host
# copy never reaches the image — every file under it is regenerated here:
# vite emits assets/ + index.html, copies frontend/public/ out as logo/ +
# observer.html, and `postbuild` copies @novnc/novnc into novnc/.
# (git tracks nothing under static/ — the old "static/logo + observer.html are
# repo-tracked" note here was wrong.)
#
# The leading `rm -rf` is kept as a safety net: if a local build ever
# re-includes static/ in the context, its stale `static/assets/*.gz` (written
# by build.sh's gzip step) would otherwise be gzip-rejected on the new build
# (forcing gzip -f) or shadow the regenerated files.
RUN rm -rf /app/static/assets /app/static/index.html \
    && bash /app/scripts/build.sh --frontend \
    && rm -rf /app/frontend/.vite /app/frontend/.tsbuildinfo

# Defensive chmod: scripts are tracked with +x in git and COPY preserves the
# mode bit, but some fuse-overlayfs / BuildKit cache layers have been seen
# to drop +x silently. Cheap to re-apply here.
RUN chmod +x /app/scripts/*.sh

WORKDIR /app

# Intentionally NO ENTRYPOINT / CMD — this is a Tekton-friendly test +
# build environment, not a single-purpose runner. Tekton specifies
# `command` + `args` per step, and the image provides pytest + Node +
# frontend deps + hermes-agent SDK + Playwright Chromium + command wrappers
# + scripts/run-test.sh + scripts/build.sh. Common invocations:
#   command=["scripts/run-test.sh"]  args=["full"]              # backend pytest
#   command=["scripts/run-test.sh"]  args=["frontend", "full"]  # frontend vitest
#   command=["scripts/build.sh"]     args=["--frontend"]        # bake ../static/
#   command=["pytest"]               args=["backend/tests/", "-v"]
#   command=["bash", "-c"]           args=["cd frontend && npm run build"]

# ---- runtime: production image (default) — trimmed FROM testing ---------------
# Derived from testing so every released runtime image is a derivative of a
# testing build that has passed validation. Cleans dev/test artifacts that the
# server does not need at runtime (frontend deps, build toolchain, pytest).
FROM testing AS runtime

USER root

# Blank build-time-only env vars that testing stage inherited but runtime
# should not leak. Empty string is equivalent to unset for npm / curl / uv.
# Note: HTTP_PROXY / HTTPS_PROXY / NO_PROXY need no entry here — they are
# build-arg-scoped ARGs since the base stage, so the keys never enter the
# image config (adding `ENV HTTP_PROXY=` would just reintroduce empty keys).
ENV npm_config_registry= \
    NPM_CONFIG_REGISTRY= \
    npm_config_fetch_timeout= \
    npm_config_fetch_retries= \
    npm_config_fetch_retry_mintimeout= \
    npm_config_fetch_retry_maxtimeout= \
    BUILD_PLAYWRIGHT_DOWNLOAD_HOST= \
    ELECTRON_MIRROR= \
    ELECTRON_BUILDER_BINARIES_MIRROR= \
    npm_config_electron_mirror= \
    npm_config_electron_builder_binaries_mirror= \
    WARMUP_DIR=

# Preserve these from the testing stage — runtime still consumes them:
#   HERMES_AGENT_COMMIT  — warn-only consistency check in entrypoint
#   HERMES_AGENT_REPO_URL — forwarded by entrypoint scripts
#
# HERMES_HOME is NOT preserved: the testing stage points it at the build-time
# seed dir. Runtime re-points it at the state dir (the compose volume).
ENV HERMES_HOME=/home/leap-aide/.hermes

# ── Trim test framework ──────────────────────────────────────────────────────
# testing stage installs pytest+pytest-timeout for tests. Remove them from runtime.
RUN _trusted="--trusted-host pypi.org --trusted-host files.pythonhosted.org"; \
    case "${UV_INDEX_URL:-}" in \
        *tuna.tsinghua*) _trusted="$_trusted --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host files.tuna.tsinghua.edu.cn" ;; \
    esac; \
    uv pip uninstall --system pytest pytest-timeout $_trusted || true

# ── Trim apt build chain ─────────────────────────────────────────────────────
# testing base stage installs these for hermes-agent wheels + frontend deps.
# Runtime does not compile or run Node/Python dev tools.
RUN apt-get purge -y --auto-remove \
        build-essential python3.12-dev xz-utils libatomic1 \
    && apt-get remove -y --purge nodejs npm \
    && apt-get autoremove -y --purge \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /var/cache/apt/pkgcache.bin /var/cache/apt/srcpkgcache.bin

# ── Trim frontend build artifacts ───────────────────────────────────────────
# frontend/node_modules (~300MB+) was installed by testing for vitest+npm ci.
# Runtime only needs the prebuilt static/ assets — frontend source tree is not
# required.
# /app/.venv: stale second venv from older install flows. The single Python
# env is the agent venv (/usr/local/lib/hermes-agent/venv); if /app/.venv ever
# reappears at runtime that is a regression alarm (bootstrap should never need
# to create one).
RUN rm -rf /app/frontend/node_modules \
           /app/frontend/dist \
           /app/frontend/.vite \
           /app/frontend/.cache \
           /app/frontend/.tsbuildinfo \
           /app/.venv

# ── Trim npm caches ──────────────────────────────────────────────────────────
# Preserve /root/.npm/_npx/ — backend/scripts/agent-browser-wrapper.sh looks up
# agent-browser in this cache (line 90) as the primary path; only falls back to
# `npx` (now removed) if the lookup misses. Also preserve /home/leap-aide-init
# npm layout for consistency.
RUN rm -rf /root/.npm/_cacache /root/.npm/_logs /root/.npm/_temp /root/.npm/_update-notifier-last-checked \
           /root/.npm/_cacache-content \
           /home/leap-aide-init/.npm/_cacache /home/leap-aide-init/.npm/_logs /home/leap-aide-init/.npm/_temp \
           /usr/local/lib/node_modules /usr/local/include/node /usr/local/share/man/man1/node* /usr/local/share/doc/*node* \
           /root/.cache/node /root/.cache/npm /root/.cache/pip /root/.cache/uv /home/leap-aide-init/.cache/pip

# ── Trim build/test shell scripts ────────────────────────────────────────────
# testing stage keeps these for build+test convenience; runtime does not invoke
# them. Repo source remains unchanged (in scripts/), only the /app/scripts/
# copies are removed.
RUN rm -f /app/scripts/install.sh \
          /app/scripts/build.sh \
          /app/scripts/run-test.sh \
          /app/scripts/cmd-util.sh

# ── Defensive cleanup ────────────────────────────────────────────────────────
# Strip any SSH scaffolding testing stage left from secret mounts (always present
# because install.sh may clone hermes-agent via SSH even when secrets are
# absent). Also strip any stray archives or shell history outside ms-playwright
# (which the VNC observer still needs).
RUN rm -rf /root/.ssh /root/.bash_history /root/.lesshst /root/.viminfo /root/.wget-hsts /root/.gitconfig \
    && find / -xdev -maxdepth 4 -type f \( -name "*.zip" -o -name "*.tar.gz" -o -name "*.tgz" -o -name "*.tar.xz" \) \
        -not -path "/root/.cache/ms-playwright/*" \
        -delete 2>/dev/null || true

# Restore runtime user. testing stage ended at USER leap-aide-init; restoring here
# means the ENTRYPOINT bash runs as the right user.
USER leap-aide-init

COPY --chmod=555 backend/scripts/docker-entrypoint.sh /leap-aide-entrypoint.sh

# Default to binding all interfaces (required for container networking)
ENV HERMES_WEBUI_HOST=0.0.0.0 \
    HERMES_WEBUI_PORT=8787

EXPOSE 8787

ENTRYPOINT ["/leap-aide-entrypoint.sh"]
