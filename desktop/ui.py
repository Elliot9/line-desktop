"""Small GTK builders that mirror the element types used in index.html."""
import re
import unicodedata

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from .art import Art, portrait_index
from .avatar import Avatar
from .model import KEEP_ID

H = Gtk.Orientation.HORIZONTAL
V = Gtk.Orientation.VERTICAL
PEOPLE = None  # set by the app: person id -> profile picture URL
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
    """kind: person | group | keep. Always a circle, like LINE."""
    if kind == "keep":
        frame = box(css="avatar keep round", halign=Gtk.Align.START, valign=Gtk.Align.START)
        frame.set_size_request(size, size)
        mark = icon("bookmark", 31 if size >= 60 else max(14, size // 2))
        mark.set_halign(Gtk.Align.CENTER)
        mark.set_valign(Gtk.Align.CENTER)
        mark.set_vexpand(True)
        frame.append(mark)
        return frame
    if kind == "group":
        return Avatar([("default", None)], size, "group")
    return Avatar([("art", value)] if value is not None else [("default", None)], size)


def person_source(person_id, own_id=""):
    person_id = str(person_id or "")
    url = PEOPLE(person_id) if PEOPLE else ""
    if url:
        return ("url", url)
    if person_id in DEMO_PORTRAITS:
        return ("art", DEMO_PORTRAITS[person_id])
    return ("default", None)


def chat_avatar(chat, own_id="", size=43, radius=16):
    if not chat:
        return avatar("person", None, size)
    if chat.get("id") == KEEP_ID:
        return avatar("keep", None, size)
    if chat.get("avatar"):
        return Avatar([("url", chat["avatar"])], size)
    if chat.get("kind") in ("group", "room"):
        # LINE tiles up to four members when a group has no picture.
        members = [m for m in chat.get("members", []) if m and m != own_id][:4]
        if members:
            return Avatar([person_source(m, own_id) for m in members], size, "group")
        return avatar("group", None, size)
    return Avatar([person_source(chat.get("id"), own_id)], size)


def person_avatar(person_id, own_id="", size=43, radius=16):
    return Avatar([person_source(person_id, own_id)], size)


# File type → colour of its icon, like LINE's file messages.
FILE_KINDS = {
    "pdf": ("pdf",),
    "word": ("doc", "docx", "odt", "rtf", "pages"),
    "sheet": ("xls", "xlsx", "xlsm", "csv", "ods", "numbers"),
    "slides": ("ppt", "pptx", "odp", "key"),
    "archive": ("zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"),
    "image": ("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "svg", "tif", "tiff"),
    "video": ("mp4", "mov", "m4v", "avi", "mkv", "webm"),
    "audio": ("mp3", "m4a", "aac", "wav", "flac", "ogg"),
}
FILE_KIND = {extension: kind for kind, extensions in FILE_KINDS.items() for extension in extensions}


def file_extension(name):
    name = str(name or "")
    stem, dot, extension = name.rpartition(".")
    return extension.lower() if dot and stem and 0 < len(extension) <= 8 and extension.isalnum() else ""


def file_badge(name, small=False):
    extension = file_extension(name)
    kind = FILE_KIND.get(extension, "other")
    badge = box(css=f"file-icon kind-{kind}" + (" small" if small else ""), valign=Gtk.Align.CENTER)
    text = label((extension or "file")[:4].upper(), xalign=0.5)
    text.set_vexpand(True)
    badge.append(text)
    return badge


def columns(text):
    """Display width: CJK and other wide characters take two columns."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def short_file_name(name, limit):
    """name shortened in the middle to at most limit columns, keeping its
    end and extension (2026年度旅遊行程…最終版本v3.xlsx)."""
    if columns(name) <= limit:
        return name
    tail = name[-(len(file_extension(name)) + 7):]
    while columns(tail) > limit // 2:
        tail = tail[1:]
    head = ""
    for char in name:
        if columns(head + char) + 1 + columns(tail) > limit:
            break
        head += char
    return head.rstrip() + "…" + tail


def file_title(name, css="file-name", max_chars=22):
    """A file name over about two lines, like LINE's file messages. A long
    one loses its middle rather than its extension. (An ellipsizing label
    would ask for the whole name's width and stretch the card.)"""
    return label(short_file_name(str(name), max_chars * 2), css, wrap=True, max_chars=max_chars)


# Web addresses in messages: http(s) and www. only. Full-width punctuation
# ends an address; trailing ASCII punctuation is left out of it.
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"'\u3000-\u303f\uff01-\uff0f\uff1a-\uff20]+", re.IGNORECASE)
URL_TRAILING = ".,;:!?'\"]}>"


def linkify(widget, text):
    """Shows the web addresses in a label's text as links; returns whether
    there were any. GtkLabel opens a link in the browser when clicked."""
    text = str(text or "")
    pieces, last = [], 0
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(URL_TRAILING)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(URL_TRAILING)
        if len(url) <= len("https://") or url.lower() in ("www.",):
            continue
        start, end = match.start(), match.start() + len(url)
        href = url if "://" in url else "https://" + url
        pieces.append(GLib.markup_escape_text(text[last:start], -1))
        pieces.append(f'<a href="{GLib.markup_escape_text(href, -1)}">{GLib.markup_escape_text(url, -1)}</a>')
        last = end
    if not pieces:
        return False
    pieces.append(GLib.markup_escape_text(text[last:], -1))
    widget.set_markup("".join(pieces))
    return True


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
