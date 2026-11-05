"""Chat state and private local notes, independent of GTK."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile


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
        self.chats = {str(c["id"]): c for c in data.get("chats", [])}

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
        if chat:
            chat["last_message"] = message.get("text") or media_label(message)
            chat["updated_at"] = message.get("time", 0)
            if not message.get("mine") and chat_id != self.selected:
                chat["unread"] = int(chat.get("unread", 0)) + 1
        return True


def media_label(message):
    return {"sticker": "[貼圖]", "image": "[圖片]", "file": "[檔案]", "system": "[系統訊息]"}.get(message.get("kind"), "[訊息]")


class Notes:
    def __init__(self):
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        if not base.is_absolute():
            base = Path.home() / ".local/share"
        self.root = base / "matrix-line" / "notes"

    def path(self, account, chat):
        key = hashlib.sha256((account + "\0" + chat).encode()).hexdigest()
        return self.root / (key + ".json")

    def read(self, account, chat):
        try:
            return json.loads(self.path(account, chat).read_text()).get("text", "")
        except (OSError, ValueError):
            return ""

    def save(self, account, chat, text):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.parent.chmod(0o700)
        self.root.chmod(0o700)
        destination = self.path(account, chat)
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".note-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"text": text}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
