#!/usr/bin/env python3
import os
import subprocess
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
# GNOME matches a running window to its launcher by the GTK application id
# (Wayland app_id / X11 WM_CLASS), so the .desktop file and the icon are
# named after it; that is what makes the dock show the LINE icon instead
# of the generic gear.
icon_source = root / 'desktop/assets/app-icon.svg'
icons = data / 'icons/hicolor/scalable/apps'
icons.mkdir(parents=True, exist_ok=True)
applications = data / 'applications'
applications.mkdir(parents=True, exist_ok=True)
launcher = desktop_quote(root / 'line-app')
entries = {
    'org.matrixline.Desktop.Live': ('LINE', launcher, False),
    'org.matrixline.Desktop.Demo': ('LINE（離線展示）', launcher + ' --demo', True),
}
for app_id, (name, command, hidden) in entries.items():
    (icons / f'{app_id}.svg').write_bytes(icon_source.read_bytes())
    lines = [
        '[Desktop Entry]',
        'Type=Application',
        f'Name={name}',
        'Comment=非官方 LINE GTK 4 用戶端',
        f'Exec={command}',
        f'Icon={app_id}',
        'Terminal=false',
        'Categories=Network;InstantMessaging;Chat;',
        'Keywords=LINE;chat;message;聊天;',
        f'StartupWMClass={app_id}',
        'StartupNotify=true',
        'DBusActivatable=false',
    ]
    if hidden:
        lines.append('NoDisplay=true')
    else:
        lines += ['Actions=demo;', '', '[Desktop Action demo]', 'Name=離線展示', f'Exec={launcher} --demo']
    (applications / f'{app_id}.desktop').write_text('\n'.join(lines) + '\n', encoding='utf-8')

# Earlier versions installed a launcher under another name; it would show
# up as a second LINE in the app grid.
old = applications / 'io.github.matrixline.desktop'
if old.exists():
    old.unlink()

for command in (['update-desktop-database', str(applications)],
                ['gtk-update-icon-cache', '-f', '-t', str(data / 'icons/hicolor')]):
    try:
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
print(f'已加入應用程式選單：{applications / "org.matrixline.Desktop.Live.desktop"}')
