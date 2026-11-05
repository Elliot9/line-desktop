import queue
import threading
import urllib.parse
import urllib.request


class Pictures:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.queue = queue.Queue(maxsize=128)
        self.cache = {}
        self.lock = threading.Lock()
        self.closed = False
        for index in range(3):
            threading.Thread(target=self.work, daemon=True, name=f"sticker-image-{index}").start()

    def load(self, url, callback):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'stickershop.line-scdn.net' or parsed.username:
            return
        with self.lock:
            cached = self.cache.get(url)
        if cached:
            self.dispatch(callback, cached)
            return
        try:
            self.queue.put_nowait((url, callback))
        except queue.Full:
            return

    def work(self):
        while not self.closed:
            url, callback = self.queue.get()
            if self.closed:
                return
            try:
                with urllib.request.urlopen(url, timeout=12) as response:
                    final = urllib.parse.urlsplit(response.url)
                    if final.scheme != 'https' or final.hostname != 'stickershop.line-scdn.net':
                        continue
                    data = response.read(2 * 1024 * 1024 + 1)
                    if len(data) > 2 * 1024 * 1024:
                        continue
                with self.lock:
                    if len(self.cache) >= 128:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[url] = data
                self.dispatch(callback, data)
            except (OSError, ValueError):
                pass
