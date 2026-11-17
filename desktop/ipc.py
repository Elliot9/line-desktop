"""Private JSON-lines transport. Only the GTK main loop touches UI state."""
import collections
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

# What the UI may send in one request; the core enforces the same limit.
MAX_ENVELOPE = 2 * 1024 * 1024
# One line from the core. The snapshot of a large account (every friend, chat
# and group member) can be several MiB, so this is only a sanity bound; photos
# never travel through the pipe (the core returns a file path instead).
MAX_INCOMING = 64 * 1024 * 1024
# Parsed envelopes waiting for the main loop. When the UI falls behind, the
# reader stops reading and the core waits on its pipe, instead of the backlog
# piling up in memory.
INBOX_LIMIT = 256
# Seconds of event handling per main-loop turn before yielding to GTK, so a
# burst of events cannot keep the window from drawing or taking input.
DRAIN_BUDGET = 0.008
STOPPED = "!stopped"


class Core:
    def __init__(self, demo, dispatch, event):
        self.dispatch = dispatch
        self.event = event
        self.callbacks = {}
        self.sequence = 0
        self.lock = threading.Lock()
        self.closed = False
        self.inbox = collections.deque()
        self.ready = threading.Condition()
        self.draining = False
        self.outgoing = queue.Queue(maxsize=256)
        executable = os.environ.get("LINE_APP_CORE", str(Path(__file__).resolve().parents[1] / "build/line-core"))
        self.process = subprocess.Popen([executable] + (["--demo"] if demo else []), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.reader = threading.Thread(target=self.read, daemon=True, name="line-core-reader")
        self.reader.start()
        self.writer = threading.Thread(target=self.write, daemon=True, name="line-core-writer")
        self.writer.start()

    def request(self, method, params=None, callback=None):
        with self.lock:
            if self.closed:
                if callback:
                    self.dispatch(callback, {"ok": False, "error": {"message": "連線已關閉"}})
                return
            self.sequence += 1
            request_id = str(self.sequence)
            body = json.dumps({"id": request_id, "method": method, "params": params or {}}, ensure_ascii=False).encode() + b"\n"
            if len(body) > MAX_ENVELOPE:
                if callback:
                    self.dispatch(callback, {"ok": False, "error": {"message": "訊息超過大小限制"}})
                return
            if callback:
                self.callbacks[request_id] = callback
            try:
                self.outgoing.put_nowait(body)
            except queue.Full:
                callback = self.callbacks.pop(request_id, None)
                if callback:
                    self.dispatch(callback, {"ok": False, "error": {"message": "目前等待中的操作過多，請稍後再試。"}})

    def write(self):
        try:
            while not self.closed:
                body = self.outgoing.get()
                if body is None:
                    return
                self.process.stdin.write(body)
                self.process.stdin.flush()
        except (OSError, ValueError):
            if not self.closed:
                self.dispatch(self.event, "notice", {"level": "error", "message": "核心連線已中斷，請重新啟動程式。"})

    def read(self):
        stream = self.process.stdout
        try:
            # Reading continues until the core closes the pipe, also after
            # stop(): a core that is in the middle of a write can then finish
            # it, notice the shutdown and remove its temporary photos.
            while True:
                line = stream.readline(MAX_INCOMING + 1)
                if not line:
                    break
                if self.closed:
                    continue
                if len(line) > MAX_INCOMING and not line.endswith(b"\n"):
                    # Drop the rest of the line and carry on. Treating this as
                    # fatal used to end the whole session over one reply.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(1024 * 1024)
                    self.deliver({"event": "notice", "data": {"level": "error", "message": "核心傳回的資料過大，已略過。"}})
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    self.deliver(data)
        except (OSError, ValueError, TypeError):
            pass
        if not self.closed:
            self.deliver({"event": STOPPED})

    def deliver(self, item):
        """Reader thread: hand one envelope to the main loop, waiting while
        the main loop is INBOX_LIMIT envelopes behind."""
        with self.ready:
            while len(self.inbox) >= INBOX_LIMIT and not self.closed:
                self.ready.wait(0.5)
            if self.closed:
                return
            self.inbox.append(item)
            schedule = not self.draining
            self.draining = True
        if schedule:
            self.dispatch(self.drain)

    def drain(self):
        """Main loop: handle queued envelopes in order for a few milliseconds,
        then let GTK draw and take input before continuing."""
        deadline = time.monotonic() + DRAIN_BUDGET
        while True:
            with self.ready:
                if not self.inbox or self.closed:
                    self.draining = False
                    return
                item = self.inbox.popleft()
                # A snapshot replaces the whole state, so one that is already
                # followed by a newer snapshot would only be work thrown away.
                stale = item.get("event") == "snapshot" and any(later.get("event") == "snapshot" for later in self.inbox)
                self.ready.notify_all()
            if not stale:
                try:
                    self.handle(item)
                except Exception:  # one bad event must not stop the rest
                    traceback.print_exc(file=sys.stderr)
            if time.monotonic() >= deadline:
                self.dispatch(self.drain)
                return

    def handle(self, data):
        event = data.get("event")
        if event == STOPPED:
            self.event("session", {"state": "error", "message": "核心已停止。請重新啟動程式；未送出的內容仍保留在輸入框。"})
            with self.lock:
                pending, self.callbacks = self.callbacks, {}
            for callback in pending.values():
                callback({"ok": False, "error": {"message": "核心已停止，傳送結果不明；請確認後再試。"}})
        elif event:
            payload = data.get("data")
            self.event(str(event), payload if isinstance(payload, dict) else {})
        elif "id" in data:
            with self.lock:
                callback = self.callbacks.pop(str(data["id"]), None)
            if callback:
                callback(data)

    def stop(self):
        if self.closed:
            return
        self.closed = True
        with self.ready:
            self.ready.notify_all()
        try:
            self.process.terminate()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        finally:
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            try:
                self.outgoing.put_nowait(None)
            except queue.Full:
                pass
