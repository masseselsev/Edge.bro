FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y \
    openssh-server borgbackup \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir /var/run/sshd && mkdir -p /data/borg
RUN useradd -m -d /home/borg -s /bin/bash borg
COPY docker/borg/sshd_hostkeys.conf /etc/ssh/sshd_config.d/10-hostkeys.conf
COPY docker/borg/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
EXPOSE 22
CMD ["/usr/local/bin/entrypoint.sh"]
