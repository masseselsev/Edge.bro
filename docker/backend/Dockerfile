FROM python:3.13-slim
RUN apt-get update && apt-get install -y \
    ssh openssh-client sshpass rsync curl parted util-linux fdisk dosfstools e2fsprogs systemd iproute2 borgbackup udev \
    xorriso squashfs-tools syslinux syslinux-common isolinux cpio \
    gcc libpq-dev \
    && mkdir -p /opt/offline-packages \
    && cd /opt/offline-packages \
    && apt-get download openssh-server openssh-client openssh-sftp-server runit-helper libwtmpdb0 wireguard-tools openresolv \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
