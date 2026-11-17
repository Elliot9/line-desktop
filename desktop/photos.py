"""Chat photos: decoded on worker threads and kept in small caches.

The core hands a photo over as a private file. Decoding a phone photo takes a
noticeable fraction of a second and its pixels take tens of megabytes, so the
work happens off the GTK main loop and the pictures are scaled to the size
they are shown at: a chat bubble never needs more than PREVIEW pixels, and the
viewer is capped at VIEWER.
"""
import collections
import queue
import threading
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

PREVIEW = 480
VIEWER = 4096
PREVIEWS_KEPT = 120
ORIGINALS_KEPT = 4
PATHS_KEPT = 200
RETRY_AFTER = 30.0


def decode(path, limit):
    """A texture of the image at path, at most limit pixels on its long side."""
    info, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    if info is None or width <= 0 or height <= 0:
        raise ValueError("not an image")
    if max(width, height) <= limit:
        return Gdk.Texture.new_from_filename(path)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, limit, limit, True)
    return Gdk.Texture.new_for_pixbuf(pixbuf)


class Photos:
    def __init__(self, dispatch, workers=2):
        self.dispatch = dispatch
        self.closed = False
        self.jobs = queue.Queue()
        # (message id, is preview) -> texture; previews and full pictures are
        # trimmed separately because a full picture is up to 64 MiB of pixels.
        self.textures = collections.OrderedDict()
        # message id -> file holding the full picture, for saving and sharing.
        self.paths = collections.OrderedDict()
        # (message id, is preview) -> [(picture widget, done, failed)] while a
        # fetch is under way, so redrawing a chat does not request it again.
        self.waiting = {}
        self.failed = {}
        for index in range(workers):
            threading.Thread(target=self.work, daemon=True, name=f"photo-{index}").start()

    def clear(self):
        self.textures.clear()
        self.paths.clear()
        self.waiting.clear()
        self.failed.clear()

    def texture(self, message_id, preview):
        """The cached texture; a preview request is also served by the full
        picture when that is what the cache holds."""
        found = self.textures.get((message_id, preview))
        if found is None and preview:
            found = self.textures.get((message_id, False))
        return found

    def remember(self, key, texture, path=None):
        self.textures.pop(key, None)
        self.textures[key] = texture
        kept = PREVIEWS_KEPT if key[1] else ORIGINALS_KEPT
        same = [other for other in self.textures if other[1] == key[1]]
        for other in same[:max(0, len(same) - kept)]:
            del self.textures[other]
        if path and not key[1]:
            self.paths.pop(key[0], None)
            self.paths[key[0]] = path
            while len(self.paths) > PATHS_KEPT:
                self.paths.popitem(last=False)

    def forget(self, message_id):
        """Drops everything held for a message (it was unsent)."""
        for preview in (True, False):
            self.textures.pop((message_id, preview), None)
            self.waiting.pop((message_id, preview), None)
        self.paths.pop(message_id, None)

    def recently_failed(self, key):
        return time.monotonic() - self.failed.get(key, -RETRY_AFTER) < RETRY_AFTER

    def decode(self, path, preview, callback):
        """Decodes path on a worker; callback(texture or None) runs on the main loop."""
        self.jobs.put((str(path), PREVIEW if preview else VIEWER, callback))

    def work(self):
        while not self.closed:
            path, limit, callback = self.jobs.get()
            if self.closed:
                return
            try:
                texture = decode(path, limit)
            except (GLib.Error, OSError, ValueError, TypeError):
                texture = None
            self.dispatch(callback, texture)
