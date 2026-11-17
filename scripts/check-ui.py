#!/usr/bin/env python3
"""Checks the UI's transport and chat state without a display or LINE.

desktop/ipc.py is driven against a scripted stand-in for the core; the GTK
main loop is replaced by a queue the test runs by hand, so ordering and
backpressure can be observed. Run: python3 scripts/check-ui.py
"""
import os
from pathlib import Path
import queue
import stat
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import ipc  # noqa: E402
from desktop.model import HISTORY_LIMIT, KEEP_ID, State  # noqa: E402

# Replies to "run" by writing the scripted lines, then answers the request.
FAKE_CORE = r'''#!/usr/bin/python3
import json, sys
out = sys.stdout.buffer
def send(obj):
    out.write(json.dumps(obj).encode() + b"\n")
for line in sys.stdin.buffer:
    request = json.loads(line)
    params = request.get("params") or {}
    for step in params.get("script", []):
        kind = step[0]
        if kind == "event":
            send({"event": step[1], "data": step[2]})
        elif kind == "events":
            for index in range(step[2]):
                send({"event": step[1], "data": {"n": index}})
        elif kind == "raw":
            out.write(step[1].encode() + b"\n")
        elif kind == "big":
            out.write(b'{"event":"blob","data":{"x":"' + b"x" * step[1] + b'"}}\n')
        elif kind == "exit":
            out.flush()
            sys.exit(0)
    send({"id": request["id"], "ok": True, "result": {"size": len(params.get("pad", ""))}})
    out.flush()
'''


class Loop:
    """Stands in for the GTK main loop: callbacks run only when told to."""

    def __init__(self):
        self.calls = queue.Queue()

    def dispatch(self, callback, *args):
        self.calls.put((callback, args))

    def run(self, until, timeout=15):
        deadline = time.monotonic() + timeout
        while not until():
            try:
                callback, args = self.calls.get(timeout=0.02)
            except queue.Empty:
                assert time.monotonic() < deadline, "timed out waiting for the transport"
                continue
            callback(*args)


class Session:
    def __init__(self, executable, fail_on=None):
        os.environ["LINE_APP_CORE"] = executable
        self.loop = Loop()
        self.events = []
        self.replies = []
        self.fail_on = fail_on
        self.core = ipc.Core(False, self.loop.dispatch, self.event)

    def event(self, name, data):
        if name == self.fail_on:
            raise RuntimeError("handler failure (expected by the test)")
        self.events.append((name, data))

    def run(self, script, **params):
        self.core.request("run", dict(params, script=script), self.replies.append)

    def wait_inbox(self, count, timeout=10):
        deadline = time.monotonic() + timeout
        while len(self.core.inbox) < count:
            assert time.monotonic() < deadline, "the reader did not deliver in time"
            time.sleep(0.005)

    def close(self):
        self.core.stop()


def check_transport(executable):
    # 1. A reply far larger than the old 2 MiB line limit arrives intact, and
    #    the session carries on (a 3 MB photo used to end it with 核心已停止).
    session = Session(executable)
    session.run([["big", 5 * 1024 * 1024]])
    session.loop.run(lambda: session.replies)
    assert session.replies[0]["ok"] and len(session.events[0][1]["x"]) == 5 * 1024 * 1024
    assert not any(name == "session" for name, _ in session.events), session.events[1:]
    session.close()
    print("PASS large line is delivered and the session survives")

    # 2. A line over the sanity bound, and a line that is not JSON, are
    #    skipped; everything after them still arrives.
    limit, ipc.MAX_INCOMING = ipc.MAX_INCOMING, 256 * 1024
    try:
        session = Session(executable)
        session.run([["big", 3 * 1024 * 1024], ["raw", "{not json"], ["raw", "[1, 2]"], ["event", "after", {"ok": 1}]])
        session.loop.run(lambda: session.replies)
        names = [name for name, _ in session.events]
        assert names == ["notice", "after"], names
        assert session.replies[0]["ok"]
        session.close()
    finally:
        ipc.MAX_INCOMING = limit
    print("PASS oversized and malformed lines are skipped without ending the session")

    # 3. Backpressure: with the main loop not running, the reader holds at
    #    most INBOX_LIMIT envelopes instead of the whole burst, and nothing is
    #    lost or reordered once the loop runs again.
    session = Session(executable)
    total = ipc.INBOX_LIMIT * 12
    session.run([["events", "tick", total]])
    session.wait_inbox(ipc.INBOX_LIMIT)
    time.sleep(0.3)
    assert len(session.core.inbox) <= ipc.INBOX_LIMIT, len(session.core.inbox)
    session.loop.run(lambda: session.replies)
    ticks = [data["n"] for name, data in session.events if name == "tick"]
    assert ticks == list(range(total)), (len(ticks), ticks[:5])
    session.close()
    print("PASS backpressure bounds the backlog; order and count are preserved")

    # 4. A snapshot that is already followed by a newer one is skipped; the
    #    events between them keep their place.
    session = Session(executable)
    session.run([["event", "snapshot", {"rev": 1}], ["event", "messages", {"messages": []}],
                 ["event", "snapshot", {"rev": 2}], ["event", "chats", {"chats": []}]])
    session.wait_inbox(5)
    session.loop.run(lambda: session.replies)
    assert session.events == [("messages", {"messages": []}), ("snapshot", {"rev": 2}), ("chats", {"chats": []})], session.events
    session.close()
    print("PASS only the newest queued snapshot is applied")

    # 5. A handler that raises does not stop the events behind it.
    session = Session(executable, fail_on="boom")
    stderr, sys.stderr = sys.stderr, open(os.devnull, "w")
    try:
        session.run([["event", "boom", {}], ["event", "after", {}]])
        session.loop.run(lambda: session.replies)
    finally:
        sys.stderr.close()
        sys.stderr = stderr
    assert [name for name, _ in session.events] == ["after"], session.events
    session.close()
    print("PASS a failing handler does not block later events")

    # 6. When the core exits, the UI is told once and waiting requests fail.
    session = Session(executable)
    session.core.request("run", {"script": [["exit"]]}, session.replies.append)
    session.loop.run(lambda: session.replies)
    assert session.events and session.events[-1][0] == "session" and session.events[-1][1]["state"] == "error"
    assert session.replies[0]["ok"] is False
    session.close()
    print("PASS a stopped core is reported and pending requests are answered")


def message(index, chat, **extra):
    return dict({"id": f"m{index}", "chat_id": chat, "sender_id": "u2", "text": f"t{index}", "kind": "text", "mine": False, "time": 1000 + index}, **extra)


def check_state():
    state = State()
    state.snapshot({"profile": {"id": "me"}, "contacts": [{"id": "u2", "name": "Friend"}],
                    "chats": [{"id": "u2", "name": "Friend", "kind": "direct", "unread": 3, "updated_at": 1000, "last_message": "old"}]})

    # Rows that come with messages are the source of the unread count.
    assert state.update_chats([{"id": "u2", "name": "Friend", "kind": "direct", "unread": 5, "updated_at": 1002, "last_message": "t2"},
                               {"id": "c9", "name": "New group", "kind": "group", "unread": 1, "updated_at": 1003, "last_message": "hi"},
                               {"id": KEEP_ID}, "junk"]) == ["u2", "c9"]
    assert state.add_message(message(1, "u2"), count=False) and state.add_message(message(2, "u2"), count=False)
    assert state.chats["u2"]["unread"] == 5, state.chats["u2"]
    assert state.chats["c9"]["name"] == "New group"
    # Without rows (older cores) the UI still counts.
    assert state.add_message(message(3, "u2")) and state.chats["u2"]["unread"] == 6
    # The same message again replaces the stored one and is not new.
    assert not state.add_message(message(3, "u2", text="edited")) and state.messages["u2"][-1]["text"] == "edited"
    # An older message is put in order and does not touch the row.
    assert state.add_message(message(0, "u2", time=5)) and [m["id"] for m in state.messages["u2"]] == ["m0", "m1", "m2", "m3"]
    assert state.chats["u2"]["unread"] == 6 and state.chats["u2"]["updated_at"] == 1003
    print("PASS chat rows and messages update the state consistently")

    # A long conversation is bounded in memory.
    for index in range(10, 10 + HISTORY_LIMIT + 50):
        state.add_message(message(index, "c9"), count=False)
    assert len(state.messages["c9"]) == HISTORY_LIMIT and state.messages["c9"][-1]["id"] == f"m{9 + HISTORY_LIMIT + 50}"
    print("PASS conversation history is bounded")

    # A chat just opened with a friend survives a snapshot that does not list
    # it; any other chat missing from the snapshot is dropped.
    state.chats["u7"] = {"id": "u7", "name": "No messages yet", "kind": "direct", "local": True}
    state.selected = "u7"
    state.snapshot({"contacts": [], "chats": [{"id": "u2", "name": "Friend", "kind": "direct", "unread": 0, "updated_at": 1}]})
    assert "u7" in state.chats and "c9" not in state.chats
    state.selected = "u2"
    state.snapshot({"contacts": [], "chats": []})
    assert "u7" not in state.chats and "u2" not in state.chats
    print("PASS a locally opened chat is kept while it is selected")


def check_launcher():
    from desktop import launcher
    entries = launcher.entries(Path("/opt/line desktop"))
    live = entries[launcher.LIVE_ID]
    assert "Actions=quit;\n" in live and "Name=結束 LINE\n" in live and live.rstrip().endswith('Exec="/opt/line desktop/line-app" --quit')
    assert "離線展示" not in live and "--demo" not in live
    assert "NoDisplay=true" in entries[launcher.DEMO_ID]
    print("PASS the dock menu offers 結束 LINE and no 離線展示")

    previous = os.environ.get("XDG_DATA_HOME")
    with tempfile.TemporaryDirectory() as data:
        os.environ["XDG_DATA_HOME"] = data
        try:
            root = Path(__file__).resolve().parents[1]
            assert not launcher.refresh(root)  # nothing installed: nothing written
            installed = launcher.install(root)
            assert not launcher.refresh(root)  # already current
            installed.write_text(installed.read_text().replace("Actions=quit;", "Actions=demo;"))
            assert launcher.refresh(root) and installed.read_text() == launcher.entries(root)[launcher.LIVE_ID]
            elsewhere = launcher.entries(Path("/elsewhere"))[launcher.LIVE_ID]
            installed.write_text(elsewhere)
            assert not launcher.refresh(root) and installed.read_text() == elsewhere
        finally:
            if previous is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = previous
    print("PASS an outdated launcher from this folder is refreshed; others are left alone")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        fake = Path(directory) / "fake-core"
        fake.write_text(FAKE_CORE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        check_transport(str(fake))
    check_state()
    check_launcher()
