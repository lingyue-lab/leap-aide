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
#   * runtime (default) — production image. ENTRYPOINT=[/leap-aide-entrypoint.sh].
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
# GHA builds (build-cicd-builder-image.yml still uses it). buildah (Step 7 of the
# plan) is used by CI for the testing and runtime targets, so we cross-validate
# Dockerfile portability.
#
# ── Stage graph (issue #40 — shrink the published runtime image) ─────────────
#
#   ubuntu:24.04
#    └─ base        runtime OS only: uv, gh + the gh-token extension, the
#    │              leap-aide users, locales, ffmpeg, the Chromium shared
#    │              libraries, X11/fcitx/fonts. NO build chain, NO node.
#       ├─ toolchain  base + build-essential/python3.12-dev/xz-utils/libatomic1
#       │             + Node.js 22 (NodeSource). Build-time only.
#       │   └─ app      toolchain + COPY . /app + version badge + backend deps
#       │       ├─ sdk      app + the hermes-agent SDK (install.sh --deps-only)
#       │       │          + the prune below + the /usr/local/bin wrappers
#       │       │   └─ testing  sdk + backend/tests + pytest + frontend deps
#       │       └─ frontend app + frontend deps + vite build → /app/static
#       └─ runtime    base + COPY --from=app /app
#                    + COPY --from=frontend /app/static
#                    + COPY --from=sdk <the pruned SDK trees>
#
# The runtime stage starts from `base`, NOT from `testing`. That is the whole
# point of the graph: a child stage inherits every byte of its parent, so
# "trimming" a child only writes whiteouts — the bytes stay in the ancestor
# layers and the published image does not shrink. Measured on the pre-#40
# image: deleting the 184 MB frontend/node_modules from a child of `testing`
# produced a 2.56 kB layer. Copying the handful of paths the server actually
# reads out of the build stages keeps them out of the runtime's layer chain
# entirely, and the bytes that are never copied are never paid for.
#
# Concretely, the runtime keeps: the OS, the Chromium runtime libraries, the
# Hermes SDK *code + venv* (minus its .git / node_modules / Electron app /
# own test suite), the Playwright Chromium build, the browser-use tool venv,
# the cua-driver, the hermes state seed, and the prebuilt frontend.
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

# ── The runtime OS package set ───────────────────────────────────────────────
# Match scripts/install.sh's IN_DOCKER dependency list (grep for
# `_sys_deps_to_install` — the block moves, so no line numbers here):
#   - base: apt-utils locales sudo curl rsync openssh-client git gh
#   - python: python3.12 python3.12-venv python3.12-dev
#   - native build tools: libatomic1 xz-utils build-essential
#   - X11/VNC observation: xvfb x11vnc openbox wmctrl xterm
#   - Chinese input: fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3
#                    fcitx5-frontend-gtk4 dbus-x11 python3-dbus
#
# Note: these intentionally overlap with install.sh's system-deps section.
# install.sh is idempotent (checks `command -v X` before installing) so the
# sdk stage's later `bash install.sh --deps-only` will skip them. We keep
# both because:
#   - the runtime stage (FROM base) needs these deps and never runs install.sh
#   - install.sh needs to work standalone on hosts without this Dockerfile
# `git` is needed by scripts/npm_warmup.sh (which clones hermes-agent via
# SSH BEFORE install.sh runs). Without it pre-installed here, warmup
# silently skips and install.sh ends up doing the slow work uncached.
#
# `gh` (GitHub CLI) is not optional decoration — the Hermes Agent SDK shells
# out to it: cli.py runs `gh pr list` to detect merged PRs, and
# tools/skills_hub.py runs `gh auth token` to authenticate against the
# skills hub. Both die with "gh: command not found" in the image, which
# blocks every GitHub-related agent task (PR/issue triage, repo queries).
# It ships in Ubuntu's `universe` component, which the sources file above
# already enables, so it rides along with BUILD_APT_MIRROR and needs no
# extra apt repo, keyring or pinned tarball — the same reason every other
# tool here comes from apt. Version floor: gh 2.45 (noble-updates), which
# covers the pr/issue/repo/api/run command surface the SDK relies on.
#
# What is NOT installed here (issue #40) and why:
#
#   build-essential python3.12-dev xz-utils libatomic1
#       Build-time only. They live in the `toolchain` stage, which the
#       runtime does not inherit. (libatomic1/xz-utils cost ~0.5 MB and
#       were purged from the runtime image before #40 as well.)
#
#   fcitx5-frontend-gtk4, fcitx5-config-qt
#       Chrome is a GTK3 app, so the GTK4 input-method frontend is dead
#       weight; fcitx5-config-qt is only the GUI configurator, and the
#       desktop is headless (Xvfb + openbox + x11vnc). Both were removed
#       from the runtime image before #40 as well — but as a *child* of
#       testing, which only left whiteouts behind. Dropping them here
#       actually removes the bytes.
#
#   fonts-unifont, fonts-wqy-zenhei, fonts-urw-base35, fonts-freefont-ttf,
#   fonts-ipafont-gothic, fonts-tlwg-loma-otf
#       Playwright's `install-deps` font set. With --no-install-recommends
#       (below) fontconfig-config only *depends* on fonts-dejavu-core and
#       fonts-dejavu-mono; the rest are recommends and stay out. What we
#       keep is deliberate: fonts-wqy-microhei (CJK, the agent's own
#       requirement — install.sh checks for it), fonts-liberation (the
#       metric-compatible Arial/Helvetica/Times clone Chrome asks for) and
#       fonts-noto-color-emoji (Chrome's emoji rendering).
#
# What IS installed here that the SDK stage used to bring in:
#
#   The Chromium shared libraries
#       `npx playwright install-deps chromium` installs them, but that ran
#       in the SDK stage — a stage the runtime no longer inherits. Chrome
#       dies at startup without them, so they are listed explicitly here.
#       The list is Playwright 1.64's own ubuntu24.04-x64 chromium set
#       plus the pieces the desktop already needs (gtk3 via fcitx5, the
#       X11 client libs via xvfb/x11vnc). The sdk stage asserts with ldd
#       that the downloaded Chromium has no unresolved shared library —
#       so a future Playwright that needs one more lib fails the BUILD
#       instead of the browser.
#
#   ffmpeg
#       The SDK's installer adds ffmpeg "for TTS voice messages" — with
#       plain `apt-get install -y ffmpeg`, i.e. WITH recommends, which drags
#       in pocketsphinx-en-us (36 MB), intel-media-va-driver (13 MB),
#       humanity-icon-theme (20 MB) and the rest of the VA-driver stack
#       (~100 MB). Pre-installing ffmpeg here with --no-install-recommends
#       makes the SDK's installer skip it (it checks `command -v ffmpeg`),
#       so the TTS feature keeps working and the recommends never land.
RUN apt-get update -y --fix-missing --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        apt-utils locales sudo curl rsync openssh-client git gh \
        python3.12 python3.12-venv \
        xvfb x11vnc openbox wmctrl xterm \
        fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 \
        dbus-x11 python3-dbus \
        fonts-wqy-microhei fonts-liberation fonts-noto-color-emoji \
        ffmpeg \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
        libcairo2 libcairo-gobject2 libcups2t64 libdbus-1-3 libdrm2 libdrm-common \
        libfontconfig1 libfreetype6 libgbm1 libglib2.0-0t64 libnspr4 libnss3 \
        libpango-1.0-0 libpangocairo-1.0-0 \
        libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxkbcommon0 libxrandr2 libxrender1 libxshmfence1 libxtst6 \
        libvulkan1 xdg-utils \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

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

# ── Free the distro default account (issue #6) ──────────────────────────────
# ubuntu:24.04 (noble) ships 'ubuntu' at UID/GID 1000; 22.04 and earlier do
# not. Compose maps leap-aide to the host UID (1000 on most Linux hosts), and
# the entrypoint used `usermod -o`, so leap-aide was remapped onto an id that
# was already taken. getpwuid() resolves the FIRST /etc/passwd match, so the
# terminal prompt (`\u`), `whoami` and `ls -l` reported "ubuntu".
# Drop the distro default account to keep UID/GID 1000 free. `userdel` without
# -r avoids the "mail spool not found" error; the home dir is removed
# explicitly.
RUN if getent passwd ubuntu >/dev/null 2>&1; then userdel ubuntu; fi \
    && rm -rf /home/ubuntu \
    && if getent group ubuntu >/dev/null 2>&1; then groupdel ubuntu; fi \
    && echo "== UID/GID 1000 after removing the distro default account:" \
    && (getent passwd 1000 || echo "   (uid 1000 free)") \
    && (getent group 1000 || echo "   (gid 1000 free)")

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

# ── gh extension: gh-token ─────────────────────────────────────────────────
# `gh token generate|revoke|installations` mints short-lived GitHub App
# installation access tokens for REST/GraphQL calls — the App-scoped GitHub
# work a PAT cannot express (the token expires an hour after creation and
# carries only the App's configured permissions).
#
# Installed by hand, NOT via `gh extension install`: gh's root command runs
# an auth check that exits 4 ("run gh auth login") for every command when
# no token is present — `gh extension install` and `gh extension list`
# included — and the image build has no GitHub credentials. The layout
# below is the one gh's own binary-extension installer produces:
#
#     <data dir>/extensions/gh-token/gh-token      the executable
#     <data dir>/extensions/gh-token/manifest.yml  owner/name/host/tag/path
#
# gh then dispatches `gh token …` to that executable. Extension dispatch
# is NOT behind the auth check, so `gh token --version` works in the
# unauthenticated image (it is the smoke test below) — while the built-in
# commands (`gh pr`, `gh issue`, `gh api`, …) still need a login.
#
# Landed in EVERY runtime identity's data dir ON PURPOSE: gh keeps
# extensions in the *invoking user's* data dir (~/.local/share/gh/extensions),
# and which user that is depends on how the container was started — the
# server and the WebUI terminal run as `leap-aide`, the entrypoint's first
# phase runs as `leap-aide-init`, and root mode (WANTED_UID=0, the
# rootless-podman default) runs the whole server as root. An extension
# installed for one identity is invisible to the others. (gh resolves the
# data dir from XDG_DATA_HOME or HOME — GH_DATA_DIR is not a thing.)
#
# The release publishes one asset per GOOS-GOARCH pair, named exactly
# `linux-amd64`, `linux-arm64`, ... — the naming gh's binary-extension
# lookup expects. dpkg --print-architecture picks the right one, so the
# multi-arch build (amd64 + arm64) needs no arch switch here.
#
# The binary is downloaded ONCE and hard-linked into each identity's
# extension dir: gh's own layout requires the executable to live at
# <extensions>/<name>/<name>, and a hard link satisfies that without
# paying for three copies of a ~10 MB Go binary.
#
# The manifest is 0644 instead of gh's 0600: gh writes it 0600 because it
# installs into a single user's home, but here several identities read it
# and it holds nothing secret (owner/name/host/tag/path).
#
# Pinned to a release tag, like the hermes-agent commit pin above: an
# unpinned "latest" would silently change the image contents between two
# builds of the same commit. Bump GH_TOKEN_EXTENSION_VERSION to update.
#
# The download base is a build arg like every other external download here
# (BUILD_APT_MIRROR, BUILD_NPM_REGISTRY, BUILD_PLAYWRIGHT_DOWNLOAD_HOST, ...):
# on CN networks github.com is often unreachable rather than merely slow, and
# --retry cannot help with an unreachable host. Point
# BUILD_GH_TOKEN_DOWNLOAD_HOST at a mirror that serves the same
# /Link-/gh-token/releases/download/... paths. Default: github.com.
#
# Retry: even against github.com the asset is intermittently slow from CN
# networks. curl's --retry/--retry-all-errors covers the transient failures
# the same way the uv installer does.
ARG GH_TOKEN_EXTENSION_VERSION=v2.0.10
ARG BUILD_GH_TOKEN_DOWNLOAD_HOST=
RUN set -e; \
    _gh_token_host="${BUILD_GH_TOKEN_DOWNLOAD_HOST:-https://github.com}"; \
    case "$(dpkg --print-architecture)" in \
        amd64) _gh_token_asset="linux-amd64" ;; \
        arm64) _gh_token_asset="linux-arm64" ;; \
        i386)  _gh_token_asset="linux-386" ;; \
        armhf) _gh_token_asset="linux-arm" ;; \
        *) echo "ERROR: no gh-token release asset for $(dpkg --print-architecture)"; exit 1 ;; \
    esac; \
    _gh_token_bin=/usr/local/share/gh-extensions/gh-token; \
    mkdir -p "$(dirname "$_gh_token_bin")"; \
    curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors \
        -o "$_gh_token_bin" \
        "${_gh_token_host}/Link-/gh-token/releases/download/${GH_TOKEN_EXTENSION_VERSION}/${_gh_token_asset}"; \
    chmod 0755 "$_gh_token_bin"; \
    for _home in /home/leap-aide /home/leap-aide-init /root; do \
        _gh_token_dir="$_home/.local/share/gh/extensions/gh-token"; \
        mkdir -p "$_gh_token_dir"; \
        ln -f "$_gh_token_bin" "$_gh_token_dir/gh-token"; \
        printf 'owner: Link-\nname: gh-token\nhost: github.com\ntag: %s\nispinned: true\npath: %s\n' \
            "${GH_TOKEN_EXTENSION_VERSION}" "$_gh_token_dir/gh-token" \
            > "$_gh_token_dir/manifest.yml"; \
        chmod 0644 "$_gh_token_dir/manifest.yml"; \
    done; \
    su -s /bin/bash -l leap-aide -c 'gh token --version'; \
    su -s /bin/bash -l leap-aide-init -c 'gh token --version'; \
    su -s /bin/bash -l root -c 'gh token --version'

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

# ── Snapshot the base /usr/local/bin ────────────────────────────────────────
# The runtime stage starts from `base`, so it inherits exactly the binaries
# listed here (uv, uvx). Everything ELSE that ends up in /usr/local/bin
# during the build — the console scripts of the backend's Python deps (idna,
# pygmentize) and the command wrappers the hermes-agent installer drops
# (hermes, hermes-agent, hermes-acp, agent-browser) — is staged separately
# by the `sdk` stage and copied in explicitly.
#
# Copying the whole directory instead would write a second copy of the
# 37 MB uv binary into the runtime layer, and copying only a hand-written
# file list would silently miss whatever the installer adds next. Diffing
# against this snapshot does neither.
RUN mkdir -p /usr/local/share/leap-aide \
    && ls -1 /usr/local/bin | LC_ALL=C sort > /usr/local/share/leap-aide/bin-base.txt \
    && echo "=== base /usr/local/bin:" \
    && cat /usr/local/share/leap-aide/bin-base.txt

# Marker file used by install.sh is_docker() detection
RUN touch /.within_container

# ── Make /root traversable ──────────────────────────────────────────────────
# The runtime image carries root-owned state under /root: the Playwright
# Chromium (/root/.cache/ms-playwright), the browser-use tool venv
# (/root/.local/share/uv/tools) and the cua-driver (/root/.cua-driver).
# The server does NOT run as root — it runs as leap-aide (or
# leap-aide-init), and Ubuntu's /root is mode 700 by default, so without
# this the browser desktop cannot exec the Chromium binary at all.
RUN chmod 755 /root

# Cleanup
RUN rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy \
    && apt-get clean

# ---- toolchain: build chain + Node.js (build-time only) ---------------------
# Everything the build stages need and the runtime must NOT carry: a C/C++
# toolchain, the Python headers, xz (Node.js ships .tar.xz) and Node.js
# itself. The runtime stage derives from `base`, so none of this reaches the
# published image — which is the point of #40: before it, the runtime stage
# inherited all of this and then "removed" it with apt-get purge, which only
# wrote whiteouts over ~250 MB of ancestor layers.
FROM base AS toolchain

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

# Native build tools + the Python headers the SDK's wheels compile against.
# build-essential is what node-pty and friends link against; python3.12-dev
# provides the headers for any sdist the SDK's installer builds.
RUN apt-get update -y --fix-missing --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        build-essential python3.12-dev xz-utils libatomic1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS ───────────────────────────────────────────────────────────
# hermes-agent 1.0.0 requires Node >= 22.22 and npm >= 11.17 (see its
# package.json engines); Ubuntu's stock nodejs is 18.x — too old. The SDK's
# own installer would do this (scripts/install.sh, NodeSource signed repo),
# but the SDK stage is not the only one that needs Node: the `frontend`
# stage builds the web assets with vite, and it must use the SAME Node
# version the SDK's own npm install used, or the two builds disagree.
# Installing it once here makes install.sh's NodeSource step a no-op
# (it checks `command -v node`) and keeps a single source of truth.
#
# The setup script is downloaded to a file and executed, never piped into a
# shell: `curl … | bash` swallows curl's exit status, and a failed download
# would then install an empty repository list.
#
# The whole thing is retried, and the RESULT is verified — the script itself
# is not a reliable failure signal. It downloads its signing key with a plain
# curl; when that connection resets, the key import fails, the script still
# exits 0, and apt then installs Ubuntu's stock nodejs 18 — which has no npm
# at all. The build would die much later with a bare "npm: not found" and no
# hint about the real cause. So: retry the setup, then assert the version
# install.sh would accept (node >= 22, npm >= 10). A flaky mirror now fails
# the build HERE, with a message that names the problem.
RUN set -eu; \
    _ok=0; \
    for _try in 1 2 3; do \
        echo "== NodeSource setup attempt $_try"; \
        rm -f /etc/apt/sources.list.d/nodesource.list; \
        if curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors \
                -o /tmp/nodesource_setup.sh \
                https://deb.nodesource.com/setup_22.x \
           && bash /tmp/nodesource_setup.sh \
           && apt-get install -y --no-install-recommends nodejs; then \
            _node=$(node --version 2>/dev/null || true); \
            _npm=$(npm --version 2>/dev/null || true); \
            if [ -n "$_node" ] && [ -n "$_npm" ] \
               && printf '%s' "$_node" | grep -qE '^v(2[2-9]|[3-9][0-9])' \
               && printf '%s' "$_npm" | grep -qE '^(1[0-9]|[2-9][0-9])\.'; then \
                _ok=1; break; \
            fi; \
            echo "++ got node=${_node:-<none>} npm=${_npm:-<none>} — not the Node 22 the SDK needs"; \
        else \
            echo "++ the NodeSource setup script failed"; \
        fi; \
        apt-get remove -y --purge nodejs npm >/dev/null 2>&1 || true; \
        rm -rf /var/lib/apt/lists/*; \
        sleep 10; \
    done; \
    [ "$_ok" = 1 ] \
      || { echo "++ could not install Node.js 22 (the SDK needs node >=22 and npm >=10)"; exit 1; }; \
    rm -f /tmp/nodesource_setup.sh; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get clean; \
    node --version; \
    npm --version

# npm configuration shared by every build stage that runs npm (the SDK
# install, the frontend build). The runtime stage derives from `base` and
# therefore carries NONE of these — before #40 the runtime inherited them
# from `testing` and had to blank them one by one.
#
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
ARG BUILD_NPM_REGISTRY=
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

# ---- app: source + baked deps -----------------------------------------------
FROM toolchain AS app

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

# Bake this app's own version tag into the image so the settings badge works
# even when .git is not present (it is excluded by .dockerignore). Not the
# hermes-agent SDK version — that one is pinned separately by
# HERMES_AGENT_COMMIT below.
# CI passes: --build-arg LEAP_AIDE_VERSION=<release tag minus its leading v>
# (release-runtime-image.yml), or nothing at all — scripts/build.sh then falls
# back to `git describe --tags --always`.
ARG LEAP_AIDE_VERSION=unknown
RUN echo "__version__ = '${LEAP_AIDE_VERSION}'" > /app/backend/app/api/_version.py

# hermes-agent pin (commit SHA). See .build.env at the repo root for
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

# ---- sdk: the hermes-agent SDK + the prune that makes the runtime small ----
FROM app AS sdk

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

# Knobs read directly by the hermes-agent installer's Node phase. Both are set
# here, in the sdk stage only: `runtime` is FROM base and inherits neither, and
# nothing they tune survives the sdk-stage prune below.
#
# NODE_DEPS_TIMEOUT — the installer wraps its workspace `npm install` in
# `run_with_timeout "$NODE_DEPS_TIMEOUT"` (upstream default 600 s) and SIGTERMs
# the whole npm process group when the budget runs out. Through a busy build
# proxy the registry fetches retry at ~35 s each, so the install overran 600 s,
# electron's postinstall died first, `install_node_deps || return` skipped
# `copy_config_templates`, and the seed came out without a config.yaml. This is
# the installer's own documented escape hatch for slow links, not a patch.
#
# ELECTRON_SKIP_BINARY_DOWNLOAD — electron's `npm/install.js` does
# `if (process.env.ELECTRON_SKIP_BINARY_DOWNLOAD) process.exit(0)`. The ~150 MB
# zip it would otherwise fetch is pure waste in this image: the sdk stage
# prunes `apps/`, `node_modules/` and `~/.cache/electron` before `testing` or
# `runtime` ever see them, and LeapAide never runs the Electron GUI. It is also
# the single slowest download in the phase, which is what pushed NODE_DEPS over
# its budget in the first place.
ENV NODE_DEPS_TIMEOUT=3600 \
    ELECTRON_SKIP_BINARY_DOWNLOAD=1

USER root

# install-wrappers.sh hardcodes /root/.cache/ms-playwright
# (it was designed for a root-owned install). Now that install.sh runs
# as root (USER root, below), Chromium lands at /root/.cache/ms-playwright
# directly — no symlink needed. We do need chmod 755 /root though, so
# leap-aide-init (the user tests run as) can traverse /root to invoke
# agent-browser (the wrapper redirects to the global desktop receptionist).
RUN chmod 755 /root

# ── Snapshot /usr/local/bin before the installer runs ────────────────────────
# The installer drops the command wrappers (hermes, hermes-agent, hermes-acp,
# agent-browser) into /usr/local/bin. The runtime stage cannot copy that whole
# directory: it already holds the 37 MB uv binary from `base`, and a COPY of
# the directory would write a SECOND copy of uv into the runtime layer.
# Instead the sdk stage stages the installer-provided wrappers in their own
# directory, and the runtime copies that. Deriving the list by diffing
# /usr/local/bin against a pre-install snapshot keeps it honest: a wrapper the
# installer adds later is picked up automatically, and node/npm/npx (installed
# by the toolchain stage, before this snapshot) are correctly excluded.
RUN ls -1 /usr/local/bin | LC_ALL=C sort > /usr/local/share/leap-aide/bin-before-install.txt \
    && echo "=== /usr/local/bin before the installer runs:" \
    && cat /usr/local/share/leap-aide/bin-before-install.txt

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
#     HERMES_AGENT_COMMIT — see .build.env)
#   - RUNS Playwright Chromium download (separate from hermes-agent's bundled
#     playwright-core to avoid the Node v26 hang bug — matches install.sh:776)
#   - RUNS browser-use CLI install
#   - RUNS leap-aide Python deps (uv pip install -r /app/backend/requirements.txt)
#   - RUNS command wrappers (agent-browser-wrapper)
# systemd is auto-skipped inside Docker (IN_DOCKER=true).
#
# Don't override HOME here — install.sh runs as USER root, and HOME only
# influences $HOME-relative fallbacks. Code placement is decided by the
# installer's root-Linux FHS layout (/usr/local/lib/hermes-agent, independent
# of HERMES_HOME); state lands in $HERMES_HOME (the seed dir). Setting
# HOME=/home/leap-aide-init would redirect $HOME/.local style paths (uv tools,
# caches) to leap-aide-init's home, which is not what we want.
# Pass --hermes-commit (preferred for stability — see
# .build.env) and --hermes-repo-url (custom fork/mirror,
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
ENV WARMUP_DIR=/tmp/hermes-agent-warmup

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
              --force-hermes-commit \
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

# ── Assert the Chromium binary has no unresolved shared library ────────────
# The runtime stage no longer inherits the SDK stage's `playwright
# install-deps` apt layer, so the Chromium runtime libraries are installed
# explicitly in `base`. If a future Playwright release needs one more shared
# library, Chrome would die at startup with a bare "error while loading
# shared libraries" and nobody would know why. Check it HERE, where the
# Chromium binary and the SDK's node are both available.
RUN set -e; \
    _chrome=""; \
    for _c in /root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome \
             /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome \
             /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome-headless-shell; do \
        [ -x "$_c" ] && { _chrome="$_c"; break; }; \
    done; \
    if [ -z "$_chrome" ]; then \
        echo "++ no Playwright Chromium binary found — the browser desktop would not work"; \
        exit 1; \
    fi; \
    echo "== checking shared libraries of $_chrome"; \
    _missing=$(ldd "$_chrome" 2>/dev/null | grep "not found" || true); \
    if [ -n "$_missing" ]; then \
        echo "++ Chromium has unresolved shared libraries — add them to the base stage:"; \
        echo "$_missing"; \
        exit 1; \
    fi; \
    echo "Chromium shared libraries OK"

# ── Stage the /usr/local/bin entries the runtime needs ─────────────────────
# The runtime stage derives from `base`, so it already has uv + uvx in
# /usr/local/bin. Everything else that landed there during the build must be
# copied in explicitly:
#
#   idna, pygmentize   console scripts of the backend's Python deps
#                      (aiohttp → yarl → idna, hindsight-client → pygments),
#                      installed system-wide by the `app` stage
#   hermes, hermes-agent, hermes-acp, agent-browser
#                      command wrappers written by the hermes-agent installer
#
# The list is derived by diffing /usr/local/bin against the BASE snapshot
# (taken in the `base` stage right after uv was installed), not against a
# hard-coded list: a wrapper the installer adds later is picked up
# automatically, and uv/uvx are correctly excluded (copying them would
# duplicate 37 MB).
RUN set -e; \
    mkdir -p /opt/leap-aide/bin; \
    ls -1 /usr/local/bin | LC_ALL=C sort > /tmp/bin-after-install.txt; \
    LC_ALL=C comm -13 /usr/local/share/leap-aide/bin-base.txt \
        /tmp/bin-after-install.txt > /tmp/bin-new.txt; \
    while IFS= read -r _f; do \
        [ -n "$_f" ] || continue; \
        cp -a "/usr/local/bin/$_f" /opt/leap-aide/bin/; \
    done < /tmp/bin-new.txt; \
    rm -f /tmp/bin-after-install.txt /tmp/bin-new.txt; \
    echo "=== staged /usr/local/bin entries:"; \
    ls -la /opt/leap-aide/bin/

# ── Prune the SDK tree ──────────────────────────────────────────────────────
# This is the single biggest lever of issue #40. Everything removed here is
# unreachable from the running server:
#
#   .git             638 MB — the update checker degrades gracefully without
#                    it (backend/app/api/updates.py returns None when the
#                    path is absent) and the entrypoint's commit check is
#                    guarded by `[ -d "$_agent_src/.git" ]`.
#   node_modules     1.1 GB — the runtime image ships no node/npm/npx, so
#                    the SDK's Node side (agent-browser, electron) cannot
#                    run anyway; browser_tool.py resolves it through
#                    `shutil.which("node")` and skips it.
#   apps/             352 MB — the Electron desktop GUI. Server-side unused
#                    (the only Python references are comments).
#   tests/ website/   65 MB — the SDK's own test suite and docs site.
#   docs/ evals/ nix/ contributors/ default.tar.gz — development artifacts.
#   web/node_modules   13 MB — the Hermes WebUI's own node_modules.
#
# The venv (280 MB) and the SDK's Python sources stay: they ARE the agent.
RUN rm -rf /usr/local/lib/hermes-agent/.git \
           /usr/local/lib/hermes-agent/node_modules \
           /usr/local/lib/hermes-agent/apps \
           /usr/local/lib/hermes-agent/tests \
           /usr/local/lib/hermes-agent/tests-js \
           /usr/local/lib/hermes-agent/website \
           /usr/local/lib/hermes-agent/docs \
           /usr/local/lib/hermes-agent/evals \
           /usr/local/lib/hermes-agent/nix \
           /usr/local/lib/hermes-agent/contributors \
           /usr/local/lib/hermes-agent/default.tar.gz \
           /usr/local/lib/hermes-agent/sqlite_leak_fix.png \
           /usr/local/lib/hermes-agent/web/node_modules \
           /root/.cache/electron \
           /root/.cache/node-gyp \
           /usr/local/lib/node_modules \
           /usr/local/include/node \
           /usr/local/share/man/man1/node* \
           /usr/local/share/doc/*node* \
           /root/.cache/node \
           /root/.cache/npm \
           /root/.cache/pip \
           /root/.cache/uv \
           /root/.npm/_cacache \
           /root/.npm/_logs \
           /root/.npm/_temp \
           /root/.npm/_update-notifier-last-checked \
           /root/.npm/_cacache-content

# ── Prune the Playwright cache ──────────────────────────────────────────────
#   chromium_headless_shell-*  273 MB — the SDK drives the full Chromium
#     build; backend/app/api/global_desktop.py globs for a binary literally
#     named `chrome`/`chrome.real`, which the headless shell does not
#     provide.
#   locales/*.pak              50 MB — 220 language bundles; only en-US and
#     zh-CN are reachable from a zh_CN.UTF-8 desktop.
#   WidevineCdm                21 MB — the DRM plugin; useless here.
RUN set -e; \
    _pw=/root/.cache/ms-playwright; \
    rm -rf "$_pw"/chromium_headless_shell-*; \
    for _locales in "$_pw"/chromium-*/chrome-linux64/locales "$_pw"/chromium-*/chrome-linux/locales; do \
        [ -d "$_locales" ] || continue; \
        find "$_locales" -maxdepth 1 -type f ! -name 'en-US.pak' ! -name 'zh-CN.pak' -delete; \
    done; \
    rm -rf "$_pw"/chromium-*/chrome-linux64/WidevineCdm "$_pw"/chromium-*/chrome-linux/WidevineCdm; \
    echo "=== Playwright cache after the prune:"; \
    du -sh "$_pw"/* 2>/dev/null || true

# ── Prune the Google API discovery documents ────────────────────────────────
# google-api-python-client ships 580 Google API discovery documents — 95 MB
# in the SDK venv. The Google Workspace skill only ever builds seven
# services: gmail, drive, calendar, docs, sheets, people and chat, so the
# other 573 are dead weight that makes the image bigger for no benefit.
#
# The browser-use tool venv is swept by the same loop. It does not carry
# google-api-python-client in today's install (the loop skips a path that
# does not exist), but a future tool bump that adds one would be caught
# here instead of silently putting ~95 MB back into the image.
#
# The keep-list is asserted for every path that exists (the files must be
# present and parse), so a renamed discovery document fails the build
# instead of silently breaking the workspace skill at runtime.
RUN set -e; \
    _keep="calendar.v3 docs.v1 drive.v3 gmail.v1 people.v1 sheets.v4 chat.v1"; \
    for _docs in \
        /usr/local/lib/hermes-agent/venv/lib/python*/site-packages/googleapiclient/discovery_cache/documents \
        /root/.local/share/uv/tools/browser-use/lib/python*/site-packages/googleapiclient/discovery_cache/documents ; do \
        [ -d "$_docs" ] || continue; \
        for _f in "$_docs"/*.json; do \
            [ -e "$_f" ] || continue; \
            _n=$(basename "$_f" .json); \
            case " $_keep " in *" $_n "*) ;; *) rm -f "$_f" ;; esac; \
        done; \
        for _k in $_keep; do \
            test -f "$_docs/$_k.json" || { echo "++ missing discovery document: $_k"; exit 1; }; \
            python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$_docs/$_k.json"; \
        done; \
        echo "=== $_docs: $(ls "$_docs" | wc -l) discovery documents left"; \
    done

# ---- testing: test + build image -------------------------------------------
FROM sdk AS testing

# The npm registry mirror is an ARG (ARG scope stops at FROM) — the ENV that
# carries it to every npm/npx call was set in the `toolchain` stage and is
# inherited, but the ARG itself has to be re-declared here for the `npm ci`
# below to be able to `npm config set registry` explicitly.
ARG BUILD_NPM_REGISTRY=

# Re-COPY backend/tests/ on top of the /app/backend/tests/ already populated
# by the earlier COPY . /app, so the testing image always has tests/ even if
# .dockerignore ever grows a rule that would drop them. Path mirrors the repo
# layout (backend/tests/) so conftest.py's REPO_ROOT discovery resolves the
# server.py location correctly.
COPY --chown=leap-aide-init:leap-aide-init backend/tests /app/backend/tests

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
# `WORKDIR /app` running as root, and `COPY --chown` only chowns the entries
# it copies, not a destination directory that already exists. So `mkdir
# /app/static` from leap-aide-init fails with EACCES. Create it here, as
# root, and hand it over. (Before static/ was excluded this never showed up:
# the copied static/ entry was chowned correctly.)
RUN mkdir -p /app/static && chown leap-aide-init:leap-aide-init /app/static
USER leap-aide-init

# Frontend deps (vitest, vite, tsc, postcss, tailwindcss) for tests + static
# build. install.sh doesn't touch frontend. Drop to leap-aide-init since npm ≥7
# refuses root installs. Timeout/retry is set as a toolchain-stage ENV (above)
# so it applies here AND to install.sh's npm/npx calls; no per-invocation
# flags needed.
RUN cd /app/frontend \
    && if [ -n "${BUILD_NPM_REGISTRY:-}" ]; then npm config set registry "${BUILD_NPM_REGISTRY}"; fi \
    && npm ci --no-audit --no-fund \
    && npm cache clean --force \
    && rm -rf node_modules/.cache

# Build the frontend once, bake the artifacts (static/) into the image.
# Doing this in testing (not runtime) because:
#   * testing already has node + npm + frontend/node_modules (from `npm ci`
#     above); the runtime stage copies the prebuilt static/ out of the
#     `frontend` stage, so it never needs node at all.
#   * the assets are identical in both images — they are built from the same
#     frontend/ sources by the same vite version.
# Without this step, runtime's `COPY . /app` (which now happens in the `app`
# stage) would pick up whatever stale static/ happens to be in the build
# context — empty on clean CI, stale on local devs — and the served frontend
# would 404 or load JS with hashes that no longer match index.html.
#
# Drop the vite cache + tsbuildinfo after build (rebuildable on demand);
# keep frontend/src/ — testing still runs vitest on it. Keep node_modules —
# the frontend test suite needs it.
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
#   command=["scripts/run-test.sh"]  args=["backend", "full"]   # backend pytest
#   command=["scripts/run-test.sh"]  args=["frontend", "full"]  # frontend vitest
#   command=["scripts/build.sh"]     args=["--frontend"]        # bake ../static/
#   command=["pytest"]               args=["backend/tests/", "-v"]
#   command=["bash", "-c"]           args=["cd frontend && npm run build"]

# ---- frontend: the prebuilt web assets for the runtime image ----------------
# A second, INDEPENDENT frontend build: it starts from `app`, so it does not
# carry the SDK, and it produces exactly the tree the runtime copies into
# /app/static. Keeping it separate means the runtime image never inherits
# the SDK's layer chain, and the two builds can run in parallel with the SDK
# install.
FROM app AS frontend

ARG BUILD_NPM_REGISTRY=

USER root
RUN mkdir -p /app/static && chown leap-aide-init:leap-aide-init /app/static
USER leap-aide-init

RUN cd /app/frontend \
    && if [ -n "${BUILD_NPM_REGISTRY:-}" ]; then npm config set registry "${BUILD_NPM_REGISTRY}"; fi \
    && npm ci --no-audit --no-fund \
    && npm cache clean --force \
    && rm -rf node_modules/.cache

# Same build the testing stage runs (scripts/build.sh --frontend → vite).
# The static/ tree is what the runtime image serves; see the `testing` stage
# for the details on why the assets are rebuilt inside the image.
RUN rm -rf /app/static/assets /app/static/index.html \
    && bash /app/scripts/build.sh --frontend \
    && rm -rf /app/frontend/.vite /app/frontend/.tsbuildinfo

# ---- runtime: production image (default) — built from `base`, not `testing` ---
# The stage graph is the fix for issue #40. `runtime` derives from `base`, so
# the runtime image's layer chain contains ONLY: the OS, the backend deps, the
# prebuilt frontend and the pruned SDK trees. It never contains the build
# chain, node, npm's cache, the Playwright headless shell, the SDK's .git or
# its Electron app — none of which were ever copied in.
FROM base AS runtime

# Build-time proxy / mirror ARGs are re-declared for the values the runtime
# image must remember (the hermes-agent commit pin and repo URL). The proxy
# vars themselves stay ARG-only, so the published image carries no
# HTTP_PROXY keys at all — Dockerfile has no way to unset an ENV, which is
# exactly why they were never ENVs to begin with.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

# hermes-agent pin (commit SHA) and repository URL. The entrypoint warns
# when the running SDK does not match the pin; the repo URL is forwarded to
# the SDK's own tooling.
ARG HERMES_AGENT_COMMIT=
ENV HERMES_AGENT_COMMIT=${HERMES_AGENT_COMMIT}
ARG HERMES_AGENT_REPO_URL=
ENV HERMES_AGENT_REPO_URL=${HERMES_AGENT_REPO_URL}

# HERMES_HOME points at the STATE dir (the compose volume), not at the
# build-time seed dir (/var/lib/leap-aide/hermes-seed). The entrypoint copies
# the seed into it on first boot.
ENV HERMES_HOME=/home/leap-aide/.hermes

# gh keeps its auth state (hosts.yml / config.yml) under $GH_CONFIG_DIR,
# which defaults to ~/.config/gh — a path that is NOT part of the compose
# volume, so `gh auth login` would be lost on every container recreation.
# Point it inside the persistent ~/.hermes volume instead: the login
# survives image rebuilds, and the Hermes SDK's `gh auth token` probe
# (same container, same `leap-aide` user, inherits this ENV) reads exactly
# those credentials. gh creates the directory on demand, so nothing has to
# pre-create it — the bind mount simply shadows the image copy, and the
# entrypoint chowns /home/leap-aide recursively on every boot.
ENV GH_CONFIG_DIR=/home/leap-aide/.hermes/gh

# Immutable-image seal for lazy runtime installs (issue #8): the agent venv is
# root-owned while the server runs as leap-aide-init, so a lazy `uv pip install`
# would burn ~26s and die with EACCES anyway. Sealed, it fails fast with an
# actionable hint instead. Pre-installed core lazy deps (anthropic, boto3,
# edge-tts, fal-client — install.sh Step 4) are unaffected: ensure() returns
# early once they import.
# Durable-target installs keep working if the operator sets
# HERMES_LAZY_INSTALL_TARGET; override the seal per-container with
# -e HERMES_DISABLE_LAZY_INSTALLS=0.
ENV HERMES_DISABLE_LAZY_INSTALLS=1

# Default to binding all interfaces (required for container networking)
ENV HERMES_WEBUI_HOST=0.0.0.0 \
    HERMES_WEBUI_PORT=8787

USER root

# ── The application source ──────────────────────────────────────────────────
# Straight from the `app` stage: the repo tree plus the backend's
# system-wide Python deps (pyyaml, aiohttp, hindsight-client). The frontend
# source tree comes along — it is ~1 MB and the SDK's own tooling reads it.
COPY --from=app --chown=leap-aide-init:leap-aide-init /app /app

# The backend's system-wide dist-packages. Copied separately from /app
# because `uv pip install --system` writes to /usr/local/lib/python3.12/…,
# outside the /app tree.
COPY --from=app /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages

# The prebuilt frontend. Served from /app/static by the server
# (backend/app/api/routes.py resolves it relative to the repo root).
COPY --from=frontend --chown=leap-aide-init:leap-aide-init /app/static /app/static

# ── The Hermes Agent SDK ────────────────────────────────────────────────────
# Already pruned in the `sdk` stage: no .git, no node_modules, no Electron
# app, no test suite. What remains is the agent's Python code and its venv.
COPY --from=sdk /usr/local/lib/hermes-agent /usr/local/lib/hermes-agent

# The uv-managed CPython the agent venv was built against. The venv's
# bin/python is a symlink into /usr/local/share/uv/python/…, so without this
# the venv is a dead directory and the entrypoint's fail-fast check on
# venv/bin/python would never even get to run.
COPY --from=sdk /usr/local/share/uv/python /usr/local/share/uv/python
COPY --from=sdk /usr/local/share/uv/bin /usr/local/share/uv/bin

# Playwright's Chromium — the browser desktop's browser. Pruned to the full
# Chromium build (no headless shell), two locales and no Widevine.
COPY --from=sdk /root/.cache/ms-playwright /root/.cache/ms-playwright

# The browser-use tool venv and the cua-driver launcher symlink. The seed's
# bin/browser-use symlinks point straight into the tool venv, so the path has
# to match exactly.
COPY --from=sdk /root/.local /root/.local

# cua-driver — the SDK's computer-use backend (~/.cua-driver).
COPY --from=sdk /root/.cua-driver /root/.cua-driver

# uv's install receipt. uv reads it to decide whether it has to fetch itself
# again; without it a `uv` invocation in the container would try to
# re-download the binary.
COPY --from=sdk /root/.config/uv /root/.config/uv

# The hermes-agent state seed. The entrypoint copies it into
# $HERMES_HOME (/home/leap-aide/.hermes) on first boot: config.yaml with
# display.show_reasoning=true, .env, SOUL.md, skills/ and the managed bin/
# (uv, the browser-use launchers).
COPY --from=sdk /var/lib/leap-aide/hermes-seed /var/lib/leap-aide/hermes-seed

# The installer's command wrappers (hermes, hermes-agent, hermes-acp,
# agent-browser). Staged in their own directory by the sdk stage so the
# runtime does not have to copy the whole /usr/local/bin (which would carry
# a second copy of the 37 MB uv binary).
COPY --from=sdk /opt/leap-aide/bin/ /usr/local/bin/

# ── Trim the docs and agent instructions ────────────────────────────────────
# The runtime image reads none of them: the bilingual-pair gate
# (backend/tests/test_docs_i18n_pairing.py) runs in the testing image, and
# the shipped image should not carry the repository's own documentation.
#
# The build/test shell scripts go with them: the runtime never invokes them
# (the image is built, not built-from), and install.sh in particular is a
# host installer too — shipping it inside the image only invites someone to
# run it there.
RUN rm -rf /app/docs \
           /app/AGENTS.md \
           /app/AGENTS.zh-CN.md \
           /app/AGENTS.i18n.yaml \
           /app/.gitignore \
    && rm -f /app/scripts/install.sh \
             /app/scripts/build.sh \
             /app/scripts/run-test.sh \
             /app/scripts/cmd-util.sh

# Restore runtime user. The entrypoint bash runs as the right user.
USER leap-aide-init

COPY --chmod=555 backend/scripts/docker-entrypoint.sh /leap-aide-entrypoint.sh

EXPOSE 8787

ENTRYPOINT ["/leap-aide-entrypoint.sh"]
