#!/usr/bin/env bash
# Renders the README screenshots into docs/images/ from the real app, with a
# believable account served by scenario.py (no LINE connection, no keyring).
#
#   scripts/screenshots/shoot.sh [output-dir]
#
# Needs gtk4-broadwayd (package libgtk-4-bin), dbus-run-session, curl and
# Pillow (python3-pil). The stock photos listed in photos.json are downloaded
# from Unsplash into the user cache on the first run.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$here/../.." && pwd)
out=${1:-$repo/docs/images}
cache=${XDG_CACHE_HOME:-$HOME/.cache}/matrix-line/screenshot-photos
mkdir -p "$cache"

python3 - "$here/photos.json" "$cache" <<'PY'
import json, subprocess, sys
from pathlib import Path
spec, cache = json.load(open(sys.argv[1])), Path(sys.argv[2])
for group, size in (("avatars", "w=480&h=480&fit=crop&crop=faces"), ("pictures", "w=1280")):
    for key, item in spec[group].items():
        target = cache / f"{key}.jpg"
        if not target.exists():
            url = f"https://images.unsplash.com/{item['photo']}?{size}&q=85&fm=jpg"
            subprocess.run(["curl", "-sfL", "--max-time", "60", "-o", str(target), url], check=True)
PY

work=$(mktemp -d "${TMPDIR:-/tmp}/line-shots.XXXXXX")
trap 'kill "$broadway" 2>/dev/null || true; rm -rf -- "$work"' EXIT
mkdir -p "$work/data" "$work/cache" "$work/config" "$work/run" "$work/png"
chmod 700 "$work/run"
display=:${SCREENSHOT_DISPLAY:-47}
XDG_RUNTIME_DIR="$work/run" gtk4-broadwayd "$display" >/dev/null 2>&1 &
broadway=$!
sleep 0.5
for theme in light dark; do
    # The app reads its theme from its settings file at start.
    mkdir -p "$work/data/matrix-line/preferences"
    printf '{"theme": "%s"}' "$theme" > "$work/data/matrix-line/preferences/settings.json"
    env -u WAYLAND_DISPLAY -u DISPLAY \
        XDG_DATA_HOME="$work/data" XDG_CACHE_HOME="$work/cache" XDG_CONFIG_HOME="$work/config" XDG_RUNTIME_DIR="$work/run" \
        GDK_BACKEND=broadway BROADWAY_DISPLAY="$display" GTK_A11Y=none LANG=zh_TW.UTF-8 \
        LINE_APP_CORE="$here/scenario.py" SCREENSHOT_PHOTOS="$cache" SCREENSHOT_OUT="$work/png" SCREENSHOT_THEME="$theme" \
        dbus-run-session -- /usr/bin/python3 "$here/shoot.py" "$repo" 2>"$work/app.log" || { tail -20 "$work/app.log" >&2; exit 1; }
done

# WebP keeps the README light; the PNGs stay in the work folder.
mkdir -p "$out"
python3 - "$work/png" "$out" <<'PY'
import sys
from pathlib import Path
from PIL import Image
for png in sorted(Path(sys.argv[1]).glob("*.png")):
    target = Path(sys.argv[2]) / (png.stem + ".webp")
    Image.open(png).save(target, "WEBP", quality=88, method=6)
    print(target, f"{target.stat().st_size // 1024} KB")
PY
