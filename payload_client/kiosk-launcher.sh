#!/bin/bash
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
