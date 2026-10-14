# CI base image: Ubuntu 24.04 + buildah, used as the job container for
# image-build workflows. Keep the toolset minimal; extend only when the
# build steps actually need it.
FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        buildah \
        ca-certificates \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*
