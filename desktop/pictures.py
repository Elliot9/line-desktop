import hashlib
import os
from pathlib import Path
import queue
import threading
import urllib.parse
import urllib.request

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

# Only LINE's public CDNs: sticker/sticon art and profile pictures.
HOSTS = {"stickershop.line-scdn.net", "profile.line-scdn.net"}
LIMIT = 2 * 1024 * 1024


def cache_dir():
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    if not base.is_absolute():
        base = Path.home() / ".cache"
    return base / "matrix-line" / "pictures"


def allowed(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in HOSTS and not parsed.username


def scaled_texture(data, size):
    """Decodes picture bytes into a texture at most size pixels on its long
    side. Runs on a worker thread: a profile picture decoded at full size on
    the main loop cost several milliseconds and megabytes each."""
    loader = GdkPixbuf.PixbufLoader()

    def prepared(source, width, height):
        longest = max(width, height)
        if longest > size:
            source.set_size(max(1, width * size // longest), max(1, height * size // longest))
    loader.connect("size-prepared", prepared)
    try:
        loader.write(data)
    finally:
        loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        raise ValueError("not a picture")
    return Gdk.Texture.new_for_pixbuf(pixbuf)


class Pictures:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.queue = queue.Queue(maxsize=2048)
        self.cache = {}
        self.lock = threading.Lock()
        self.closed = False
        self.disk = cache_dir()
        for index in range(4):
            threading.Thread(target=self.work, daemon=True, name=f"picture-{index}").start()

    def load(self, url, callback, failed=None, size=0):
        """Fetches url and calls callback on the main loop with its bytes or,
        when size is given, with a texture decoded on the worker and scaled
        down to size. Returns False when the URL is not allowed or the queue
        is full."""
        if not allowed(url):
            return False
        if not size:
            with self.lock:
                cached = self.cache.get(url)
            if cached:
                self.dispatch(callback, cached)
                return True
        try:
            self.queue.put_nowait((url, callback, failed, size))
        except queue.Full:
            return False
        return True

    def path(self, url):
        return self.disk / hashlib.sha256(url.encode()).hexdigest()

    def fetch(self, url):
        path = self.path(url)
        try:
            data = path.read_bytes()
            if data:
                return data
        except OSError:
            pass
        with urllib.request.urlopen(url, timeout=12) as response:
            if not allowed(response.url):
                raise ValueError("redirected off LINE CDN")
            data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("picture too large")
        try:
            self.disk.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(".tmp%d" % threading.get_ident())
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass
        return data

    def work(self):
        while not self.closed:
            url, callback, failed, size = self.queue.get()
            if self.closed:
                return
            try:
                data = self.fetch(url)
                if size:
                    self.dispatch(callback, scaled_texture(data, size))
                    continue
                with self.lock:
                    if len(self.cache) >= 256:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[url] = data
                self.dispatch(callback, data)
            except (OSError, ValueError, GLib.Error):
                if failed:
                    self.dispatch(failed)
