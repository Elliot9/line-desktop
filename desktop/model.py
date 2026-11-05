"""Chat state and private local data (notes, Keep memos, preferences), independent of GTK."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

KEEP_ID = "keep"
KEEP_LIMIT = 1000
DEMO_PINNED = ("demo-weekend", "demo-yiting")
DEMO_FAVORITES = ("demo-yiting", "demo-hao")
DEMO_KEEP = "記下生活裡的小靈感 ✍️\n想去的地方、突然想到的點子，都放在這裡。"


@dataclass
class State:
    mode: str = "live"
    session: str = "logged_out"
    profile: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    contacts: dict = field(default_factory=dict)
    chats: dict = field(default_factory=dict)
    messages: dict = field(default_factory=dict)
    drafts: dict = field(default_factory=dict)
    selected: str = ""

    def clear(self):
        self.session = "logged_out"
        self.profile.clear()
        self.contacts.clear()
        self.chats.clear()
        self.messages.clear()
        self.drafts.clear()
        self.selected = ""

    def snapshot(self, data):
        self.profile = data.get("profile") or self.profile
        self.contacts = {str(c["id"]): c for c in data.get("contacts", [])}
        keep = self.chats.get(KEEP_ID)
        self.chats = {str(c["id"]): c for c in data.get("chats", [])}
        for chat_id, chat in self.chats.items():
            history = self.messages.get(chat_id)
            if history and history[-1].get("time", 0) >= int(chat.get("updated_at", 0) or 0):
                chat["last_message"] = preview(history[-1], chat)
                chat["updated_at"] = history[-1].get("time", 0)
        if keep:
            self.chats[KEEP_ID] = keep

    def name_of(self, person_id):
        if person_id and person_id == self.profile.get("id"):
            return self.profile.get("name", "你")
        contact = self.contacts.get(str(person_id))
        if contact:
            return contact.get("name", "好友")
        chat = self.chats.get(str(person_id))
        return chat.get("name", "成員") if chat else "成員"

    def add_message(self, message):
        chat_id = str(message.get("chat_id", ""))
        message_id = str(message.get("id", ""))
        if not chat_id or not message_id:
            return False
        history = self.messages.setdefault(chat_id, [])
        for index, existing in enumerate(history):
            if str(existing["id"]) == message_id:
                history[index] = message
                return False
        history.append(message)
        history.sort(key=lambda item: item.get("time", 0))
        chat = self.chats.get(chat_id)
        if chat and history[-1] is message:
            chat["last_message"] = preview(message, chat)
            chat["updated_at"] = message.get("time", 0)
            if not message.get("mine") and chat_id != self.selected:
                chat["unread"] = int(chat.get("unread", 0)) + 1
        return True


def media_label(message):
    kind = message.get("kind")
    if kind == "file":
        return "[檔案] " + str(message.get("filename") or "檔案")
    return {"sticker": "[貼圖]", "image": "[照片]", "system": "[系統訊息]"}.get(kind, "[訊息]")


def preview(message, chat):
    text = message.get("text") if message.get("kind") in ("text", None) else media_label(message)
    text = str(text or media_label(message))
    if chat.get("kind") == "group":
        return ("你" if message.get("mine") else str(message.get("sender_name") or "成員")) + "：" + text
    return text


def data_root():
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not base.is_absolute():
        base = Path.home() / ".local/share"
    return base / "matrix-line"


def private_write(directory, destination, data):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.parent.chmod(0o700)
    directory.chmod(0o700)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def account_file(directory, account, chat=""):
    key = hashlib.sha256((account + "\0" + chat).encode()).hexdigest()
    return directory / (key + ".json")


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class Notes:
    """One private note per account and conversation."""

    def __init__(self):
        self.root = data_root() / "notes"

    def path(self, account, chat):
        return account_file(self.root, account, chat)

    def read(self, account, chat):
        data = read_json(self.path(account, chat))
        title = str(data.get("title", ""))[:80]
        body = str(data.get("text", ""))[:5000]
        if not title and not body:
            return None
        return {"title": title or body.split("\n", 1)[0][:80] or "本機記事", "body": body, "updated": int(data.get("updated", 0) or 0)}

    def save(self, account, chat, title, body):
        record = {"title": title.strip()[:80], "text": body[:5000], "updated": int(time.time() * 1000)}
        private_write(self.root, self.path(account, chat), record)
        return self.read(account, chat)

    def delete(self, account, chat):
        try:
            self.path(account, chat).unlink()
        except FileNotFoundError:
            pass


class Preferences:
    """Per-account pinned, muted and favourite chats plus app-wide settings."""

    defaults = {"enter_send": True, "notifications": True, "font_size": 14}

    def __init__(self):
        self.root = data_root() / "preferences"
        self.account = ""
        self.persist = False
        self.lists = {"pinned": [], "muted": [], "favorites": []}
        self.settings = dict(self.defaults)
        stored = read_json(self.root / "settings.json")
        for key, value in self.defaults.items():
            if isinstance(stored.get(key), type(value)):
                self.settings[key] = stored[key]
        if self.settings["font_size"] not in (13, 14, 16):
            self.settings["font_size"] = 14

    def load(self, account, demo):
        """Demo preferences live in memory only, like the rest of the demo."""
        self.account = account
        self.persist = not demo
        if demo:
            self.lists = {"pinned": list(DEMO_PINNED), "muted": [], "favorites": list(DEMO_FAVORITES)}
            return
        data = read_json(account_file(self.root, account))
        self.lists = {key: [str(item) for item in data.get(key, []) if isinstance(item, str)][:500] for key in ("pinned", "muted", "favorites")}

    def has(self, key, item):
        return str(item) in self.lists[key]

    def toggle(self, key, item):
        values = self.lists[key]
        item = str(item)
        values.remove(item) if item in values else values.append(item)
        if self.persist and self.account:
            private_write(self.root, account_file(self.root, self.account), self.lists)
        return item in values

    def save_settings(self, **values):
        for key, value in values.items():
            if key in self.defaults and isinstance(value, type(self.defaults[key])):
                self.settings[key] = value
        private_write(self.root, self.root / "settings.json", self.settings)


class Keep:
    """Private memo conversation that stays on this computer, shown as Keep 筆記."""

    def __init__(self):
        self.root = data_root() / "keep"
        self.account = ""
        self.persist = False
        self.items = []

    def load(self, account, demo):
        self.account = account
        self.persist = not demo
        if demo:
            self.items = [{"id": "keep-demo", "text": DEMO_KEEP, "time": int((time.time() - 3 * 86400) * 1000)}]
            return
        data = read_json(account_file(self.root, account))
        self.items = [item for item in data.get("items", []) if isinstance(item, dict) and isinstance(item.get("text"), str)][-KEEP_LIMIT:]

    def add(self, text):
        item = {"id": "keep-" + uuid.uuid4().hex, "text": text[:10000], "time": int(time.time() * 1000)}
        self.items = (self.items + [item])[-KEEP_LIMIT:]
        if self.persist and self.account:
            private_write(self.root, account_file(self.root, self.account), {"items": self.items})
        return item

    def messages(self):
        return [{"id": item["id"], "chat_id": KEEP_ID, "text": item["text"], "time": item.get("time", 0), "mine": True, "kind": "text", "status": "saved"} for item in self.items]

    def chat(self):
        last = self.items[-1] if self.items else None
        return {"id": KEEP_ID, "name": "Keep 筆記", "kind": "self", "last_message": last["text"] if last else "記下生活裡的小靈感", "unread": 0, "updated_at": last.get("time", 0) if last else 0}
