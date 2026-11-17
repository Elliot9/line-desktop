"""The application-menu launcher (.desktop file) and icon for this folder.

GNOME matches a running window to its launcher by the GTK application id
(Wayland app_id / X11 WM_CLASS), so the .desktop file and the icon are named
after it; that is what makes the dock show the LINE icon instead of the
generic gear. Imported without GTK (scripts/install-menu.py)."""
import os
from pathlib import Path
import subprocess

LIVE_ID = "org.matrixline.Desktop.Live"
DEMO_ID = "org.matrixline.Desktop.Demo"
ROOT = Path(__file__).resolve().parent.parent


def desktop_quote(value):
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def data_home():
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return data if data.is_absolute() else Path.home() / ".local/share"


# GNOME Shell reloads a changed launcher only when its Name, Comment,
# Exec, Icon or NoDisplay differs; a change to the dock menu (Actions)
# alone stays unseen until the next login. So whenever the actions
# change, change this too.
COMMENT = "非官方 LINE 桌面用戶端（GTK 4）"


def entries(root=ROOT):
    """The .desktop files by application id. The demo has its own (hidden)
    entry only so its window gets the icon; the dock menu of LINE itself
    offers 結束 LINE, since closing the window keeps LINE running."""
    launcher = desktop_quote(Path(root) / "line-app")
    files = {}
    for app_id, name, command, hidden in ((LIVE_ID, "LINE", launcher, False),
                                          (DEMO_ID, "LINE（離線展示）", launcher + " --demo", True)):
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={name}",
            f"Comment={COMMENT}",
            f"Exec={command}",
            f"Icon={app_id}",
            "Terminal=false",
            "Categories=Network;InstantMessaging;Chat;",
            "Keywords=LINE;chat;message;聊天;",
            f"StartupWMClass={app_id}",
            "StartupNotify=true",
            "DBusActivatable=false",
        ]
        if hidden:
            lines.append("NoDisplay=true")
        else:
            lines += ["Actions=quit;", "", "[Desktop Action quit]", "Name=結束 LINE", f"Exec={launcher} --quit"]
        files[app_id] = "\n".join(lines) + "\n"
    return files


def install(root=ROOT):
    """Writes the launchers and icons; returns the path of LINE's launcher."""
    data = data_home()
    icon = (Path(root) / "desktop/assets/app-icon.svg").read_bytes()
    icons = data / "icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    applications = data / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    for app_id, text in entries(root).items():
        (icons / f"{app_id}.svg").write_bytes(icon)
        (applications / f"{app_id}.desktop").write_text(text, encoding="utf-8")
    # Earlier versions installed a launcher under another name; it would
    # show up as a second LINE in the app grid.
    old = applications / "io.github.matrixline.desktop"
    if old.exists():
        old.unlink()
    for command in (["update-desktop-database", str(applications)],
                    ["gtk-update-icon-cache", "-f", "-t", str(data / "icons/hicolor")]):
        try:
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    return applications / f"{LIVE_ID}.desktop"


def refresh(root=ROOT):
    """Brings an installed launcher up to date after an update changed it
    (such as its dock menu), but only one installed from this folder: a
    copy elsewhere keeps its own. Returns True when it was rewritten."""
    applications = data_home() / "applications"
    wanted = entries(root)
    current = {}
    for app_id in wanted:
        try:
            current[app_id] = (applications / f"{app_id}.desktop").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current[app_id] = ""
    if f"\nExec={desktop_quote(Path(root) / 'line-app')}\n" not in current[LIVE_ID] or current == wanted:
        return False
    try:
        install(root)
    except OSError:
        return False
    return True
