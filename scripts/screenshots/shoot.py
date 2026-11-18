#!/usr/bin/python3
"""Runs the real GTK app against scenario.py and renders README screenshots.
Started by shoot.sh, which sets up a private display, D-Bus session and data
folders; see that file."""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gtk

from desktop import pictures
from desktop.app import Application

PHOTOS = Path(os.environ["SCREENSHOT_PHOTOS"])
OUT = Path(os.environ["SCREENSHOT_OUT"])
SCALE = int(os.environ.get("SCREENSHOT_SCALE", "2"))
# The theme is chosen at start (shoot.sh runs once per theme): switching it
# later does not restyle the window on a display with no viewer attached.
DARK = os.environ.get("SCREENSHOT_THEME") == "dark"
WIDTH, HEIGHT = 1120, 720
PREFIX = "https://profile.line-scdn.net/screenshot/"

# Profile pictures come from the photo folder instead of LINE's CDN.
real_fetch = pictures.Pictures.fetch


def local_fetch(self, url):
    if url.startswith(PREFIX):
        return (PHOTOS / (url[len(PREFIX):] + ".jpg")).read_bytes()
    return real_fetch(self, url)


pictures.Pictures.fetch = local_fetch

app = Application(argparse.Namespace(demo=False, smoke_test=None))
started = time.monotonic()


def pump(seconds):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not context.iteration(False):
            time.sleep(0.005)


def capture(name):
    window = app.window
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    snapshot.scale(SCALE, SCALE)
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width * SCALE, height * SCALE))
    OUT.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(OUT / f"{name}.png"))
    print(f"{name}.png {width * SCALE}x{height * SCALE}", flush=True)


def chat_named(name):
    return next(c["id"] for c in app.state.chats.values() if c.get("name") == name)


def run():
    if app.state.session != "ready" or len(app.state.chats) < 5:
        return time.monotonic() - started < 30
    window = app.window
    window.set_default_size(WIDTH, HEIGHT)
    window.set_size_request(WIDTH, HEIGHT)
    pump(1)
    hike = chat_named("週末爬山小隊")

    app.select_chat(hike)
    pump(3)  # history, avatars and photo previews arrive
    app.scroll_bottom()
    pump(0.5)
    if DARK:
        capture("chat-dark")
        app.quit()
        return False
    capture("chat-light")

    # Photo viewer on the trail photo.
    trail = next(m for m in app.state.messages[hike] if m.get("kind") == "image" and int(m.get("height", 0)) == 960)
    app.open_viewer(trail)
    pump(2.5)
    capture("viewer")
    app.close_modal()
    pump(0.3)

    # Friends page with a profile card.
    app.navigate("friends")
    pump(1.5)
    yating = next(c["id"] for c in app.state.contacts.values() if c["name"] == "張雅婷")
    app.open_profile(yating)
    pump(1.5)
    capture("friends")
    app.close_modal()

    # A family chat with the info panel open.
    app.navigate("chats")
    app.select_chat(chat_named("陳家 🏠"))
    pump(2)
    if not app.details_open:
        app.toggle_details()
    pump(2)
    app.scroll_bottom()
    pump(0.5)
    capture("details")

    app.quit()
    return False


GLib.timeout_add(300, run)
raise SystemExit(app.run([sys.argv[0]]))
