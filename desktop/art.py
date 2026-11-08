"""Artwork shared with index.html: stroke icons, illustrated avatars, scenes and stickers.

The SVG sources are ported verbatim from the reference design so the GTK client
draws the same pictures. Rendering goes through GdkPixbuf's SVG loader and is
cached per size, colour and scale factor.
"""
from functools import lru_cache

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, Graphene, Gtk

ICON_PATHS = {
    # Filled rail glyphs, drawn like LINE's navigation icons.
    "rail-friends": '<circle cx="12" cy="7.6" r="4.4" fill="currentColor" stroke="none"/><path d="M3.6 20.6c.5-4.6 4-7.4 8.4-7.4s7.9 2.8 8.4 7.4Z" fill="currentColor" stroke="none"/>',
    "rail-chats": '<path d="M12 3.4c5 0 9 3.3 9 7.5s-4 7.5-9 7.5c-.9 0-1.7-.1-2.5-.3L5 20.6l.7-3.6C4 15.6 3 13.4 3 10.9c0-4.2 4-7.5 9-7.5Z" fill="currentColor" stroke="none"/><path d="M8 11h.01M12 11h.01M16 11h.01" stroke="#202a43" stroke-width="2.2"/>',
    "rail-add": '<circle cx="10" cy="7.6" r="4.2" fill="currentColor" stroke="none"/><path d="M2.4 20.6c.5-4.5 3.7-7.2 7.6-7.2 1.9 0 3.6.6 4.9 1.7v5.5Z" fill="currentColor" stroke="none"/><path d="M18.5 13.5v7M15 17h7" stroke-width="2"/>',
    "more-v": '<path d="M12 5h.01M12 12h.01M12 19h.01" stroke-width="2.6"/>',
    "clip": '<path d="m20 11.5-8 8a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
    "dots": '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="2.6"/>',
    "chat": '<path d="M20 11.3a8 8 0 0 1-8.2 7.8 10.3 10.3 0 0 1-3.4-.6L4 20l.8-4.1A7.2 7.2 0 0 1 3.5 11c0-4.3 3.7-7.8 8.3-7.8S20 6.9 20 11.3Z"/><path d="M7.7 10.9h.1m4 0h.1m4 0h.1"/>',
    "friends": '<circle cx="9" cy="7.5" r="3.2"/><path d="M2.7 20v-2.2a6.3 6.3 0 0 1 12.6 0V20M16 4.7a3.2 3.2 0 0 1 0 6.2M18 14a5 5 0 0 1 3 4.8V20"/>',
    "phone": '<path d="m7.7 3 2.1 4.5-2.2 2.4a15 15 0 0 0 6.7 6.7l2.4-2.2 4.3 2.2c-.2 2.5-1.4 4.4-3.8 4.4C10.8 21 3 13.2 3 6.8 3 4.4 5.1 3.1 7.7 3Z"/>',
    "video": '<rect x="2.5" y="5.5" width="13" height="13" rx="3"/><path d="m15.5 10 6-3v10l-6-3"/>',
    "bookmark": '<path d="M6 3h12v18l-6-4-6 4V3Z"/><path d="M9 8h6m-6 4h6"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 8.8a2.5 2.5 0 1 1 4 2c-1.3.8-1.6 1.4-1.6 2.7M12 17h.01"/>',
    "settings": '<path d="m9.7 3-.7 2.2-2.2 1-2.3-.4L2.9 8.6l1.5 1.8v2.5L3 14.7l1.7 2.8 2.2-.4 2.2 1 .7 2.2h3.3l.7-2.2 2.2-1 2.3.4 1.7-2.8-1.5-1.8v-2.5l1.5-1.8-1.7-2.8-2.3.4-2.2-1-.7-2.2H9.7Z"/><circle cx="11.4" cy="11.7" r="3.1"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "plus": '<path d="M12 4v16M4 12h16"/>',
    "edit": '<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6Z"/>',
    "chevron": '<path d="m9 5 7 7-7 7"/>',
    "back": '<path d="m15 5-7 7 7 7"/>',
    "x": '<path d="m6 6 12 12M6 18 18 6"/>',
    "more": '<circle cx="5" cy="12" r=".8"/><circle cx="12" cy="12" r=".8"/><circle cx="19" cy="12" r=".8"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 10v7m0-10h.01"/>',
    "bell": '<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 7H4c0-1 2-2 2-7ZM9 20h6"/>',
    "belloff": '<path d="m3 3 18 18M9.4 3.6A6 6 0 0 1 18 9c0 2.1.4 3.5.9 4.5M6 6.5A6 6 0 0 0 6 9c0 5-2 6-2 7h12M9 20h6"/>',
    "pin": '<path d="m9 3 12 12-5-1-4 4-1-5-6-6 4-4ZM11 13l-8 8M13 7l4-4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m4 17 5-5 4 4 3-3 5 5"/>',
    "file": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6M8 13h8m-8 4h5"/>',
    "smile": '<circle cx="12" cy="12" r="9"/><path d="M8 14a4 4 0 0 0 8 0M8 9h.01M16 9h.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "crop": '<path d="M7 3v14h14M3 7h14v14M3 3l18 18"/>',
    "send": '<path d="m3 3 19 9-19 9 4-9-4-9Zm4 9h15"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    "check": '<path d="m5 12 4 4L20 5"/>',
    "mic": '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 10a6.5 6.5 0 0 0 13 0M12 17v4m-4 0h8"/>',
    "speaker": '<path d="m3 9 5 0 5-5v16l-5-5H3V9Zm13-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M12 17v4m-5 0h10m-9-11 4-4 4 4m-4-4v8"/>',
    "heart": '<path d="M20 5.8c-2.5-2.7-6.4-1.7-8 1.1C10.4 4.1 6.5 3.1 4 5.8c-4 4.3 2.6 10.7 8 14.2 5.4-3.5 12-9.9 8-14.2Z"/>',
    "star": '<path d="m12 2 3 6 6.7 1-4.8 4.8 1.1 6.7-6-3.2-6 3.2 1.1-6.7L2.3 9 9 8l3-6Z"/>',
    "reply": '<path d="m9 5-7 7 7 7v-5c6-1 10 0 13 5 0-8-5-11-13-10V5Z"/>',
    "copy": '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 5V3H3v13h2"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    "forward": '<path d="m14 4 7 7-7 7v-5c-6-1-9 0-12 5 0-8 6-11 12-10V4Z"/>',
    "history": '<path d="M3 7V2m0 5h5M3 7a9 9 0 1 1-.5 9M12 7v5l3 2"/>',
    "logout": '<path d="M10 3H4v18h6m5-14 5 5-5 5M8 12h12"/>',
    "folder": '<path d="M3 6h7l2 2h9v12H3V6Z"/>',
    "cloud": '<path d="M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5.5 5.5 0 0 1 0 11H6Z"/>',
}

PORTRAIT_COLORS = [
    ("#ecdccf", "#785b48", "#4b6552"),
    ("#e7dcc9", "#443d35", "#d9ae78"),
    ("#dbe6df", "#4e5547", "#829f87"),
    ("#e4e0ed", "#6e4d45", "#b2a4c3"),
    ("#e4e9d8", "#6f5d45", "#9db879"),
    ("#dce7ec", "#454b54", "#83a6ba"),
]
MOODS = ["出發！", "收到 ♡", "好期待", "謝謝你", "早安", "一起加油", "哈哈哈", "晚安"]
EMOJI = ["😊", "🥰", "😂", "😍", "🥺", "😎", "😴", "🙌", "👍", "👏", "💚", "✨", "🌿", "☀️", "☕", "🎉", "🐻", "🌸", "🍰", "🎧", "🚶"]


def icon(name):
    paths = ICON_PATHS.get(name, ICON_PATHS["chat"])
    return "0 0 24 24", "xMidYMid meet", (
        '<g fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</g>'
    )


def portrait(index=0):
    bg, hair, shirt = PORTRAIT_COLORS[index % len(PORTRAIT_COLORS)]
    glasses = (
        '<g fill="none" stroke="#6f6758" stroke-width="1.5"><rect x="27" y="31" width="12" height="10" rx="4"/>'
        '<rect x="44" y="31" width="12" height="10" rx="4"/><path d="M39 35h5"/></g>'
        if index % 3 == 1 else ""
    )
    return "0 0 80 80", "xMidYMid slice", (
        f'<rect width="80" height="80" fill="{bg}"/><path d="M12 80c0-20 11-31 28-31s29 11 29 31" fill="{shirt}"/>'
        f'<path d="M20 35c0-20 10-26 21-26 16 0 23 11 21 29l-3 14H21Z" fill="{hair}"/><path d="M34 49h13v14H34Z" fill="#e8b99a"/>'
        '<ellipse cx="41" cy="35" rx="17" ry="21" fill="#f4d3b5"/>'
        f'<path d="M22 30c0-17 8-22 20-22 14 0 23 11 21 23-10 0-16-5-21-12-5 7-11 10-20 11Z" fill="{hair}"/>'
        '<circle cx="35" cy="36" r="1.25" fill="#6a5142"/><circle cx="48" cy="36" r="1.25" fill="#6a5142"/>'
        '<path d="M38 45q4 3 8-1" fill="none" stroke="#b27c69" stroke-width="1.3" stroke-linecap="round"/>'
        f'{glasses}<path d="m29 60 12 10 12-10" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="2"/>'
    )


def group_picture():
    return "0 0 80 80", "xMidYMid slice", (
        '<rect width="80" height="80" fill="#dfead5"/><circle cx="61" cy="20" r="8" fill="#f2dfa5"/>'
        '<path d="m-7 64 32-37 26 30 14-22 23 29v16H0" fill="#a6bf8c"/><path d="M5 80V62c10-13 21-11 27 1l3 17" fill="#6f9672"/>'
        '<path d="m31 80 2-20c8-14 23-12 30 2l3 18" fill="#8da879"/><circle cx="19" cy="45" r="11" fill="#f0d7b9"/>'
        '<circle cx="48" cy="43" r="12" fill="#f2d5b2"/><path d="M8 44c-1-18 23-19 23 1-8 0-12-6-12-6-3 5-7 5-11 5Z" fill="#655a45"/>'
        '<path d="M36 40c0-16 26-16 25 4-10-1-13-8-13-8-3 5-8 5-12 4Z" fill="#7a654b"/>'
        '<path d="m17 48 1 0m5 0h1m21-2h1m6 0h1" stroke="#6e5743" stroke-width="2" stroke-linecap="round"/>'
        '<path d="M62 80c-1-11-5-14-7-19" stroke="#e7dfbd" stroke-width="3"/>'
    )


def scene(kind="mountain"):
    if kind == "coffee":
        return "0 0 200 150", "xMidYMid slice", (
            '<rect width="200" height="150" fill="#d9c5a0"/><rect x="130" width="70" height="110" fill="#b8c8a6"/>'
            '<path d="M130 0v110m35-110v110M130 45h70" stroke="#f8f1dc" stroke-width="7"/><path d="M0 105h200v45H0" fill="#9f7f58"/>'
            '<ellipse cx="82" cy="128" rx="49" ry="8" fill="#705d44" fill-opacity=".25"/><ellipse cx="79" cy="116" rx="39" ry="9" fill="#e8dfca"/>'
            '<path d="M50 72h59l-5 33c-2 17-46 17-49 0Z" fill="#fbf6e8"/><path d="M109 78c28-3 25 27-2 24" fill="none" stroke="#f5efdf" stroke-width="8"/>'
            '<ellipse cx="79" cy="72" rx="30" ry="9" fill="#e4dac6"/><ellipse cx="79" cy="72" rx="24" ry="6" fill="#775843"/>'
            '<path d="M72 44c-10-11 10-13 1-25m15 27c-10-11 10-13 1-25" stroke="#f8f3dc" fill="none" stroke-width="3" stroke-linecap="round"/>'
            '<path d="M20 50c-6-15 0-25 6-30 6 16 4 26-6 30Z" fill="#71855a"/>'
        )
    if kind == "trail":
        return "0 0 200 150", "xMidYMid slice", (
            '<rect width="200" height="150" fill="#b6ccad"/><path d="M0 100 65 40l45 60 55-78 35 66v62H0" fill="#8eae88"/>'
            '<path d="M102 150c-50-45 28-49-3-72 54 24-30 37 51 72" fill="#d7c69e"/>'
            '<g stroke="#607d58" stroke-width="7"><path d="M20 0v140M65 0v100M165 0v140M192 0v150"/></g>'
            '<g fill="#6c8c62"><ellipse cx="20" cy="22" rx="37" ry="50"/><ellipse cx="66" cy="3" rx="29" ry="50"/>'
            '<ellipse cx="163" cy="10" rx="40" ry="51"/><ellipse cx="191" cy="48" rx="25" ry="45"/></g>'
            '<g fill="#d5e0a8" opacity=".6"><circle cx="96" cy="41" r="6"/><circle cx="119" cy="62" r="4"/><circle cx="87" cy="80" r="3"/></g>'
        )
    return "0 0 270 220", "xMidYMid slice", (
        '<rect width="270" height="220" fill="#c8dfde"/><circle cx="209" cy="43" r="17" fill="#f7e9b9"/>'
        '<path d="M-20 118 59 35l62 70 56-58 103 86v87H0" fill="#94b4a3"/><path d="m38 58 21-23 30 34-28-11-9 7Z" fill="#d4e2d7"/>'
        '<path d="M-20 141 74 74l85 80 52-59 82 59v66H0" fill="#6e9b7d"/><path d="M-20 181c85-77 178-56 310-17v66H0" fill="#a7bb7c"/>'
        '<path d="M0 186c110-44 148 42 270-2v36H0" fill="#8eab6c"/><path d="M107 220c-4-34 42-39 25-63 31 23 1 41 10 63" fill="#dfcfaa"/>'
        '<g stroke="#6f8852" stroke-width="1.3"><path d="m24 188 2-18m-2 12-6-6m7 0 5-7m180 30 2-18m-2 12-6-6m7 0 5-7"/></g>'
        '<path d="M55 27h20m-10-4v8" stroke="#f3f7ec" stroke-width="2" stroke-linecap="round" opacity=".7"/>'
    )


def bear(index=0):
    mood = index % 8
    extra = ""
    if mood in (1, 2):
        extra += '<path d="M116 28c-9-14-21-2-12 7l12 10 12-10c9-9-3-21-12-7" fill="#e6a29a"/>'
    if mood == 4:
        extra += ('<g stroke="#dbc47d" stroke-width="2"><circle cx="119" cy="20" r="9" fill="#f3db97"/>'
                  '<path d="M119 5V0m15 20h5m-20 15v5m-15-20h-5"/></g>')
    if mood == 7:
        extra += ('<path d="M119 6c-20 12-8 31 7 25-20 25-43-13-7-25Z" fill="#dfcd8c"/>'
                  '<text x="111" y="50" fill="#a8b5a0" font-size="13" font-family="sans-serif">z z</text>')
    if mood == 6:
        eyes = '<path d="m51 45 6-4 6 4m20 0 6-4 6 4" fill="none" stroke="#594a3d" stroke-width="2.5" stroke-linecap="round"/>'
    elif mood == 7:
        eyes = '<path d="M52 46q5 4 10 0m21 0q5 4 10 0" fill="none" stroke="#594a3d" stroke-width="2" stroke-linecap="round"/>'
    else:
        eyes = '<ellipse cx="57" cy="45" rx="2.4" ry="3.3" fill="#594a3d"/><ellipse cx="89" cy="45" rx="2.4" ry="3.3" fill="#594a3d"/>'
    raised = mood in (0, 5)
    arrow = ('<path d="M17 30h15m-10-8 10 8-10 8" stroke="#86ad78" fill="none" stroke-width="3" stroke-linecap="round"/>'
             if mood == 0 else "")
    return "0 0 150 135", "xMidYMid meet", (
        f'<g transform="translate(0,5)">{extra}<ellipse cx="74" cy="102" rx="37" ry="7" fill="#657a53" fill-opacity=".125"/>'
        '<ellipse cx="73" cy="80" rx="28" ry="30" fill="#b89d7f"/><ellipse cx="73" cy="81" rx="17" ry="22" fill="#ead9bd"/>'
        '<circle cx="45" cy="28" r="13" fill="#b89d7f"/><circle cx="100" cy="28" r="13" fill="#b89d7f"/>'
        '<circle cx="45" cy="28" r="7" fill="#e0c6a6"/><circle cx="100" cy="28" r="7" fill="#e0c6a6"/>'
        '<ellipse cx="73" cy="48" rx="37" ry="30" fill="#c1a687"/><ellipse cx="73" cy="59" rx="19" ry="14" fill="#f0e2cc"/>'
        f'{eyes}<ellipse cx="73" cy="54" rx="4" ry="3" fill="#594a3d"/>'
        '<path d="M73 56v5m-7-1q7 8 14 0" stroke="#715746" stroke-width="1.6" fill="none" stroke-linecap="round"/>'
        '<ellipse cx="48" cy="57" rx="6" ry="3.7" fill="#dfaa92"/><ellipse cx="98" cy="57" rx="6" ry="3.7" fill="#dfaa92"/>'
        f'<path d="M48 79 30 {"54" if raised else "84"}M98 79l19 {"-25" if raised else "5"}" stroke="#b89d7f" stroke-width="13" stroke-linecap="round"/>'
        f'<path d="m57 101-6 6m39-6 6 6" stroke="#b89d7f" stroke-width="13" stroke-linecap="round"/>{arrow}</g>'
        f'<text x="75" y="132" fill="#778967" text-anchor="middle" font-size="14" font-weight="600" '
        f'font-family="Noto Sans CJK TC, sans-serif" letter-spacing="1">{MOODS[mood]}</text>'
    )


EXTRA = {}


def source(kind, value=None):
    if kind in EXTRA:
        return EXTRA[kind]
    if kind == "icon":
        return icon(value)
    if kind == "portrait":
        return portrait(int(value or 0))
    if kind == "group":
        return group_picture()
    if kind == "scene":
        return scene(value or "mountain")
    if kind == "bear":
        return bear(int(value or 0))
    raise ValueError("unknown artwork " + str(kind))


def portrait_index(identifier):
    """Stable illustrated portrait for an account or contact identifier."""
    return sum(ord(char) * (index + 1) for index, char in enumerate(str(identifier))) % len(PORTRAIT_COLORS)


@lru_cache(maxsize=512)
def texture(kind, value, width, height, color):
    viewbox, aspect, body = source(kind, value)
    if color:
        body = body.replace("currentColor", color)
    document = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{viewbox}" preserveAspectRatio="{aspect}">{body}</svg>'
    )
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(document.encode())
    loader.close()
    return Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())


def css_color(widget):
    try:
        rgba = widget.get_color()
    except AttributeError:
        rgba = widget.get_style_context().get_color()
    return "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)), rgba.alpha


class Art(Gtk.Widget):
    """Draws one piece of artwork; icons take their colour from CSS."""

    __gtype_name__ = "LineArt"

    def __init__(self, kind, value=None, width=20, height=None, css=None):
        super().__init__()
        self.kind = kind
        self.value = value
        self.art_width = width
        self.art_height = height or width
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_can_target(False)
        self.add_css_class("art")
        for name in (css or "").split():
            self.add_css_class(name)
        if kind == "icon":
            self.add_css_class("icon")

    def do_measure(self, orientation, for_size):
        size = self.art_width if orientation == Gtk.Orientation.HORIZONTAL else self.art_height
        return size, size, -1, -1

    def set_art(self, kind, value=None):
        self.kind, self.value = kind, value
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        scale = max(1, self.get_scale_factor())
        color, alpha = css_color(self) if self.kind == "icon" else (None, 1)
        paint = texture(self.kind, self.value, width * scale, height * scale, color)
        if alpha < 1:
            snapshot.push_opacity(alpha)
        snapshot.append_texture(paint, Graphene.Rect().init(0, 0, width, height))
        if alpha < 1:
            snapshot.pop()
