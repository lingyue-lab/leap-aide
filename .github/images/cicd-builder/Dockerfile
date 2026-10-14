# CICD builder image: Ubuntu 24.04 + buildah, used as the job container
# that builds the runtime/testing product images. Keep the toolset
# minimal; extend only when the build steps actually need it.
FROM ubuntu:24.04

# crun is explicit: it is only a Recommends of buildah ("crun | runc") and
# --no-install-recommends drops it. Without an OCI runtime, buildah's RUN
# steps fail at container creation with "exec: no command".
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        buildah \
        crun \
        ca-certificates \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*
