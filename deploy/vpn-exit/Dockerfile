# syntax=docker/dockerfile:1
# Supply an approved official python:3.12-slim-bookworm@sha256:... image digest at build time.
# No default image/tag: an operator must record the exact reviewed base image used for a deployment.
ARG AKENO_EXIT_BASE_IMAGE
FROM ${AKENO_EXIT_BASE_IMAGE}
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then \
      printf '%s\n' 'Acquire::https::CaInfo "/run/secrets/system_ca";' > /etc/apt/apt.conf.d/99-session-ca; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates iproute2 nftables wireguard-tools && rm -rf /var/lib/apt/lists/*
RUN rm -f /etc/apt/apt.conf.d/99-session-ca
WORKDIR /opt/akeno-exit
COPY server/akeno_server/vpn/agent.py ./agent.py
COPY deploy/vpn-exit/entrypoint.py ./entrypoint.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/akeno-exit/entrypoint.py"]
