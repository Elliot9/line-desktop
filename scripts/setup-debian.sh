#!/usr/bin/env bash
set -euo pipefail

if [[ $(uname -s) != Linux ]]; then
    echo '此啟動程式適用於 Ubuntu／Debian Linux。' >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo '找不到 /etc/os-release，請手動安裝 docs/GUIDE.zh-TW.md「其他發行版」列出的套件。' >&2
    exit 1
fi
source /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
    *ubuntu*|*debian*) ;;
    *) echo '自動安裝目前只支援 Ubuntu／Debian。其他發行版請參考 docs/GUIDE.zh-TW.md。' >&2; exit 1 ;;
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
# Videos in the viewer: the MP4 demuxer and the H.264/AAC decoders LINE uses,
# and GTK's GStreamer backend, a package of its own on older releases (newer
# GTK has it built in).
packages+=(gstreamer1.0-plugins-good gstreamer1.0-libav)
echo '安裝 GTK 4、Python、系統鑰匙圈、字型與影片播放元件；首次安裝可能需要 sudo 密碼。' >&2
"${apt_command[@]}" update
if apt-cache show libgtk-4-media-gstreamer >/dev/null 2>&1; then
    packages+=(libgtk-4-media-gstreamer)
fi
"${apt_command[@]}" install -y --no-install-recommends "${packages[@]}"

