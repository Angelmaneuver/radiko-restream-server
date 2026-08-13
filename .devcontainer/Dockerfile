ARG  VARIANT=3

FROM mcr.microsoft.com/devcontainers/python:${VARIANT}

RUN  apt-get update && apt-get install -y \
       curl                               \
       ffmpeg                             \
     && apt-get clean                     \
     && rm -rf /var/lib/apt/lists/*

ENV  UV_INSTALL_DIR="/usr/local/bin"

RUN  curl -LsSf https://astral.sh/uv/install.sh | sh
