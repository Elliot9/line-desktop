import argparse
import os
import sys

from .launcher import LIVE_ID


def quit_running():
    """結束 LINE from the dock: closing the window only hides it (LINE keeps
    receiving messages), so the launcher's menu asks the running instance
    to quit. Does nothing when LINE is not running."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return 1
    app = Gio.Application(application_id=LIVE_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    try:
        app.register(None)
    except GLib.Error:
        return 1
    if app.get_is_remote():
        app.activate_action("quit", None)
        connection = app.get_dbus_connection()
        if connection is not None:
            connection.flush_sync(None)
    return 0


def main():
    parser = argparse.ArgumentParser(description="LINE GTK 桌面程式")
    parser.add_argument("--demo", action="store_true", help="離線展示，不連接 LINE")
    parser.add_argument("--quit", action="store_true", help="結束執行中的 LINE")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="操作真實 GTK 元件並儲存測試結果")
    options = parser.parse_args()
    if options.quit:
        return quit_running()
    # Notifications go through org.freedesktop.Notifications rather than
    # GLib's default org.gtk.Notifications on GNOME: GNOME saves the latter
    # and restores them at every login, so the dock showed an unread count
    # before LINE was opened. Freedesktop notifications are dropped when
    # LINE exits. Sandboxes need their portal, so they keep the default.
    if not (os.environ.get("FLATPAK_ID") or os.environ.get("SNAP")):
        os.environ.setdefault("GNOTIFICATION_BACKEND", "freedesktop")
    try:
        from .app import Application
    except (ImportError, ValueError) as error:
        print("需要 Linux 系統套件 python3-gi、gir1.2-gtk-4.0。請以系統 /usr/bin/python3 執行。", file=sys.stderr)
        return 1
    return Application(options).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
