FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y \
    apt-cacher-ng \
    && rm -rf /var/lib/apt/lists/*

# Allow HTTPS tunneling in apt-cacher-ng
RUN echo "PassThroughPattern: .*" >> /etc/apt-cacher-ng/acng.conf

# Force apt-cacher-ng to use IPv4 for upstream connections to prevent IPv6 timeouts
RUN echo "ConnectProto: v4" >> /etc/apt-cacher-ng/acng.conf

EXPOSE 3142
CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1"]
