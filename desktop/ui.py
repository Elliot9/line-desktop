"""Small GTK builders that mirror the element types used in index.html."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from .art import Art, portrait_index
from .model import KEEP_ID

H = Gtk.Orientation.HORIZONTAL
V = Gtk.Orientation.VERTICAL
DEMO_PORTRAITS = {"demo-me": 0, "demo-yiting": 1, "demo-hao": 2, "demo-momo": 3, "demo-jia": 4, "demo-kai": 5}


def classes(widget, css):
    for name in (css or "").split():
        widget.add_css_class(name)
    return widget


def box(orientation=V, spacing=0, css=None, hexpand=False, vexpand=False, halign=None, valign=None):
    widget = classes(Gtk.Box(orientation=orientation, spacing=spacing), css)
    widget.set_hexpand(hexpand)
    widget.set_vexpand(vexpand)
    if halign is not None:
        widget.set_halign(halign)
    if valign is not None:
        widget.set_valign(valign)
    return widget


def label(text="", css=None, wrap=False, xalign=0.0, ellipsize=False, lines=0, max_chars=-1, selectable=False):
    widget = classes(Gtk.Label(label=str(text), xalign=xalign), css)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if lines:
            widget.set_lines(lines)
            widget.set_ellipsize(Pango.EllipsizeMode.END)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
        widget.set_single_line_mode(True)
    if max_chars > 0:
        widget.set_max_width_chars(max_chars)
    if selectable:
        widget.set_selectable(True)
    return widget


def dot(css=""):
    widget = box(css="dot " + css, valign=Gtk.Align.CENTER)
    return widget


def button(child=None, callback=None, css=None, tooltip=None, text=None):
    widget = classes(Gtk.Button(), css)
    if text is not None:
        widget.set_child(label(text, xalign=0.5))
    elif child is not None:
        widget.set_child(child)
    if callback:
        widget.connect("clicked", lambda *_: callback())
    if tooltip:
        widget.set_tooltip_text(tooltip)
        set_accessible_label(widget, tooltip)
    return widget


def set_accessible_label(widget, text):
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    except (TypeError, AttributeError):
        pass


def icon(name, size=20, css=None):
    return Art("icon", name, size, size, css)


def icon_button(name, callback=None, tooltip=None, size=20, css="icon-btn"):
    image = icon(name, size)
    image.set_halign(Gtk.Align.CENTER)
    image.set_valign(Gtk.Align.CENTER)
    return button(image, callback, css, tooltip)


def icon_text(name, text, size=14, spacing=7, css=None):
    row = box(H, spacing, css)
    image = icon(name, size)
    image.set_valign(Gtk.Align.CENTER)
    row.append(image)
    row.append(label(text))
    return row


def clear(widget):
    child = widget.get_first_child()
    while child:
        following = child.get_next_sibling()
        widget.remove(child)
        child = following


def scroller(child, css=None, horizontal=False):
    widget = classes(Gtk.ScrolledWindow(), css)
    widget.set_policy(Gtk.PolicyType.AUTOMATIC if horizontal else Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_child(child)
    widget.set_vexpand(True)
    return widget


def portrait_for(person_id, own_id=""):
    person_id = str(person_id or "")
    if person_id in DEMO_PORTRAITS:
        return DEMO_PORTRAITS[person_id]
    if own_id and person_id == own_id:
        return 0
    return 1 + portrait_index(person_id) % 5


def avatar(kind, value=None, size=43, radius=16):
    """kind: person | group | keep. Rounded with the index.html radius."""
    if kind == "keep":
        frame = box(css=f"avatar keep r{radius}", halign=Gtk.Align.START, valign=Gtk.Align.START)
        frame.set_size_request(size, size)
        mark = icon("bookmark", 31 if size >= 60 else 22)
        mark.set_halign(Gtk.Align.CENTER)
        mark.set_valign(Gtk.Align.CENTER)
        mark.set_vexpand(True)
        frame.append(mark)
        return frame
    art = Art("group", None, size, size, f"avatar group r{radius}") if kind == "group" else Art("portrait", value, size, size, f"avatar r{radius}")
    art.set_halign(Gtk.Align.START)
    art.set_valign(Gtk.Align.START)
    return art


def chat_avatar(chat, own_id="", size=43, radius=16):
    if not chat:
        return avatar("person", 0, size, radius)
    if chat.get("id") == KEEP_ID:
        return avatar("keep", None, size, radius)
    if chat.get("kind") in ("group", "room"):
        return avatar("group", None, size, radius)
    return avatar("person", portrait_for(chat.get("id"), own_id), size, radius)


def person_avatar(person_id, own_id="", size=43, radius=16):
    return avatar("person", portrait_for(person_id, own_id), size, radius)


def file_badge(name, small=False):
    extension = (str(name or "FILE").rsplit(".", 1)[-1] if "." in str(name or "") else "FILE")[:4].upper()
    badge = box(css="file-icon" + (" small" if small else ""), valign=Gtk.Align.CENTER)
    text = label(extension, xalign=0.5)
    text.set_vexpand(True)
    badge.append(text)
    return badge


def file_size(size):
    size = int(size or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1048576:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1048576:.1f} MB"


class Responsive(Gtk.Box):
    """Box that reports its width after allocation, for index.html breakpoints."""

    __gtype_name__ = "LineResponsive"

    def __init__(self, callback, **kwargs):
        super().__init__(**kwargs)
        self.callback = callback
        self.last_width = 0

    def do_size_allocate(self, width, height, baseline):
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        if width != self.last_width:
            self.last_width = width
            GLib.idle_add(lambda: self.callback(width) and False)
