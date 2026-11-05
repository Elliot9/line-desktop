#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
target_arch=${1:-amd64}
case $target_arch in amd64|arm64) ;; *) echo 'Usage: scripts/package.sh [amd64|arm64]' >&2; exit 1 ;; esac
scripts/build-core.sh >/dev/null
case $(uname -m) in x86_64) host_arch=amd64 ;; *) host_arch=arm64 ;; esac
cache_base=${XDG_CACHE_HOME:-$HOME/.cache}
[[ $cache_base = /* ]] || cache_base=$HOME/.cache
go_command=$cache_base/matrix-line/toolchains/go1.26.10-linux-$host_arch/bin/go
staging=$(mktemp -d "$project_dir/.build/package.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
mkdir -p "$staging/matrix-line/bin" dist
tar --exclude='__pycache__' --exclude='*.pyc' -cf - \
    line-app README.md THIRD_PARTY.md index.html core desktop docs scripts |
    tar -xf - -C "$staging/matrix-line"
echo "建置 Linux $target_arch 可攜套件…" >&2
(cd core && CGO_ENABLED=0 GOOS=linux GOARCH="$target_arch" GOTOOLCHAIN=local \
    "$go_command" build -mod=readonly -trimpath -ldflags='-s -w' \
    -o "$staging/matrix-line/bin/line-core-linux-$target_arch" .)
archive=matrix-line-preview-linux-$target_arch.tar.gz
tar -czf "dist/$archive" -C "$staging" matrix-line
(cd dist && sha256sum "$archive" >"$archive.sha256")
printf '%s\n' "$project_dir/dist/$archive"
