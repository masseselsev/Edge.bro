# Trixie, matching the other images. Bookworm's regular support ended
# 2026-07-11; only LTS remains, and there is nothing here that needs the older
# release. apt-cacher-ng caches whatever suites the nodes ask for, so the
# proxy's own base has no bearing on what it serves.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y \
    apt-cacher-ng \
    && rm -rf /var/lib/apt/lists/*

# Allow HTTPS tunneling in apt-cacher-ng
RUN echo "PassThroughPattern: .*" >> /etc/apt-cacher-ng/acng.conf

# Force apt-cacher-ng to use IPv4 for upstream connections to prevent IPv6 timeouts
RUN echo "ConnectProto: v4" >> /etc/apt-cacher-ng/acng.conf

EXPOSE 3142
CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1"]
