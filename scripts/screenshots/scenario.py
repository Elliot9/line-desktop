#!/usr/bin/env python3
"""A stand-in for the LINE core that serves one believable account, for README
screenshots only. It speaks the core's JSON-lines protocol on stdin/stdout,
never touches the network or the keyring, and serves pictures from
SCREENSHOT_PHOTOS (files named <key>.jpg, see photos.json).

The people are stock-photo models; their names and conversations are invented.
"""
import datetime
import json
import os
import sys

PHOTOS = os.environ.get("SCREENSHOT_PHOTOS", "")
AVATAR = "https://profile.line-scdn.net/screenshot/"  # mapped to local files by shoot.py
# Today at 20:45, whenever the screenshots are taken, so the times look like
# an ordinary evening.
NOW = int(datetime.datetime.combine(datetime.date.today(), datetime.time(20, 45)).timestamp() * 1000)
MINUTE, HOUR, DAY = 60_000, 3_600_000, 86_400_000


def mid(kind, n):
    return kind + "%032x" % n


ME = mid("u", 1)
PEOPLE = {
    "yating": ("張雅婷", "週末不是在山上，就是在去山上的路上 ⛰️"),
    "bohan": ("陳柏翰", "專注當下"),
    "jiahao": ("許家豪", "咖啡續命中 ☕"),
    "zixuan": ("周子軒", "台北 · 攝影 · 跑步"),
    "guanyu": ("林冠宇", "產品設計｜週五下午不開會"),
    "peishan": ("李佩珊", "今天也要好好吃飯"),
    "shihan": ("黃詩涵", "生活需要一點甜"),
    "siyu": ("王思妤", "📚 讀書會每月第二個週六"),
    "xinyi": ("吳欣怡", ""),
    "mom": ("媽媽", "家和萬事興"),
    "grandpa": ("阿公", ""),
}
IDS = {key: mid("u", n + 2) for n, key in enumerate(PEOPLE)}
KEY_OF = {v: k for k, v in IDS.items()}


def person(key):
    name, status = PEOPLE[key]
    return {"id": IDS[key], "name": name, "status": status, "friend": True, "avatar": AVATAR + key}


me = {"id": ME, "name": "陳品妤", "status": "慢慢來，比較快。", "friend": False, "avatar": AVATAR + "me"}
contacts = sorted((person(k) for k in PEOPLE), key=lambda p: p["name"])
for c in contacts:
    if KEY_OF[c["id"]] in ("yating", "mom"):
        c["favorite"] = True

HIKE, FAMILY, WORK = mid("c", 101), mid("c", 102), mid("c", 103)
messages = {}
pictures = {}  # message id -> photo key
sequence = [1000]


def add(chat, who, text="", ago=0, kind="text", picture=None, reply_to=None, **extra):
    sequence[0] += 1
    mine = who == "me"
    sender = ME if mine else IDS[who]
    m = {"id": str(sequence[0]), "chat_id": chat, "sender_id": sender, "sender_name": me["name"] if mine else PEOPLE[who][0],
         "text": text, "kind": kind, "mine": mine, "time": NOW - ago, "status": "sent" if mine else "received"}
    if picture:
        m.update(kind="image", text="[照片]", width=extra.pop("width", 1280), height=extra.pop("height", 960))
        pictures[m["id"]] = picture
    if reply_to:
        m["reply_to"] = reply_to
    m.update(extra)
    messages.setdefault(chat, []).append(m)
    return m["id"]


# 週末爬山小隊: the hero conversation.
add(HIKE, "bohan", "週六要不要去走七星山？氣象說是大晴天 ☀️", DAY + 3 * HOUR)
add(HIKE, "yating", "好啊！上次去芒草超美", DAY + 3 * HOUR - 4 * MINUTE)
add(HIKE, "yating", picture="ridge", ago=DAY + 3 * HOUR - 5 * MINUTE, width=1280, height=853)
add(HIKE, "jiahao", "我可以，幾點集合？", DAY + 2 * HOUR)
early = add(HIKE, "me", "早上七點小油坑停車場？太晚會沒車位", DAY + 2 * HOUR - 6 * MINUTE)
ride = add(HIKE, "zixuan", "七點 OK，我開車可以載兩個人 🚗", 2 * HOUR + 20 * MINUTE)
add(HIKE, "me", "我搭你的車！早餐我來買 🥪", 2 * HOUR + 12 * MINUTE, reply_to=ride)
add(HIKE, "yating", picture="trail", ago=48 * MINUTE)
add(HIKE, "yating", "這是上次拍的，這週應該也差不多", 47 * MINUTE)
add(HIKE, "bohan", "下山後去吃那家牛肉麵吧", 26 * MINUTE)
add(HIKE, "zixuan", picture="noodles", ago=24 * MINUTE, width=1280, height=853)
noodles = add(HIKE, "zixuan", "就是這家，排隊也值得 😋", 23 * MINUTE)
add(HIKE, "jiahao", "+1 🙋‍♂️ 我負責帶防曬", 15 * MINUTE)
add(HIKE, "me", "好，那就這樣說定了！我先訂位 🙌", 6 * MINUTE, reply_to=noodles)

# 陳家 🏠
add(FAMILY, "mom", "週日回來吃飯嗎？阿公說想吃你上次帶的鳳梨酥", 5 * HOUR)
add(FAMILY, "me", "會！我中午前到", 4 * HOUR + 50 * MINUTE)
add(FAMILY, "grandpa", "好 好 👍", 4 * HOUR + 20 * MINUTE)
add(FAMILY, "mom", picture="cat", ago=35 * MINUTE, width=1024, height=1280)
add(FAMILY, "mom", "小橘又睡在你的床上了 😂", 34 * MINUTE)

# 設計組
add(WORK, "guanyu", "新版首頁的稿子放在共用資料夾了", 3 * HOUR)
add(WORK, "guanyu", "[檔案] 首頁改版提案.pdf", 3 * HOUR - MINUTE, kind="file", filename="首頁改版提案.pdf", size=2_684_354)
add(WORK, "peishan", "收到，我下午看完回饋給你", 2 * HOUR + 40 * MINUTE)
add(WORK, "siyu", "明天十點的會議改到小會議室喔", 75 * MINUTE)

# Direct chats.
add(IDS["yating"], "yating", "週六記得帶外套，山上風很大", 30 * MINUTE)
add(IDS["yating"], "yating", "還有我借你的書不急著還 📖", 29 * MINUTE)
add(IDS["shihan"], "shihan", picture="latte", ago=2 * HOUR)
add(IDS["shihan"], "shihan", "這家新開的咖啡廳很可以，下次一起來", 2 * HOUR - MINUTE)
add(IDS["xinyi"], "me", "生日快樂！🎂 晚上的餐廳我訂好了", DAY + 6 * HOUR)
add(IDS["xinyi"], "xinyi", "謝謝妳～好期待 ❤️", DAY + 5 * HOUR)
add(IDS["guanyu"], "guanyu", "提案裡第 12 頁的數字我再確認一下", 4 * HOUR)
add(IDS["mom"], "mom", "天氣變冷了，記得多穿一點", 2 * DAY)

members = {
    HIKE: [ME] + [IDS[k] for k in ("yating", "bohan", "jiahao", "zixuan")],
    FAMILY: [ME, IDS["mom"], IDS["grandpa"]],
    WORK: [ME] + [IDS[k] for k in ("guanyu", "peishan", "siyu")],
}
names = {HIKE: "週末爬山小隊", FAMILY: "陳家 🏠", WORK: "設計組"}
unread = {HIKE: 0, FAMILY: 2, WORK: 1, IDS["yating"]: 2, IDS["shihan"]: 1}
read_by = {HIKE: {m: NOW for m in members[HIKE][1:]}, FAMILY: {IDS["mom"]: NOW}, IDS["xinyi"]: {IDS["xinyi"]: NOW}}


def preview(chat_id, m):
    if m["kind"] == "image":
        body = "傳送了照片"
    elif m["kind"] == "file":
        body = "傳送了檔案"
    else:
        body = m["text"]
    if chat_id.startswith("c"):
        who = "你" if m["mine"] else m["sender_name"]
        return who + ("" if m["kind"] in ("image", "file") else "：") + body
    return ("你" if m["mine"] and m["kind"] != "text" else "") + body


chats = []
for chat_id, history in messages.items():
    last = history[-1]
    group = chat_id.startswith("c")
    row = {"id": chat_id, "kind": "group" if group else "direct", "last_message": preview(chat_id, last), "unread": unread.get(chat_id, 0),
           "updated_at": last["time"], "has_messages": True, "member_count": len(members.get(chat_id, [])), "read_by": read_by.get(chat_id, {})}
    if group:
        row.update(name=names[chat_id], members=members[chat_id])
    else:
        key = KEY_OF[chat_id]
        row.update(name=PEOPLE[key][0], avatar=AVATAR + key)
    chats.append(row)

out = sys.stdout.buffer


def send(obj):
    out.write(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
    out.flush()


def reply(rid, result):
    send({"id": rid, "ok": True, "result": result})


for line in sys.stdin.buffer:
    request = json.loads(line)
    method, rid, params = request.get("method"), request.get("id"), request.get("params") or {}
    if method == "initialize":
        reply(rid, {"mode": "live", "capabilities": {"text": True, "contacts": True, "groups": True, "stickers": True, "files": True, "images": True,
                                                       "reply": True, "unsend": True, "read_receipts": True, "screenshot": True}})
        send({"event": "session", "data": {"state": "ready", "message": "", "profile": me, "persistent": True}})
        send({"event": "snapshot", "data": {"profile": me, "contacts": contacts, "chats": chats, "people": {}}})
    elif method == "messages.list":
        chat_id = params.get("chat_id", "")
        reply(rid, {"messages": messages.get(chat_id, []), "read_by": read_by.get(chat_id, {})})
    elif method == "messages.before":
        reply(rid, {"messages": [], "more": False})
    elif method in ("images.fetch", "videos.fetch"):
        key = pictures.get(str(params.get("message_id")))
        path = os.path.join(PHOTOS, key + ".jpg") if key else ""
        if path and os.path.exists(path):
            reply(rid, {"path": path})
        else:
            send({"id": rid, "ok": False, "error": {"code": "image_unavailable", "message": "無法下載照片"}})
    elif method == "shutdown":
        reply(rid, {})
        sys.exit(0)
    else:
        reply(rid, {})
