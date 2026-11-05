#!/usr/bin/env bash
set -euo pipefail
umask 077

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $(uname -s) != Linux ]]; then
    echo '請在 Linux 執行此建置腳本。開發者可在 core 目錄直接使用 go build。' >&2
    exit 1
fi
case $(uname -m) in
    x86_64) go_arch=amd64; go_sha=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5 ;;
    aarch64|arm64) go_arch=arm64; go_sha=3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797 ;;
    *) echo '目前啟動包支援 x86_64 與 ARM64。' >&2; exit 1 ;;
esac

cache_base=${XDG_CACHE_HOME:-$HOME/.cache}
[[ $cache_base = /* ]] || cache_base=$HOME/.cache
toolchain_dir=$cache_base/matrix-line/toolchains
go_version=1.26.10
go_root=$toolchain_dir/go$go_version-linux-$go_arch
mkdir -p "$toolchain_dir" "$project_dir/.build"
exec 9>"$toolchain_dir/build.lock"
flock 9

if [[ ! -x $go_root/bin/go ]]; then
    echo "首次啟動：下載 Go $go_version 通訊核心建置工具（安裝至使用者快取）。" >&2
    staging=$(mktemp -d "$toolchain_dir/.download.XXXXXX")
    trap 'rm -rf -- "$staging"' EXIT
    archive=go$go_version.linux-$go_arch.tar.gz
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        "https://go.dev/dl/$archive" -o "$staging/$archive"
    (cd "$staging" && printf '%s  %s\n' "$go_sha" "$archive" | sha256sum --check --status)
    tar -xzf "$staging/$archive" -C "$staging"
    mv -- "$staging/go" "$go_root"
    rm -rf -- "$staging"
    trap - EXIT
fi

binary=$project_dir/.build/line-core-linux-$go_arch
fingerprint=$(
    cd "$project_dir/core"
    { printf '%s\n' "$go_version" "$go_arch";
      find . -type f \( -name '*.go' -o -name 'go.mod' -o -name 'go.sum' \) -print0 |
          sort -z | xargs -0 sha256sum; } | sha256sum | cut -d ' ' -f 1
)
stamp=$binary.sha256
if [[ ! -x $binary || ! -f $stamp || $(cat "$stamp") != "$fingerprint" ]]; then
    echo '建置 LINE 通訊核心…' >&2
    temp_binary=$(mktemp "$project_dir/.build/.line-core.XXXXXX")
    trap 'rm -f -- "$temp_binary"' EXIT
    (cd "$project_dir/core" && CGO_ENABLED=0 GOTOOLCHAIN=local "$go_root/bin/go" build \
        -mod=readonly -trimpath -ldflags='-s -w' -o "$temp_binary" .)
    chmod 700 "$temp_binary"
    mv -- "$temp_binary" "$binary"
    printf '%s\n' "$fingerprint" >"$stamp"
    trap - EXIT
fi
printf '%s\n' "$binary"

