# Trixie, matching the backend and worker images.
#
# This is a version dependency, not housekeeping. The nodes run `borg create`
# and push here over ssh, so this container is the borg *server* and the
# backend/worker containers are borg clients when they prune. Bookworm ships
# borg 1.2.4 while python:3.13-slim now resolves to trixie and ships 1.4.0, so
# leaving this behind meant client and server drifted apart by accident rather
# than by decision.
#
# Bookworm's 1.2.4 also carries CVE-2023-36811 (archive spoofing), which Debian
# marked "ignored" for that release and will not backport. Trixie's 1.4.0-5
# has the fix.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y \
    openssh-server borgbackup \
    && rm -rf /var/lib/apt/lists/*
# -p on both: trixie's openssh-server already creates /run/sshd (/var/run is a
# symlink to /run), so a bare mkdir fails the build.
RUN mkdir -p /var/run/sshd /data/borg
RUN useradd -m -d /home/borg -s /bin/bash borg
COPY docker/borg/sshd_hostkeys.conf /etc/ssh/sshd_config.d/10-hostkeys.conf
COPY docker/borg/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
EXPOSE 22
CMD ["/usr/local/bin/entrypoint.sh"]
