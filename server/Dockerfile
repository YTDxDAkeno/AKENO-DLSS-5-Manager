# syntax=docker/dockerfile:1
# AKENO licensing server. One image for the three services (AKENO_ROLE = api | web | admin).
# Behind a TLS-inspecting proxy, pass its CA for pip: docker build --secret id=pip_ca,src=/path/ca.crt ...
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

RUN useradd --system --uid 10001 --user-group --home-dir /app --shell /usr/sbin/nologin akeno
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; pip install -r requirements.txt
COPY pyproject.toml alembic.ini ./
COPY akeno_server ./akeno_server
COPY migrations ./migrations
RUN printf '#!/bin/sh\nexec python -m akeno_server "$@"\n' > /usr/local/bin/akeno-server \
    && chmod 0755 /usr/local/bin/akeno-server && mkdir -p /data/keys /data/mail && chown -R akeno:akeno /data
COPY docker-entrypoint.sh /usr/local/bin/akeno-entrypoint
RUN chmod 0755 /usr/local/bin/akeno-entrypoint

USER akeno
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/system/health', timeout=4)"
ENTRYPOINT ["/usr/local/bin/akeno-entrypoint"]
