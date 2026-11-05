#!/usr/bin/env bash
set -euo pipefail

if [[ $(uname -s) != Linux ]]; then
    echo '此啟動程式適用於 Ubuntu／Debian Linux。' >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo '找不到 /etc/os-release，請手動安裝 README 中的 GTK 套件。' >&2
    exit 1
fi
source /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
    *ubuntu*|*debian*) ;;
    *) echo '自動安裝目前只支援 Ubuntu／Debian。請參考 README。' >&2; exit 1 ;;
esac

packages=(python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0
    libsecret-tools gnome-keyring ca-certificates curl fonts-noto-cjk
    fonts-noto-color-emoji librsvg2-common util-linux)
apt_command=(apt-get)
if [[ $EUID != 0 ]]; then
    if ! command -v sudo >/dev/null; then
        echo '請由管理員執行 scripts/setup-debian.sh 安裝相依套件。' >&2
        exit 1
    fi
    apt_command=(sudo apt-get)
fi
echo '安裝 GTK 4、Python、系統鑰匙圈與字型；首次安裝可能需要 sudo 密碼。' >&2
"${apt_command[@]}" update
"${apt_command[@]}" install -y --no-install-recommends "${packages[@]}"

