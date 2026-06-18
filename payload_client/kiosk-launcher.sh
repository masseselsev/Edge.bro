#!/bin/bash
# Mark the desktop shortcut as trusted to bypass XFCE's untrusted launcher warning
DESKTOP_SHORTCUT="$HOME/Desktop/offline-kiosk.desktop"
if [ -f "$DESKTOP_SHORTCUT" ]; then
  chmod +x "$DESKTOP_SHORTCUT"
  if command -v gio &>/dev/null; then
    gio set -t string "$DESKTOP_SHORTCUT" metadata::xfce-exe-checksum "$(sha256sum "$DESKTOP_SHORTCUT" | awk '{print $1}')" &>/dev/null
  fi
fi

# Disable screensaver and DPMS (screen turning off / going to sleep)
if command -v xset &>/dev/null; then
  xset s off      # disable screensaver
  xset s noblank  # don't blank the video device
  xset -dpms      # disable DPMS (Energy Star) features
fi

if command -v xfconf-query &>/dev/null; then
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-sleep -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-off -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s false --create -t bool || true
  xfconf-query -c xfce4-screensaver -p /saver/enabled -s false --create -t bool || true
  xfconf-query -c xfce4-screensaver -p /lock/enabled -s false --create -t bool || true
fi

# Wait for backend to be ready
logger -t offline-kiosk "Waiting for offline backend on port 8000..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:8000/api/nodes > /dev/null; then
    logger -t offline-kiosk "Backend is online!"
    break
  fi
  sleep 1
done

logger -t offline-kiosk "Launching web browser..."

# Try launching chromium or firefox in kiosk mode
if command -v chromium &>/dev/null; then
  logger -t offline-kiosk "Found chromium, launching in kiosk mode"
  exec chromium --kiosk --incognito --no-errdialogs --disable-translate --no-first-run --fast --fast-start --disable-infobars http://127.0.0.1:8000
elif command -v chromium-browser &>/dev/null; then
  logger -t offline-kiosk "Found chromium-browser, launching in kiosk mode"
  exec chromium-browser --kiosk --incognito --no-errdialogs --disable-translate --no-first-run --fast --fast-start --disable-infobars http://127.0.0.1:8000
elif command -v firefox &>/dev/null; then
  logger -t offline-kiosk "Found firefox, launching in kiosk mode"
  exec firefox --kiosk --private-window http://127.0.0.1:8000
elif command -v firefox-esr &>/dev/null; then
  logger -t offline-kiosk "Found firefox-esr, launching in kiosk mode"
  exec firefox-esr --kiosk --private-window http://127.0.0.1:8000
else
  logger -t offline-kiosk "No kiosk browser found, falling back to x-www-browser"
  exec x-www-browser http://127.0.0.1:8000
fi
