#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
output_dir=${1:-.audit/gtk-smoke}
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
export LINE_APP_CORE=${LINE_APP_CORE:-$(scripts/build-core.sh)}
export LINE_SMOKE_OUTPUT=$output_dir
export GSK_RENDERER=cairo
export GDK_BACKEND=x11
export GTK_A11Y=none
exec dbus-run-session -- xvfb-run -a -s '-screen 0 1600x1000x24' /usr/bin/python3 scripts/gui-smoke.py
