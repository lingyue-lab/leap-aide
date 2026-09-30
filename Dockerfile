# syntax=docker/dockerfile:1.7
# ============================================================================
# Leap Aide — multi-stage image, builds with both docker buildx and buildah.
#
# Targets:
#   * runtime (default) — production image. CMD=[/leap-aide_init.bash]
#   * cicd              — test + build image. No ENTRYPOINT/CMD — caller
#                         (Tekton, scripts/run-test.sh, scripts/build.sh)
#                         drives the command. Provides pytest + Node +
#                         frontend deps + scripts/run-test.sh +
#                         scripts/build.sh.
#
# Build contexts:
#   docker buildx build --target runtime -t leap-aide:runtime -f Dockerfile .
#   docker buildx build --target cicd --ignore-file .dockerignore.cicd \
#                        -t leap-aide:cicd -f Dockerfile .
#   buildah bud --target runtime --layers -t leap-aide:runtime -f Dockerfile .
#   buildah bud --target cicd --layers --ignorefile .dockerignore.cicd \
#               -t leap-aide:cicd -f Dockerfile .
#
# Why these engines: docker buildx is the most battle-tested path for multi-arch
# GHA builds (release.yml uses it). buildah (Step 7 of the plan) is used by CI
# for the ci target so we cross-validate Dockerfile portability.
#
# Portability constraints (avoid BuildKit-only features):
#   - No heredoc RUN. No RUN --mount=type=cache|ssh|bind. No --output.
#   - Standard COPY/COPY --chown/COPY --chmod (Dockerfile 1.6+, both engines).
#   - Multi-arch via caller --platform; buildah also needs QEMU+binfmt.
# ============================================================================

# ---- base: Ubuntu 24.04 + Python 3.12 + leap-aide users + uv ----------------
FROM ubuntu:24.04 AS base

LABEL maintainer="yinfangxi"
LABEL description="Leap Aide — browser interface for Hermes Agent"

ENV DEBIAN_FRONTEND=noninteractive

# Optional proxy (build-arg controlled). Empty = direct connect.
# These are the standard HTTP_PROXY / HTTPS_PROXY / NO_PROXY env vars; npm,
# curl, uv, pip, hermes-agent installer etc. all read them. Set via
#   --build-arg HTTP_PROXY=http://proxy.example.com:8080
# or pass via host's http_proxy (BuildKit auto-forwards it).
# Lowercase variants are also exported since some tools only check those.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

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
# cicd stage's later `bash install.sh --deps-only` will skip them. We keep
# both because:
#   - runtime stage (FROM builder) needs these deps and never runs install.sh
#   - install.sh needs to work standalone on hosts without this Dockerfile
RUN apt-get update -y --fix-missing --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        apt-utils locales sudo curl rsync openssh-client \
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
WORKDIR /build

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
# docker-init.sh will skip the download when uv is already on PATH.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Marker file used by install.sh is_docker() detection
RUN touch /.within_container

# Cleanup
RUN rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy \
    && apt-get clean

# ---- app: source + baked deps -------------------------------------------
FROM base AS app

USER root

# Source tree (excludes .git, __pycache__, tests/, .env* via repo-root .dockerignore)
COPY --chown=leap-aide-init:leap-aide-init . /build

# Bake the git version tag into the image so the settings badge works even
# when .git is not present (it is excluded by .dockerignore).
# CI passes: --build-arg HERMES_VERSION=$(git describe --tags --always)
ARG HERMES_VERSION=unknown
RUN echo "__version__ = '${HERMES_VERSION}'" > /build/backend/app/api/_version.py

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
        -r /build/backend/requirements.txt \
        $_trusted \
    && uv pip install --system \
        "hindsight-client>=0.4.22" \
        $_trusted

# ---- runtime: production image (default) ------------------------------------
FROM app AS runtime

USER leap-aide-init

# Unset proxy env vars. The base stage declares HTTP_PROXY/HTTPS_PROXY/NO_PROXY
# (and lowercase variants) as ENV for build-time use (apt, curl, uv, npm
# inside Dockerfile RUNs). For runtime, we don't want the build proxy to
# leak into the running server — runtime deployments should configure their
# own proxy via the deployment environment if needed. Setting to empty
# string is equivalent to unset for all proxy-aware tools (curl, npm, uv,
# pip, etc.).
ENV HTTP_PROXY= \
    HTTPS_PROXY= \
    NO_PROXY= \
    http_proxy= \
    https_proxy= \
    no_proxy=

COPY --chmod=555 backend/scripts/docker-init.sh /leap-aide_init.bash

# Default to binding all interfaces (required for container networking)
ENV HERMES_WEBUI_HOST=0.0.0.0 \
    HERMES_WEBUI_PORT=8787

EXPOSE 8787

CMD ["/leap-aide_init.bash"]

# ---- cicd: test + build image -----------------------------------------------
FROM app AS cicd

# Optional mirrors (mirror of the BUILD_APT_MIRROR / BUILD_PIP_INDEX_URL /
# BUILD_UV_PYTHON_MIRROR pattern). Empty = upstream.
ARG BUILD_NPM_REGISTRY=
ARG BUILD_PLAYWRIGHT_DOWNLOAD_HOST=
# Persist npm registry mirror as ENV (both lower- and uppercase variants —
# some npm forks only check one). Without this, install.sh's
# `npm config set registry` only writes to /root/.npmrc; USER leap-aide-init
# has empty /home/leap-aide-init/.npmrc and no env var, so npm falls back
# to registry.npmjs.org for frontend ci + playwright installs.
ENV npm_config_registry="${BUILD_NPM_REGISTRY}" \
    NPM_CONFIG_REGISTRY="${BUILD_NPM_REGISTRY}"

# Custom hermes-agent git URL (e.g. private mirror / fork). Empty = upstream
# NousResearch/hermes-agent. Read by hermes-agent installer as
# HERMES_REPO_URL env var at startup; install.sh passes through via
# --hermes-repo-url flag for explicitness.
ARG HERMES_AGENT_REPO_URL=
ENV HERMES_AGENT_REPO_URL=${HERMES_AGENT_REPO_URL}

# Where hermes-agent installs to. Default /usr/local/lib/hermes-agent (the
# path documented in leap-aide's README). Override via --build-arg if you
# need it elsewhere (e.g. /opt/hermes-agent for a non-root install).
# install.sh reads HERMES_HOME env var and passes --hermes-home to installer.
ARG HERMES_HOME=/usr/local/lib
ENV HERMES_HOME=${HERMES_HOME}

USER root

# install-wrappers.sh and chrome-wrapper.sh hardcode /root/.cache/ms-playwright
# (they were designed for a root-owned install). Now that install.sh runs
# as root (USER root, below), Chromium lands at /root/.cache/ms-playwright
# directly — no symlink needed. We do need chmod 755 /root though, so
# leap-aide-init (the user tests run as) can traverse /root to invoke
# chrome-wrapper / hermes-chrome / agent-browser.
RUN chmod 755 /root

# Re-COPY backend/tests/ on top of the /build/backend/tests/ already populated
# by the earlier COPY . /build, so the cicd image always has tests/ regardless of
# whether the build context used the repo-root .dockerignore (which excludes
# tests/) or .dockerignore.cicd (which keeps it). Path mirrors the repo
# layout (backend/tests/) so conftest.py's REPO_ROOT discovery resolves the
# server.py location correctly.
COPY --chown=leap-aide-init:leap-aide-init backend/tests /build/backend/tests

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
#   - SKIPS leap-aide self-install (clone/symlink) — provided by /build already
#   - RUNS apt-get system deps (nodejs, npm, git, locales, …)
#   - RUNS hermes-agent install (curl|bash installer, pinned to
#     HERMES_AGENT_COMMIT — see env file)
#   - RUNS Playwright Chromium download (separate from hermes-agent's bundled
#     playwright-core to avoid the Node v26 hang bug — matches install.sh:776)
#   - RUNS browser-use CLI install
#   - RUNS leap-aide Python deps (uv pip install -r /build/backend/requirements.txt)
#   - RUNS command wrappers (chrome-wrapper, agent-browser-wrapper)
# systemd is auto-skipped inside Docker (IN_DOCKER=true).
#
# Don't override HOME here — install.sh runs as USER root, and the
# hermes-agent installer defaults to /root/.hermes (or /usr/local/lib when
# it detects it can write there). Setting HOME=/home/leap-aide-init would
# redirect the installer to ~/.hermes of leap-aide-init, which is not what
# we want — hermes-agent should land at /usr/local/lib/hermes-agent.
# Pass --hermes-commit (preferred for stability — see
# backend/scripts/hermes-agent.env) and --hermes-repo-url (custom fork/mirror,
# empty = upstream NousResearch) to install.sh. Uses POSIX `set --` so the
# RUN works under /bin/sh (dash on Ubuntu) — Docker's default SHELL doesn't
# support bash arrays.
RUN export PLAYWRIGHT_DOWNLOAD_HOST="${BUILD_PLAYWRIGHT_DOWNLOAD_HOST}" \
    && _hermes_pin= \
    && _hermes_repo= \
    && if [ -n "${HERMES_AGENT_COMMIT}" ]; then \
         _hermes_pin="--hermes-commit ${HERMES_AGENT_COMMIT}"; \
       fi \
    && if [ -n "${HERMES_AGENT_REPO_URL}" ]; then \
         _hermes_repo="--hermes-repo-url ${HERMES_AGENT_REPO_URL}"; \
       fi \
    && set -- --deps-only --dir /build \
              --skip-build --non-interactive --china-mirror \
              --npm-registry "${BUILD_NPM_REGISTRY}" \
              ${_hermes_pin} ${_hermes_repo} \
    && bash /build/scripts/install.sh "$@" 2>&1 \
    # install.sh's apt-get install leaves /var/lib/apt/lists/* in the layer
    # (~30-100MB of metadata for already-installed packages). Strip it — the
    # cicd image doesn't need apt after this point and BuildKit will re-cache
    # if a later stage needs apt-get update.
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    || { echo "++ install.sh --deps-only failed"; exit 1; }

# install.sh installs backend/requirements.txt (pyyaml) but NOT pytest —
# pytest is a test framework, not a runtime dep. Add it on top, system-wide.
USER root
RUN _trusted="--trusted-host pypi.org --trusted-host files.pythonhosted.org"; \
    case "${UV_INDEX_URL:-}" in \
        *tuna.tsinghua*) _trusted="$_trusted --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host files.tuna.tsinghua.edu.cn" ;; \
    esac; \
    uv pip install --system pytest pytest-timeout \
        $_trusted
USER leap-aide-init

# Frontend deps (vitest, vite, tsc, postcss, tailwindcss) for tests + static
# build. install.sh doesn't touch frontend. Drop to leap-aide-init since npm ≥7
# refuses root installs.
RUN cd /build/frontend \
    && if [ -n "${BUILD_NPM_REGISTRY:-}" ]; then npm config set registry "${BUILD_NPM_REGISTRY}"; fi \
    && npm ci --no-audit --no-fund \
    && npm cache clean --force \
    && rm -rf node_modules/.cache

# Defensive chmod: scripts are tracked with +x in git and COPY preserves the
# mode bit, but some fuse-overlayfs / BuildKit cache layers have been seen
# to drop +x silently. Cheap to re-apply here.
RUN chmod +x /build/scripts/*.sh

WORKDIR /build

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
