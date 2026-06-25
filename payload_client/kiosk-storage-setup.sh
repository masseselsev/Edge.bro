#!/bin/bash
set -e
PERSISTENT_DEV="/dev/disk/by-label/kiosk-data"
MOUNT_POINT="/media/usb-data"

if [ -b "$PERSISTENT_DEV" ]; then
    echo "Persistent partition found. Mounting..."
    mkdir -p "$MOUNT_POINT"
    mount "$PERSISTENT_DEV" "$MOUNT_POINT"
else
    echo "Persistent partition not found. Attempting partitioning..."
    # Find boot partition
    BOOT_PART=$(findmnt -n -o SOURCE /run/live/medium || true)
    if [ -z "$BOOT_PART" ] || [[ "$BOOT_PART" == *"/dev/sr"* ]]; then
        echo "WARNING: Booting from CD-ROM or VM. Falling back to non-persistent mode."
        exit 0
    fi
    
    # Extract parent disk
    PARENT_DISK=$(echo "$BOOT_PART" | sed -r 's/p?[0-9]+$//')
    if [ -z "$PARENT_DISK" ] || [ ! -b "$PARENT_DISK" ]; then
        echo "ERROR: Could not resolve parent disk for $BOOT_PART"
        exit 0
    fi
    
    echo "Parent USB disk is $PARENT_DISK. Creating partition..."
    # Relocate GPT backup headers to the physical end of the disk
    echo "Fix" | parted "$PARENT_DISK" print || true
    
    # Find end of last partition in MB
    LAST_END=$(parted -s "$PARENT_DISK" unit MB print | awk '/^[0-9]/ {end=$3} END {print end}')
    if [ -z "$LAST_END" ]; then
        LAST_END="4000MB"
    fi

    parted -s "$PARENT_DISK" mkpart primary ext4 "$LAST_END" 100%
    partprobe "$PARENT_DISK" || true
    udevadm settle || true
    
    # Find new partition
    NEW_PART=$(lsblk -o NAME,TYPE -n -l "$PARENT_DISK" | grep part | tail -n 1 | awk '{print "/dev/"$1}')
    if [ -z "$NEW_PART" ] || [ ! -b "$NEW_PART" ]; then
        echo "ERROR: New partition not found"
        exit 0
    fi
    
    echo "Formatting $NEW_PART as ext4 with label kiosk-data..."
    mkfs.ext4 -F -L kiosk-data "$NEW_PART"
    mkdir -p "$MOUNT_POINT"
    mount "$NEW_PART" "$MOUNT_POINT"
fi

# Setup storage layout & symlinks
mkdir -p "$MOUNT_POINT"/.ssh
mkdir -p "$MOUNT_POINT"/borg/fleet

CONFIG_FILE="/opt/offline-client/backend/config.json"
PERSISTENT_CONFIG="$MOUNT_POINT/config.json"
if [ ! -f "$PERSISTENT_CONFIG" ] && [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$PERSISTENT_CONFIG"
fi
ln -sf "$PERSISTENT_CONFIG" "$CONFIG_FILE"

# Link SSH key
SSH_KEY="/opt/offline-client/backend/id_ed25519"
PERSISTENT_SSH="$MOUNT_POINT/.ssh/id_ed25519"
if [ -f "$SSH_KEY" ] && [ ! -f "$PERSISTENT_SSH" ]; then
    cp "$SSH_KEY" "$PERSISTENT_SSH"
    chmod 600 "$PERSISTENT_SSH"
fi
if [ -f "$SSH_KEY.pub" ] && [ ! -f "$PERSISTENT_SSH.pub" ]; then
    cp "$SSH_KEY.pub" "$PERSISTENT_SSH.pub"
fi
if [ -f "$PERSISTENT_SSH" ]; then
    ln -sf "$PERSISTENT_SSH" "$SSH_KEY"
    ln -sf "$PERSISTENT_SSH.pub" "$SSH_KEY.pub"
fi
echo "Kiosk storage setup completed successfully!"
