"""Private JSON-lines transport. Only the GTK main loop touches UI state."""
import json
import os
import queue
from pathlib import Path
import subprocess
import threading

MAX_ENVELOPE = 2 * 1024 * 1024
DEBUG = os.environ.get("LINE_APP_DEBUG") == "1"


class Core:
    def __init__(self, demo, dispatch, event):
        self.dispatch = dispatch
        self.event = event
        self.callbacks = {}
        self.sequence = 0
        self.lock = threading.Lock()
        self.closed = False
        self.outgoing = queue.Queue(maxsize=64)
        executable = os.environ.get("LINE_APP_CORE", str(Path(__file__).resolve().parents[1] / "build/line-core"))
        environment = dict(os.environ)
        if DEBUG:
            environment["LINE_CORE_DEBUG"] = "1"
        # In debug mode the core's redacted step log goes to this terminal.
        self.process = subprocess.Popen([executable] + (["--demo"] if demo else []), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None if DEBUG else subprocess.DEVNULL, env=environment)
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
        try:
            while not self.closed:
                line = self.process.stdout.readline(MAX_ENVELOPE + 1)
                if not line:
                    break
                if len(line) > MAX_ENVELOPE:
                    raise ValueError("oversized envelope")
                data = json.loads(line)
                if data.get("event"):
                    self.dispatch(self.event, data["event"], data.get("data", {}))
                elif "id" in data:
                    with self.lock:
                        callback = self.callbacks.pop(str(data["id"]), None)
                    if callback:
                        self.dispatch(callback, data)
        except (OSError, ValueError, TypeError):
            pass
        if not self.closed:
            self.dispatch(self.event, "session", {"state": "error", "message": "核心已停止。請重新啟動程式；未送出的內容仍保留在輸入框。"})
            with self.lock:
                pending, self.callbacks = self.callbacks, {}
            for callback in pending.values():
                self.dispatch(callback, {"ok": False, "error": {"message": "核心已停止，傳送結果不明；請確認後再試。"}})

    def stop(self):
        if self.closed:
            return
        self.closed = True
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
