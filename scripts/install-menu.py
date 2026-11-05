#!/usr/bin/env python3
import os
from pathlib import Path


def desktop_quote(value):
    value = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value + '"'


root = Path(__file__).resolve().parent.parent
data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
if not data.is_absolute():
    data = Path.home() / '.local/share'
destination = data / 'applications' / 'io.github.matrixline.desktop'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    '[Desktop Entry]\n'
    'Type=Application\n'
    'Name=LINE for Linux\n'
    'Comment=非官方 LINE GTK 4 用戶端\n'
    f'Exec={desktop_quote(root / "line-app")}\n'
    f'Icon={root / "desktop/assets/app-icon.svg"}\n'
    'Terminal=false\n'
    'Categories=Network;InstantMessaging;\n'
    'StartupNotify=true\n',
    encoding='utf-8',
)
print(f'已加入應用程式選單：{destination}')
