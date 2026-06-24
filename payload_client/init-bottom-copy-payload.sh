#!/bin/sh
PREREQ=""
prereqs() {
    echo "$PREREQ"
}
case $1 in
prereqs)
    prereqs
    exit 0
    ;;
esac

echo "===================================================="
echo "Offline Client: Copying payload files to real root..."
echo "===================================================="

# Copy opt files
if [ -d /opt/offline-client ]; then
    mkdir -p /root/opt
    cp -r /opt/offline-client /root/opt/
fi

# Copy systemd units
if [ -d /etc/systemd/system ]; then
    mkdir -p /root/etc/systemd/system/multi-user.target.wants
    
    if [ -f /etc/systemd/system/offline-backend.service ]; then
        cp /etc/systemd/system/offline-backend.service /root/etc/systemd/system/
        ln -sf /etc/systemd/system/offline-backend.service /root/etc/systemd/system/multi-user.target.wants/offline-backend.service
    fi

    if [ -f /etc/systemd/system/offline-ssh-install.service ]; then
        cp /etc/systemd/system/offline-ssh-install.service /root/etc/systemd/system/
        ln -sf /etc/systemd/system/offline-ssh-install.service /root/etc/systemd/system/multi-user.target.wants/offline-ssh-install.service
    fi

    if [ -f /etc/systemd/system/kiosk-storage-setup.service ]; then
        cp /etc/systemd/system/kiosk-storage-setup.service /root/etc/systemd/system/
        ln -sf /etc/systemd/system/kiosk-storage-setup.service /root/etc/systemd/system/multi-user.target.wants/kiosk-storage-setup.service
    fi

fi

# Set NetworkManager to manage all interfaces
if [ -f /root/etc/NetworkManager/NetworkManager.conf ]; then
    sed -i 's/managed=false/managed=true/g' /root/etc/NetworkManager/NetworkManager.conf
fi

# Clean /etc/network/interfaces to prevent conflicts with NetworkManager
if [ -f /root/etc/network/interfaces ]; then
    echo "auto lo" > /root/etc/network/interfaces
    echo "iface lo inet loopback" >> /root/etc/network/interfaces
fi


# Copy XDG autostart kiosk file
if [ -d /etc/xdg/autostart ]; then
    mkdir -p /root/etc/xdg/autostart
    if [ -f /etc/xdg/autostart/offline-kiosk.desktop ]; then
        cp /etc/xdg/autostart/offline-kiosk.desktop /root/etc/xdg/autostart/
    fi
fi

# Copy etc/skel files (like Desktop shortcut)
if [ -d /etc/skel ]; then
    mkdir -p /root/etc/skel
    cp -r /etc/skel/* /root/etc/skel/
fi

echo "Offline Client: Payload files copy completed!"
