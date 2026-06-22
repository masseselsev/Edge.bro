FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y \
    apt-cacher-ng \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 3142
CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1"]
