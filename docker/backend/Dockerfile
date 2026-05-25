FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    ssh openssh-client sshpass rsync curl parted util-linux dosfstools e2fsprogs systemd iproute2 borgbackup udev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
