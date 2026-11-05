#!/usr/bin/env bash
# Runs LINE GTK on a virtual display and serves it as a web page through noVNC.
# Used by scripts/remote-view.Dockerfile; run it inside a D-Bus session.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
web_dir=${LINE_VIEW_WEB:-/opt/line-view-web}
port=${LINE_VIEW_PORT:-6080}
mode_args=(--demo)
if [[ ${LINE_MODE:-demo} == live ]]; then
    mode_args=()
    # A fresh keyring with an empty password, so the login can be kept for
    # this container run. It disappears with the container.
    printf '\n' | gnome-keyring-daemon --unlock --components=secrets >/dev/null
    eval "$(gnome-keyring-daemon --start --components=secrets)"
    export GNOME_KEYRING_CONTROL
fi

export DISPLAY=${DISPLAY:-:99}
Xvfb "$DISPLAY" -screen 0 1408x888x24 -nolisten tcp &
until xsetroot -solid '#eef3f0' 2>/dev/null; do sleep 0.2; done

# Closing the window restarts the app so the page never goes blank.
(
    while true; do
        "$project_dir/line-app" "${mode_args[@]}" || true
        sleep 1
    done
) &

x11vnc -display "$DISPLAY" -forever -shared -nopw -localhost -rfbport 5900 -quiet -noxdamage &
echo "LINE GTK 畫面：http://localhost:$port" >&2
exec websockify --web "$web_dir" "0.0.0.0:$port" localhost:5900
