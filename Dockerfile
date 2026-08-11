FROM    python:3.14-slim

ENV     PYTHONDONTWRITEBYTECODE=1
ENV     PYTHONUNBUFFERED=1

ARG     http_proxy
ARG     HTTP_PROXY
ARG     https_proxy
ARG     HTTPS_PROXY
ARG     ftp_proxy
ARG     FTP_PROXY

WORKDIR /app

RUN     apt-get update && apt-get install -y \
          sudo                               \
        && apt-get clean                     \
        && rm -rf /var/lib/apt/lists/*

COPY    src src

COPY    pyproject.toml pyproject.toml

COPY    README.md README.md

COPY    requirements.txt requirements.txt

RUN     pip install --upgrade pip            \
        && pip install -r requirements.txt   \
        && pip cache purge

CMD     ["gunicorn", "--workers", "1", "--threads", "4", "--worker-class", "gthread", "-b", "0.0.0.0:5000", "radiko_restream_server.app:app"]
