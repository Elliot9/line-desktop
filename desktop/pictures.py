import hashlib
import os
from pathlib import Path
import queue
import threading
import urllib.parse
import urllib.request

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


class Pictures:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.queue = queue.Queue(maxsize=512)
        self.cache = {}
        self.lock = threading.Lock()
        self.closed = False
        self.disk = cache_dir()
        for index in range(4):
            threading.Thread(target=self.work, daemon=True, name=f"picture-{index}").start()

    def load(self, url, callback, failed=None):
        """Returns False when the URL is not allowed or the queue is full."""
        if not allowed(url):
            return False
        with self.lock:
            cached = self.cache.get(url)
        if cached:
            self.dispatch(callback, cached)
            return True
        try:
            self.queue.put_nowait((url, callback, failed))
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
            url, callback, failed = self.queue.get()
            if self.closed:
                return
            try:
                data = self.fetch(url)
                with self.lock:
                    if len(self.cache) >= 256:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[url] = data
                self.dispatch(callback, data)
            except (OSError, ValueError):
                if failed:
                    self.dispatch(failed)
