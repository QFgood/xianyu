#!/bin/bash
set -euo pipefail
umask 077
if [ "${#BROWSER_PASSWORD}" -lt 12 ]; then
    echo 'BROWSER_PASSWORD must contain at least 12 characters.' >&2
    exit 1
fi
printf '%s\n' "$BROWSER_PASSWORD" | htpasswd -ci /tmp/browser.htpasswd browser >/dev/null 2>&1
unset BROWSER_PASSWORD
mkdir -p /tmp/nginx /config/chromium
exec 9>/config/.browser-owner.lock
if ! flock -n 9; then
    echo 'This browser profile is already in use by another container.' >&2
    exit 1
fi
# A recreated container has a different hostname. Remove only stale Chromium
# singleton markers after taking exclusive ownership of this profile volume.
rm -f /config/chromium/SingletonLock /config/chromium/SingletonSocket /config/chromium/SingletonCookie
children=()
cleanup() { kill "${children[@]}" 2>/dev/null || true; wait || true; }
trap cleanup EXIT
trap 'exit 0' TERM INT
Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp -ac & children+=("$!")
for attempt in {1..50}; do
    [ -S /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done
openbox >/tmp/openbox.log 2>&1 & children+=("$!")
x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5900 -quiet >/tmp/vnc.log 2>&1 & children+=("$!")
websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 >/tmp/websockify.log 2>&1 & children+=("$!")
# CDP is reachable only on the Compose network, never published to the NAS host.
socat TCP-LISTEN:9223,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:9222 & children+=("$!")
chromium --no-sandbox --disable-dev-shm-usage --no-first-run --no-default-browser-check \
    --user-data-dir=/config/chromium --password-store=basic \
    --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
    "${BROWSER_START_URL:-https://www.goofish.com/}" >/tmp/chromium.log 2>&1 & children+=("$!")
nginx -c /etc/nginx/browser.conf -g 'daemon off;' & children+=("$!")
wait -n "${children[@]}"
