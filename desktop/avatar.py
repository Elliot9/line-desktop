"""Circular profile pictures as LINE draws them: the real photo when the
account has one, a grey silhouette when not, and a collage for groups."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk

from .art import texture as art_texture

# Set by the application: Pictures loader and a callable(person_id) -> dict.
LOADER = None
TEXTURES = {}
WAITING = {}
SILHOUETTE = (
    "0 0 80 80", "xMidYMid slice",
    '<rect width="80" height="80" fill="#d5d9e0"/>'
    '<circle cx="40" cy="31" r="14" fill="#fff"/>'
    '<path d="M13 76c2-17 13-26 27-26s25 9 27 26Z" fill="#fff"/>',
)


def silhouette(size):
    from . import art
    art.EXTRA["silhouette"] = SILHOUETTE
    return art_texture("silhouette", None, size, size, None)


def request(url, widget):
    """Loads url once; every widget waiting for it redraws when it arrives."""
    if url in TEXTURES:
        return TEXTURES[url]
    waiting = WAITING.get(url)
    if waiting is not None:
        waiting.append(widget)
        return None
    WAITING[url] = [widget]

    def loaded(data):
        try:
            paint = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            paint = None
        if len(TEXTURES) > 400:
            TEXTURES.pop(next(iter(TEXTURES)))
        TEXTURES[url] = paint
        for item in WAITING.pop(url, []):
            item.queue_draw()
    if LOADER is None or not LOADER.load(url, loaded, failed=lambda: WAITING.pop(url, None)):
        WAITING.pop(url, None)
        TEXTURES[url] = None
    return None


class Avatar(Gtk.Widget):
    """sources: list of (kind, value) where kind is url | art | default.
    One source fills the circle; 2–4 are tiled like LINE's group collage."""

    __gtype_name__ = "LineAvatar"

    def __init__(self, sources, size=43, css=None):
        super().__init__()
        self.sources = sources[:4] or [("default", None)]
        self.size = size
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_can_target(False)
        self.add_css_class("avatar")
        for name in (css or "").split():
            self.add_css_class(name)

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def paint_for(self, source, pixels):
        kind, value = source
        if kind == "url" and value:
            paint = request(value, self)
            if paint is not None:
                return paint
            return silhouette(pixels)
        if kind == "art":
            return art_texture("portrait", value, pixels, pixels, None)
        return silhouette(pixels)

    def do_snapshot(self, snapshot):
        size = min(self.get_width(), self.get_height())
        if size <= 0:
            return
        scale = max(1, self.get_scale_factor())
        bounds = Graphene.Rect().init(0, 0, size, size)
        corner = Graphene.Size().init(size / 2, size / 2)
        clip = Gsk.RoundedRect()
        clip.init(bounds, corner, corner, corner, corner)
        snapshot.push_rounded_clip(clip)
        cells = self.layout(len(self.sources), size)
        for source, (x, y, w, h) in zip(self.sources, cells):
            paint = self.paint_for(source, int(max(w, h) * scale))
            # Cover-fit the picture inside its cell.
            pw, ph = paint.get_width(), paint.get_height()
            ratio = max(w / pw, h / ph)
            dw, dh = pw * ratio, ph * ratio
            snapshot.push_clip(Graphene.Rect().init(x, y, w, h))
            snapshot.append_texture(paint, Graphene.Rect().init(x + (w - dw) / 2, y + (h - dh) / 2, dw, dh))
            snapshot.pop()
        snapshot.pop()

    @staticmethod
    def layout(count, size):
        half = size / 2
        gap = 1
        if count == 1:
            return [(0, 0, size, size)]
        if count == 2:
            return [(0, 0, half - gap, size), (half + gap, 0, half - gap, size)]
        if count == 3:
            return [(0, 0, half - gap, size), (half + gap, 0, half - gap, half - gap), (half + gap, half + gap, half - gap, half - gap)]
        return [(0, 0, half - gap, half - gap), (half + gap, 0, half - gap, half - gap),
                (0, half + gap, half - gap, half - gap), (half + gap, half + gap, half - gap, half - gap)]
