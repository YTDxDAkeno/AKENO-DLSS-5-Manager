# syntax=docker/dockerfile:1
FROM debian:bookworm-slim
RUN --mount=type=secret,id=ca,required=true \
    apt-get -o Acquire::https::CaInfo=/run/secrets/ca update && \
    apt-get -o Acquire::https::CaInfo=/run/secrets/ca install -y --no-install-recommends \
      wireguard-tools wireguard-go iproute2 iptables python3 ca-certificates && \
    rm -rf /var/lib/apt/lists/*
CMD ["sleep", "infinity"]
