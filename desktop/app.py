import base64
import datetime
import json
import os
from pathlib import Path
import time
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .ipc import Core
from .model import Notes, State, media_label
from .pictures import Pictures

ASSETS = Path(__file__).with_name("assets")


def box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css=None):
    widget = Gtk.Box(orientation=orientation, spacing=spacing)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def label(text="", css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def button(text, callback=None, css=None, icon=None):
    widget = Gtk.Button()
    if icon:
        contents = box(Gtk.Orientation.HORIZONTAL, 7)
        contents.append(Gtk.Image.new_from_icon_name(icon))
        if text:
            contents.append(label(text))
        widget.set_child(contents)
    else:
        widget.set_label(text)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    if callback:
        widget.connect("clicked", lambda *_: callback())
    widget.set_tooltip_text(text)
    return widget


def clear(widget):
    child = widget.get_first_child()
    while child:
        next_child = child.get_next_sibling()
        widget.remove(child)
        child = next_child


def padded(widget, top=0, end=0, bottom=0, start=0):
    widget.set_margin_top(top)
    widget.set_margin_end(end)
    widget.set_margin_bottom(bottom)
    widget.set_margin_start(start)
    return widget


def scroll(child, horizontal=False):
    widget = Gtk.ScrolledWindow()
    widget.set_policy(Gtk.PolicyType.AUTOMATIC if horizontal else Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_child(child)
    widget.set_vexpand(True)
    return widget


def avatar(name, large=False):
    initials = "".join(str(name).split())[:1] or "L"
    widget = label(initials, "avatar large" if large else "avatar")
    widget.set_xalign(0.5)
    widget.set_valign(Gtk.Align.CENTER)
    widget.set_halign(Gtk.Align.START)
    widget.set_size_request(66 if large else 42, 66 if large else 42)
    colors = ["sage", "peach", "lavender", "sand"]
    widget.add_css_class(colors[sum(ord(c) for c in str(name)) % len(colors)])
    return widget


class Application(Gtk.Application):
    def __init__(self, options):
        app_id = "org.matrixline.Desktop.Demo" if options.demo else "org.matrixline.Desktop.Live"
        if options.smoke_test:
            app_id += ".Smoke" + uuid.uuid4().hex[:8]
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.options = options
        self.state = State(mode="demo" if options.demo else "live")
        self.notes = Notes()
        self.core = None
        self.pictures = Pictures(self.dispatch)
        self.window = None
        self.navigation = "chats"
        self.pending = {}
        self.dialogs = set()
        self.generation = 0
        self.unread_only = False
        self.smoke = {"checks": [], "mode": self.state.mode}
        self.exit_status = 0
        self.connect("activate", self.activate)
        self.connect("shutdown", self.shutdown_core)

    def dispatch(self, callback, *args):
        def invoke():
            if self.window:
                callback(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(invoke)

    def activate(self, *_):
        if self.window:
            self.window.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ASSETS / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title("LINE Desktop" + (" · 離線展示" if self.options.demo else ""))
        self.window.set_default_size(1370, 870)
        self.window.set_size_request(1050, 650)
        header = Gtk.HeaderBar()
        heading = box(Gtk.Orientation.HORIZONTAL, 12)
        heading.append(label("LINE", "wordmark"))
        heading.append(label("通訊，簡單一點。", "tagline"))
        header.set_title_widget(heading)
        self.mode_label = label("● 離線展示 · 不連接 LINE" if self.options.demo else "● LINE 連線", "mode-pill")
        header.pack_end(self.mode_label)
        self.window.set_titlebar(header)
        self.root = box()
        self.notice = label("", "notice", True)
        self.notice.set_visible(False)
        self.root.append(self.notice)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.set_vexpand(True)
        self.root.append(self.pages)
        self.build_login()
        self.build_workspace()
        self.window.set_child(self.root)
        self.pages.set_visible_child_name("login")
        self.window.present()
        try:
            self.core = Core(self.options.demo, self.dispatch, self.on_event)
            self.core.request("initialize", callback=self.initialized)
        except OSError:
            self.show_notice("無法啟動 LINE 核心。請先建置程式，或確認 LINE_APP_CORE 指向可執行檔。", True)
            self.auth_status.set_text("核心尚未啟動。請關閉後重新開啟程式。")
        if self.options.smoke_test:
            GLib.timeout_add(250, self.smoke_tick)

    def initialized(self, response):
        if not response.get("ok"):
            self.show_notice(self.error(response), True)
            return
        result = response.get("result", {})
        self.state.mode = result.get("mode", self.state.mode)
        self.state.capabilities = result.get("capabilities", {})
        self.update_capabilities()

    def build_login(self):
        outer = box(Gtk.Orientation.HORIZONTAL, 0, "login")
        art = box(css="login-art")
        art.set_hexpand(True)
        art.set_valign(Gtk.Align.FILL)
        padded(art, 58, 50, 52, 50)
        art.append(label("LINE", "login-wordmark"))
        art.append(padded(label("把日常，\n聊成喜歡的樣子。", "hero-title", True), 30))
        art.append(padded(label("每一句問候，都是靠近彼此的一小步。", "hero-subtitle", True), 18))
        picture = Gtk.Picture.new_for_filename(str(ASSETS / "landscape.svg"))
        picture.set_can_shrink(True)
        picture.set_vexpand(True)
        art.append(padded(picture, 28, 0, 28))
        art.append(label("保留一點時間，給在乎的人。", "hero-subtitle"))
        outer.append(art)
        card = box(spacing=15, css="login-card")
        card.set_size_request(420, -1)
        card.set_valign(Gtk.Align.CENTER)
        padded(card, 40, 60, 40, 60)
        card.append(label("歡迎回來", "page-title"))
        card.append(label("登入 LINE，繼續你的對話。", "muted"))
        switches = box(Gtk.Orientation.HORIZONTAL, 8)
        self.qr_tab = button("行動條碼登入", lambda: self.login_view("qr"), "tab")
        self.email_tab = button("電子郵件登入", lambda: self.login_view("email"), "tab")
        switches.append(self.qr_tab)
        switches.append(self.email_tab)
        card.append(padded(switches, 12))
        self.auth_pages = Gtk.Stack()
        qr = box(spacing=15)
        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(218, 218)
        self.qr_picture.set_visible(False)
        qr.append(self.qr_picture)
        self.qr_placeholder = box(spacing=12, css="qr-placeholder")
        self.qr_placeholder.set_size_request(218, 218)
        qr_icon = Gtk.Image.new_from_icon_name("view-grid-symbolic")
        qr_icon.set_pixel_size(70)
        self.qr_placeholder.append(qr_icon)
        self.qr_placeholder.append(label("使用手機 LINE 掃描", "muted"))
        qr.append(self.qr_placeholder)
        self.qr_button = button("產生登入行動條碼", self.start_qr, "primary")
        qr.append(self.qr_button)
        qr.append(label("開啟手機 LINE › 設定 › 行動條碼登入", "small muted", True))
        self.auth_pages.add_named(qr, "qr")
        email = box(spacing=12)
        email.append(label("電子郵件", "small"))
        self.email_entry = Gtk.Entry(placeholder_text="name@example.com")
        self.email_entry.set_input_purpose(Gtk.InputPurpose.EMAIL)
        email.append(self.email_entry)
        email.append(label("密碼", "small"))
        self.password_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.password_entry.set_property("placeholder-text", "LINE 帳號密碼")
        self.password_entry.connect("activate", lambda *_: self.start_email())
        email.append(self.password_entry)
        self.email_button = button("登入", self.start_email, "primary")
        email.append(self.email_button)
        email.append(label("請先在手機 LINE 設定電子郵件與密碼。", "small muted", True))
        self.auth_pages.add_named(email, "email")
        card.append(self.auth_pages)
        self.pin_label = label("", "pin")
        self.pin_label.set_visible(False)
        card.append(self.pin_label)
        self.auth_status = label("帳號憑證由系統鑰匙圈保管。", "small muted", True)
        card.append(self.auth_status)
        self.cancel_login = button("取消登入", self.cancel_auth, "quiet")
        self.cancel_login.set_visible(False)
        card.append(self.cancel_login)
        card.append(label("非官方 Linux 用戶端 · 僅顯示實際可用功能", "fine-print", True))
        outer.append(card)
        self.pages.add_named(outer, "login")
        self.login_view("qr")

    def login_view(self, name):
        self.auth_pages.set_visible_child_name(name)
        for widget, selected in ((self.qr_tab, name == "qr"), (self.email_tab, name == "email")):
            if selected:
                widget.add_css_class("active")
            else:
                widget.remove_css_class("active")

    def start_qr(self):
        if self.core:
            self.auth_busy(True)
            self.qr_picture.set_visible(False)
            self.qr_placeholder.set_visible(True)
            self.auth_status.set_text("正在取得行動條碼…")
            self.core.request("login.qr", callback=self.auth_reply)

    def start_email(self):
        email = self.email_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not email or not password:
            self.auth_status.set_text("請輸入電子郵件與密碼。")
            return
        if self.core:
            self.auth_busy(True)
            self.auth_status.set_text("正在登入，請稍候…")
            self.core.request("login.email", {"email": email, "password": password}, self.auth_reply)
        self.password_entry.set_text("")

    def auth_busy(self, busy):
        self.qr_button.set_sensitive(not busy)
        self.email_button.set_sensitive(not busy)
        self.cancel_login.set_visible(busy)
        if not busy:
            self.pin_label.set_visible(False)

    def auth_reply(self, response):
        if not response.get("ok"):
            self.auth_busy(False)
            self.auth_status.set_text(self.error(response))

    def cancel_auth(self):
        self.password_entry.set_text("")
        if self.core:
            self.core.request("login.cancel", callback=self.auth_reply)
        self.auth_busy(False)
        self.qr_picture.set_visible(False)
        self.qr_placeholder.set_visible(True)
        self.auth_status.set_text("已取消。你可以重新登入。")

    def build_workspace(self):
        shell = box(Gtk.Orientation.HORIZONTAL)
        rail = box(spacing=9, css="rail")
        rail.set_size_request(74, -1)
        padded(rail, 24, 9, 16, 9)
        logo = label("LINE", "rail-logo")
        logo.set_xalign(0.5)
        rail.append(padded(logo, 0, 0, 20))
        self.nav_buttons = {}
        for key, title, icon in (("chats", "聊天", "user-available-symbolic"), ("contacts", "好友", "system-users-symbolic"), ("notes", "本機筆記", "document-edit-symbolic")):
            item = self.rail_button(title, icon, lambda value=key: self.navigate(value))
            self.nav_buttons[key] = item
            rail.append(item)
        spacer = box()
        spacer.set_vexpand(True)
        rail.append(spacer)
        rail.append(self.rail_button("設定", "emblem-system-symbolic", lambda: self.navigate("settings")))
        self.self_button = self.rail_button("個人檔案", "avatar-default-symbolic", lambda: self.navigate("profile"))
        rail.append(self.self_button)
        shell.append(rail)
        side = box(css="sidebar")
        side.set_hexpand(False)
        side.set_size_request(283, -1)
        top = padded(box(spacing=17), 26, 20, 12, 21)
        titlebar = box(Gtk.Orientation.HORIZONTAL, 8)
        self.side_title = label("聊天", "page-title")
        self.side_title.set_hexpand(True)
        titlebar.append(self.side_title)
        self.refresh_button = button("", self.refresh, "icon-button", "view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("重新整理聊天與好友")
        titlebar.append(self.refresh_button)
        top.append(titlebar)
        self.search = Gtk.SearchEntry(placeholder_text="搜尋聊天或好友")
        self.search.connect("search-changed", lambda *_: self.render_list())
        top.append(self.search)
        filters = box(Gtk.Orientation.HORIZONTAL, 7)
        self.all_filter = button("全部", lambda: self.filter_unread(False), "filter active")
        self.unread_filter = button("未讀", lambda: self.filter_unread(True), "filter")
        filters.append(self.all_filter)
        filters.append(self.unread_filter)
        top.append(filters)
        side.append(top)
        self.list_caption = padded(label("最近的對話", "section-label"), 7, 22, 7, 22)
        side.append(self.list_caption)
        self.chat_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.chat_list.add_css_class("chat-list")
        side.append(scroll(self.chat_list))
        self.connection = padded(label("● 正在連線", "connection"), 15, 20, 16, 23)
        side.append(self.connection)
        shell.append(side)
        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content.set_hexpand(True)
        self.build_chat()
        self.build_settings()
        shell.append(self.content)
        self.pages.add_named(shell, "workspace")
        self.navigate("chats")

    def rail_button(self, title, icon, callback):
        item = button("", callback, "rail-button")
        contents = box(spacing=6)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(22)
        contents.append(image)
        text = label(title)
        text.set_xalign(0.5)
        contents.append(text)
        item.set_child(contents)
        item.set_tooltip_text(title)
        return item

    def build_chat(self):
        chat = box()
        head = padded(box(Gtk.Orientation.HORIZONTAL, 12, "chat-header"), 17, 24, 17, 24)
        self.header_avatar = avatar("L")
        head.append(self.header_avatar)
        titles = box(spacing=6)
        titles.set_hexpand(True)
        self.chat_title = label("讓對話，從這裡開始。", "chat-title")
        self.chat_subtitle = label("選擇左側聊天或好友", "small muted")
        titles.append(self.chat_title)
        titles.append(self.chat_subtitle)
        head.append(titles)
        self.capability_widgets = []
        for title, icon, capability in (("語音通話", "call-start-symbolic", "calls"), ("視訊通話", "camera-video-symbolic", "calls")):
            action = button("", css="icon-button", icon=icon)
            action.set_tooltip_text(title + " · 目前不支援")
            head.append(action)
            self.capability_widgets.append((action, capability, title))
        self.details_toggle = button("", self.toggle_details, "icon-button", "sidebar-show-right-symbolic")
        self.details_toggle.set_tooltip_text("顯示或隱藏聊天資訊")
        head.append(self.details_toggle)
        chat.append(head)
        body = box(Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        conversation = box(css="conversation")
        conversation.set_hexpand(True)
        self.history = padded(box(spacing=20), 22, 28, 25, 28)
        self.history_scroll = scroll(self.history)
        conversation.append(self.history_scroll)
        self.empty_chat()
        composer = padded(box(spacing=9, css="composer"), 13, 22, 12, 22)
        tools = box(Gtk.Orientation.HORIZONTAL, 8)
        self.sticker_button = button("貼圖", self.open_stickers, "tool", "face-smile-symbolic")
        tools.append(self.sticker_button)
        self.capability_widgets.append((self.sticker_button, "stickers", "貼圖"))
        self.file_button = button("檔案", self.choose_file, "tool", "mail-attachment-symbolic")
        tools.append(self.file_button)
        self.capability_widgets.append((self.file_button, "files", "傳送檔案"))
        image_button = button("照片", css="tool", icon="image-x-generic-symbolic")
        tools.append(image_button)
        self.capability_widgets.append((image_button, "images", "傳送照片"))
        local = button("本機筆記", self.edit_note, "tool", "document-edit-symbolic")
        tools.append(local)
        composer.append(tools)
        input_row = box(Gtk.Orientation.HORIZONTAL, 12)
        self.compose = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.compose.add_css_class("compose-input")
        self.compose.set_hexpand(True)
        self.compose.set_size_request(-1, 54)
        self.compose.get_buffer().connect("changed", self.draft_changed)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.composer_key)
        self.compose.add_controller(key)
        compose_scroll = Gtk.ScrolledWindow()
        compose_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        compose_scroll.set_max_content_height(120)
        compose_scroll.set_child(self.compose)
        compose_scroll.set_hexpand(True)
        input_row.append(compose_scroll)
        self.send_button = button("", self.send_text, "send", "mail-send-symbolic")
        self.send_button.set_tooltip_text("傳送訊息")
        self.send_button.set_valign(Gtk.Align.END)
        input_row.append(self.send_button)
        composer.append(input_row)
        hints = box(Gtk.Orientation.HORIZONTAL)
        hint = label("輸入訊息，分享今天的一點小事。", "fine-print")
        hint.set_hexpand(True)
        hints.append(hint)
        hints.append(label("Enter 傳送 · Shift + Enter 換行", "fine-print"))
        composer.append(hints)
        conversation.append(composer)
        body.append(conversation)
        self.details = box(css="details")
        self.details.set_size_request(248, -1)
        self.detail_contents = padded(box(spacing=16), 27, 20, 22, 20)
        self.details.append(scroll(self.detail_contents))
        body.append(self.details)
        chat.append(body)
        self.content.add_named(chat, "chat")
        self.render_details()

    def build_settings(self):
        page = padded(box(spacing=21, css="settings-page"), 40, 48, 40, 48)
        self.settings_heading = label("設定", "hero-small")
        page.append(self.settings_heading)
        self.profile_card = padded(box(spacing=12, css="settings-card"), 24, 24, 24, 24)
        page.append(self.profile_card)
        card = padded(box(spacing=15, css="settings-card"), 24, 24, 24, 24)
        card.append(label("讓聯繫更安心", "chat-title"))
        card.append(label("登入憑證交由系統鑰匙圈保管。訊息僅留在本次工作階段；本機筆記單獨存放於這台電腦。", "muted", True))
        self.persistence_label = label("", "small muted", True)
        card.append(self.persistence_label)
        page.append(card)
        self.features_card = padded(box(spacing=12, css="settings-card"), 24, 24, 24, 24)
        page.append(self.features_card)
        self.logout_button = button("登出", self.logout, "danger")
        self.logout_button.set_halign(Gtk.Align.START)
        page.append(self.logout_button)
        self.content.add_named(scroll(page), "settings")

    def navigate(self, destination):
        self.navigation = destination
        for key, widget in self.nav_buttons.items():
            if destination == key:
                widget.add_css_class("active")
            else:
                widget.remove_css_class("active")
        if destination in ("settings", "profile"):
            self.settings_heading.set_text("個人檔案" if destination == "profile" else "設定")
            self.content.set_visible_child_name("settings")
            self.render_settings()
        else:
            self.content.set_visible_child_name("chat")
            self.side_title.set_text({"contacts": "好友", "notes": "本機筆記"}.get(destination, "聊天"))
            self.list_caption.set_text({"contacts": "我的好友", "notes": "選擇對話以編輯本機筆記"}.get(destination, "最近的對話"))
            self.render_list()
            if destination == "notes":
                self.render_details()
                self.details.set_visible(True)

    def render_list(self):
        clear(self.chat_list)
        query = self.search.get_text().casefold().strip()
        contacts = self.navigation == "contacts"
        values = list((self.state.contacts if contacts else self.state.chats).values())
        values.sort(key=(lambda item: item.get("name", "")) if contacts else (lambda item: -item.get("updated_at", 0)))
        count = 0
        for item in values:
            if query and query not in (item.get("name", "") + " " + str(item.get("last_message", ""))).casefold():
                continue
            if self.unread_only and not contacts and not item.get("unread"):
                continue
            count += 1
            item_id = str(item["id"])
            row = button("", lambda value=item_id, is_contact=contacts: self.open_contact(value) if is_contact else self.select_chat(value), "chat-row")
            if self.state.selected == item_id:
                row.add_css_class("selected")
            contents = box(Gtk.Orientation.HORIZONTAL, 11)
            contents.append(avatar(item.get("name", "")))
            info = box(spacing=7)
            info.set_hexpand(True)
            info.set_size_request(100, -1)
            line = box(Gtk.Orientation.HORIZONTAL, 5)
            name = label(item.get("name", "未命名對話"), "row-title")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(22)
            name.set_hexpand(True)
            line.append(name)
            if not contacts:
                line.append(label(self.time_text(item.get("updated_at", 0), date=True), "tiny muted"))
            info.append(line)
            preview = str(item.get("status" if contacts else "last_message", "") or "開始聊天")
            preview_label = label(preview.replace("\n", " "), "row-preview")
            preview_label.set_ellipsize(Pango.EllipsizeMode.END)
            preview_label.set_max_width_chars(28)
            info.append(preview_label)
            contents.append(info)
            if item.get("unread") and not contacts:
                unread = label(str(item["unread"]), "unread")
                unread.set_valign(Gtk.Align.CENTER)
                contents.append(unread)
            row.set_child(contents)
            self.chat_list.append(row)
        if not count:
            self.chat_list.append(padded(label("找不到符合的對話" if query else "目前沒有對話\n可以從好友開始聊天。", "muted", True), 35, 20, 20, 20))

    def filter_unread(self, enabled):
        self.unread_only = enabled
        for widget, active in ((self.all_filter, not enabled), (self.unread_filter, enabled)):
            widget.add_css_class("active") if active else widget.remove_css_class("active")
        self.render_list()

    def open_contact(self, contact_id):
        contact = self.state.contacts[contact_id]
        self.state.chats.setdefault(contact_id, {"id": contact_id, "name": contact.get("name", "好友"), "kind": "direct", "last_message": "", "unread": 0, "updated_at": 0})
        self.select_chat(contact_id)

    def select_chat(self, chat_id):
        if chat_id not in self.state.chats:
            return
        self.state.selected = chat_id
        chat = self.state.chats[chat_id]
        chat["unread"] = 0
        self.chat_title.set_text(chat.get("name", "對話"))
        kind = "群組" if chat.get("kind") == "group" else "聊天室" if chat.get("kind") == "room" else "個人聊天"
        count = chat.get("member_count", 0)
        self.chat_subtitle.set_text(kind + (f" · {count} 位成員" if count else "") + (" · 離線展示" if self.state.mode == "demo" else ""))
        self.header_avatar.set_text(chat.get("name", "L")[:1])
        self.compose.get_buffer().set_text(self.state.drafts.get(chat_id, ""))
        self.render_list()
        self.render_messages()
        self.render_details()
        self.update_send()
        generation = self.generation
        def loaded(response):
            if generation != self.generation:
                return
            if response.get("ok"):
                for message in response.get("result", {}).get("messages", []):
                    self.state.add_message(message)
                if self.state.selected == chat_id:
                    self.render_messages()
                    self.mark_read()
            else:
                self.show_notice(self.error(response), True)
        if self.core:
            self.core.request("messages.list", {"chat_id": chat_id, "limit": 50}, loaded)
        if self.navigation == "notes":
            self.edit_note()

    def empty_chat(self):
        clear(self.history)
        empty = box(spacing=16, css="empty-conversation")
        empty.set_vexpand(True)
        empty.set_valign(Gtk.Align.CENTER)
        picture = Gtk.Picture.new_for_filename(str(ASSETS / "landscape.svg"))
        picture.set_size_request(290, 220)
        picture.set_can_shrink(True)
        empty.append(picture)
        title = label("每一天，都有值得分享的小事。", "chat-title")
        title.set_xalign(0.5)
        empty.append(title)
        subtitle = label("選擇左側對話，開始今天的問候。", "muted")
        subtitle.set_xalign(0.5)
        empty.append(subtitle)
        self.history.append(empty)

    def render_messages(self):
        clear(self.history)
        history = self.state.messages.get(self.state.selected, [])
        if not history:
            self.history.append(padded(label("打聲招呼，讓對話開始吧。", "day-label"), 60))
        day = None
        for message in history:
            moment = self.time_text(message.get("time", 0), full=True)
            if moment != day:
                day = moment
                stamp = label(moment or "今天", "day-label")
                stamp.set_xalign(0.5)
                self.history.append(stamp)
            mine = bool(message.get("mine"))
            line = box(Gtk.Orientation.HORIZONTAL, 9)
            line.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
            if not mine:
                line.append(avatar(message.get("sender_name", "LINE")))
            contents = box(spacing=6)
            if not mine:
                contents.append(label(message.get("sender_name", "LINE"), "sender"))
            text = message.get("text") or media_label(message)
            if message.get("kind") == "file":
                text = "▤  " + message.get("filename", "檔案") + "\n" + text
            if message.get("kind") == "sticker":
                contents.append(self.sticker_picture(message.get("url", ""), 130))
            bubble = label(text, "bubble mine" if mine else "bubble", True)
            bubble.set_selectable(True)
            bubble.set_max_width_chars(36)
            contents.append(bubble)
            if message.get("kind") == "file" and self.state.mode != "demo":
                contents.append(button("另存檔案", lambda item=message: self.download_file(item), "quiet"))
            status = {"pending": "傳送中…", "failed": "傳送失敗 · 內容保留於草稿"}.get(message.get("status"), "已傳送" if mine else "")
            meta = label((status + "  " if status else "") + self.time_text(message.get("time", 0)), "message-meta")
            meta.set_xalign(1 if mine else 0)
            if message.get("status") == "failed":
                meta.add_css_class("failed")
            contents.append(meta)
            line.append(contents)
            self.history.append(line)
        GLib.idle_add(self.scroll_bottom)

    def scroll_bottom(self):
        adjustment = self.history_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def draft_changed(self, *_):
        if self.state.selected:
            buffer = self.compose.get_buffer()
            self.state.drafts[self.state.selected] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        self.update_send()

    def update_send(self):
        self.send_button.set_sensitive(bool(self.state.selected and self.state.session == "ready" and self.state.capabilities.get("text") and self.state.drafts.get(self.state.selected, "").strip()))
        self.compose.set_editable(bool(self.state.selected))

    def composer_key(self, controller, keyval, keycode, modifiers):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not modifiers & Gdk.ModifierType.SHIFT_MASK:
            if self.send_button.get_sensitive():
                self.send_text()
            return True
        return False

    def send_text(self):
        chat_id = self.state.selected
        text = self.state.drafts.get(chat_id, "")
        if not self.send_button.get_sensitive() or not self.core:
            return
        if len(text.encode()) > 65536:
            self.show_notice("訊息太長，請分成幾段傳送。", True)
            return
        temporary = "pending-" + uuid.uuid4().hex
        generation = self.generation
        message = {"id": temporary, "chat_id": chat_id, "text": text, "mine": True, "time": int(time.time() * 1000), "kind": "text", "status": "pending"}
        self.state.add_message(message)
        self.state.drafts[chat_id] = ""
        self.compose.get_buffer().set_text("")
        self.render_messages()
        def sent(response):
            if generation != self.generation:
                return
            if response.get("ok"):
                self.state.messages[chat_id] = [item for item in self.state.messages.get(chat_id, []) if item["id"] != temporary]
                actual = response.get("result", {}).get("message")
                if actual:
                    self.state.add_message(actual)
            else:
                message["status"] = "failed"
                self.state.drafts[chat_id] = text + ("\n" + self.state.drafts[chat_id] if self.state.drafts.get(chat_id) else "")
                self.show_notice(self.error(response), True)
                if self.state.selected == chat_id:
                    self.compose.get_buffer().set_text(self.state.drafts[chat_id])
            if self.state.selected == chat_id:
                self.render_messages()
            self.render_list()
        self.core.request("messages.send", {"chat_id": chat_id, "text": text}, sent)

    def render_details(self):
        clear(self.detail_contents)
        chat = self.state.chats.get(self.state.selected)
        name = chat.get("name", "你的對話空間") if chat else "你的對話空間"
        portrait = avatar(name, True)
        portrait.set_halign(Gtk.Align.CENTER)
        self.detail_contents.append(portrait)
        title = label(name, "detail-title", True)
        title.set_xalign(0.5)
        self.detail_contents.append(title)
        subtitle = label("好好聊天，慢慢靠近。", "tiny muted")
        subtitle.set_xalign(0.5)
        self.detail_contents.append(subtitle)
        self.detail_contents.append(Gtk.Separator())
        tabs = box(Gtk.Orientation.HORIZONTAL, 5)
        tabs.append(label("本機筆記", "detail-tab"))
        for title in ("LINE 記事本", "相簿"):
            item = button(title, css="detail-disabled")
            item.set_sensitive(False)
            item.set_tooltip_text(title + "目前不支援")
            tabs.append(item)
        self.detail_contents.append(tabs)
        note = self.notes.read(self.account_key(), self.state.selected) if chat else ""
        card = padded(box(spacing=11, css="note-card"), 15, 14, 15, 14)
        card.append(label("只在這台電腦", "note-tag"))
        card.append(label("留下一點小提醒", "row-title"))
        card.append(label(note if note else "集合時間、靈感、下次想說的話。\n替這段對話留一張小紙條。", "note-text", True))
        self.note_button = button("編輯本機筆記", self.edit_note, "quiet")
        self.note_button.set_sensitive(bool(chat))
        card.append(self.note_button)
        self.detail_contents.append(card)
        self.detail_contents.append(label("本機筆記不會同步到 LINE，\n也不會傳送給聊天成員。", "tiny muted", True))
        self.detail_contents.append(Gtk.Separator())
        self.detail_contents.append(label("聊天資訊", "section-label"))
        if chat:
            self.detail_contents.append(label("群組聊天" if chat.get("kind") == "group" else "個人聊天", "small muted"))
            if chat.get("member_count"):
                self.detail_contents.append(label(f"{chat['member_count']} 位成員", "small muted"))
        artwork = Gtk.Picture.new_for_filename(str(ASSETS / "landscape.svg"))
        artwork.set_can_shrink(True)
        artwork.set_size_request(190, 145)
        self.detail_contents.append(padded(artwork, 14))
        self.detail_contents.append(label("把日常的小風景，分享給重要的人。", "fine-print", True))

    def account_key(self):
        return self.state.mode + ":" + str(self.state.profile.get("id", "anonymous"))

    def edit_note(self):
        if not self.state.selected:
            return
        chat_id = self.state.selected
        account = self.account_key()
        generation = self.generation
        dialog = Gtk.Window(title="本機筆記", transient_for=self.window, modal=True)
        self.track_dialog(dialog)
        dialog.set_default_size(440, 360)
        contents = padded(box(spacing=14), 24, 24, 24, 24)
        contents.append(label("本機筆記", "page-title"))
        contents.append(label("只儲存在這台電腦，不會傳送或同步到 LINE。", "small muted", True))
        editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        editor.add_css_class("note-editor")
        editor.get_buffer().set_text(self.notes.read(account, chat_id))
        contents.append(scroll(editor))
        actions = box(Gtk.Orientation.HORIZONTAL, 9)
        actions.set_halign(Gtk.Align.END)
        actions.append(button("取消", dialog.close, "quiet"))
        def save():
            if generation != self.generation:
                return
            buffer = editor.get_buffer()
            try:
                self.notes.save(account, chat_id, buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))
                self.render_details()
                dialog.close()
            except OSError:
                self.show_notice("無法儲存本機筆記。請確認資料目錄的權限。", True)
        save_button = button("儲存筆記", save, "primary")
        actions.append(save_button)
        contents.append(actions)
        dialog.set_child(contents)
        dialog.present()
        self.note_dialog = dialog
        self.note_editor = editor
        self.note_save_button = save_button

    def toggle_details(self):
        self.details.set_visible(not self.details.get_visible())

    def render_settings(self):
        clear(self.profile_card)
        profile = self.state.profile
        self.profile_card.append(avatar(profile.get("name", "LINE"), True))
        self.profile_card.append(label(profile.get("name", "尚未登入"), "page-title"))
        self.profile_card.append(label(profile.get("status", "留一點時間，給喜歡的人和事。"), "muted", True))
        self.profile_card.append(label("離線展示帳號 · 不連接 LINE" if self.state.mode == "demo" else "LINE 帳號", "small muted"))
        clear(self.features_card)
        self.features_card.append(label("此版本的功能", "chat-title"))
        titles = {"text": "文字訊息", "contacts": "好友", "groups": "群組聊天", "stickers": "貼圖", "files": "檔案傳送", "images": "照片傳送", "calls": "通話", "notes": "LINE 記事本", "albums": "LINE 相簿"}
        for key, title in titles.items():
            row = box(Gtk.Orientation.HORIZONTAL, 20)
            item = label(title, "small")
            item.set_hexpand(True)
            row.append(item)
            row.append(label("可使用" if self.state.capabilities.get(key) else "目前不支援", "available" if self.state.capabilities.get(key) else "small muted"))
            self.features_card.append(row)

    def update_capabilities(self):
        for widget, capability, title in self.capability_widgets:
            supported = bool(self.state.capabilities.get(capability))
            widget.set_sensitive(supported)
            widget.set_tooltip_text(title if supported else title + " · 此版本目前不支援")
        self.update_send()

    def refresh(self):
        if self.core:
            self.core.request("chats.refresh", callback=lambda reply: self.show_notice("已重新整理") if reply.get("ok") else self.show_notice(self.error(reply), True))

    def mark_read(self):
        messages = self.state.messages.get(self.state.selected, [])
        if messages and self.core:
            self.core.request("messages.read", {"chat_id": self.state.selected, "message_id": messages[-1]["id"]})

    def logout(self):
        if not self.core:
            return
        def finished(response):
            if not response.get("ok"):
                self.show_notice(self.error(response), True)
                return
            self.clear_session()
        self.core.request("logout", callback=finished)

    def clear_session(self):
        self.generation += 1
        for dialog in list(self.dialogs):
            if isinstance(dialog, Gtk.NativeDialog):
                dialog.destroy()
            else:
                dialog.close()
        self.dialogs.clear()
        self.state.clear()
        self.compose.get_buffer().set_text("")
        self.password_entry.set_text("")
        self.email_entry.set_text("")
        self.search.set_text("")
        self.qr_picture.set_paintable(None)
        self.qr_picture.set_visible(False)
        self.qr_placeholder.set_visible(True)
        self.pin_label.set_text("")
        self.auth_busy(False)
        self.pages.set_visible_child_name("login")
        self.render_list()
        self.render_details()
        self.empty_chat()
        self.chat_title.set_text("讓對話，從這裡開始。")
        self.chat_subtitle.set_text("選擇左側聊天或好友")
        self.render_settings()
        self.update_send()

    def track_dialog(self, dialog):
        self.dialogs.add(dialog)
        signal = "response" if isinstance(dialog, Gtk.NativeDialog) else "close-request"
        def closed(*_):
            self.dialogs.discard(dialog)
            return False
        dialog.connect(signal, closed)

    def on_event(self, event, data):
        if event == "session":
            state = data.get("state", "error")
            if state == "logged_out":
                self.clear_session()
                self.auth_status.set_text(data.get("message") or "使用手機 LINE 掃描行動條碼，或以電子郵件登入。")
            else:
                self.state.session = state
                if data.get("profile"):
                    self.state.profile = data["profile"]
                if state == "ready":
                    self.pages.set_visible_child_name("workspace")
                    self.auth_busy(False)
                    self.password_entry.set_text("")
                    self.qr_picture.set_paintable(None)
                    self.pin_label.set_text("")
                    self.notice.set_visible(False)
                elif state == "error":
                    self.show_notice(data.get("message", "連線發生錯誤"), True)
                    self.auth_busy(False)
                    self.auth_status.set_text(data.get("message", "請重新登入。"))
                self.connection.set_text("● 離線展示 · 不連接 LINE" if self.state.mode == "demo" else "● 已連線" if state == "ready" else "○ 正在重新連線…" if state == "connecting" else "○ 連線已中斷")
                if "persistent" in data:
                    self.persistence_label.set_text("已使用系統鑰匙圈，下次可恢復登入。" if data["persistent"] else "本次登入僅保留在記憶體。關閉程式後需要重新登入。")
                    if state == "ready" and self.state.mode != "demo" and not data["persistent"]:
                        self.connection.set_text("● 已連線 · 登入未保存至鑰匙圈")
                self.update_send()
        elif event == "snapshot":
            if self.state.session == "logged_out":
                return
            self.state.snapshot(data)
            self.render_list()
            self.render_settings()
            self.render_details()
            if not self.state.selected and self.state.chats:
                self.select_chat(next(iter(self.state.chats)))
        elif event == "message":
            if self.state.session == "logged_out":
                return
            message = data.get("message", {})
            self.state.add_message(message)
            self.render_list()
            if message.get("chat_id") == self.state.selected:
                self.render_messages()
                self.mark_read()
        elif event == "notice":
            self.show_notice(data.get("message", ""), data.get("level") == "error")
        elif event == "auth":
            stage = data.get("stage")
            if stage == "qr":
                self.login_view("qr")
                try:
                    png = base64.b64decode(data.get("qr_png", ""), validate=True)
                    if not png:
                        raise ValueError("missing QR image")
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
                    self.qr_picture.set_paintable(texture)
                    self.qr_picture.set_visible(True)
                    self.qr_placeholder.set_visible(False)
                    self.auth_status.set_text("請使用手機 LINE 掃描上方行動條碼。")
                except (ValueError, GLib.Error):
                    self.auth_status.set_text("無法顯示行動條碼。請改用電子郵件登入，或重新產生。")
                    self.auth_busy(False)
            elif stage == "pin":
                self.pin_label.set_text(str(data.get("pin", "")))
                self.pin_label.set_visible(True)
                self.auth_status.set_text(data.get("message") or "請在手機 LINE 輸入上方驗證碼。")
            elif stage == "error":
                self.auth_busy(False)
                self.auth_status.set_text(data.get("message", "登入失敗，請重新嘗試。"))

    def show_notice(self, message, error=False):
        self.notice.set_text(message)
        self.notice.set_visible(bool(message))
        self.notice.add_css_class("error") if error else self.notice.remove_css_class("error")

    @staticmethod
    def error(response):
        return response.get("error", {}).get("message", "操作失敗，請稍後再試。")

    @staticmethod
    def time_text(milliseconds, date=False, full=False):
        if not milliseconds:
            return ""
        try:
            stamp = datetime.datetime.fromtimestamp(milliseconds / 1000)
            if full:
                return stamp.strftime("%Y 年 %m 月 %d 日")
            if date and stamp.date() != datetime.date.today():
                return stamp.strftime("%m/%d")
            return stamp.strftime("%H:%M")
        except (ValueError, OverflowError, TypeError):
            return ""

    def open_stickers(self):
        if not self.core or not self.state.selected:
            return
        chat_id = self.state.selected
        generation = self.generation
        def loaded(response):
            if generation != self.generation or self.state.session != "ready":
                return
            if not response.get("ok"):
                self.show_notice(self.error(response), True)
                return
            packs = response.get("result", {}).get("packs", [])
            dialog = Gtk.Window(title="貼圖", transient_for=self.window, modal=True)
            self.track_dialog(dialog)
            self.sticker_dialog = dialog
            self.sticker_choices = []
            dialog.set_default_size(440, 360)
            contents = padded(box(spacing=12), 20, 20, 20, 20)
            contents.append(label("貼圖", "page-title"))
            if packs:
                choices = Gtk.DropDown.new_from_strings([pack.get("name", "貼圖包") for pack in packs])
                contents.append(choices)
                grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=3)
                contents.append(grid)
                paging = box(Gtk.Orientation.HORIZONTAL, 8)
                contents.append(paging)
                page = [0]
                def render(*_):
                    self.sticker_choices.clear()
                    clear(grid)
                    clear(paging)
                    pack = packs[choices.get_selected()]
                    stickers = pack.get("stickers", [])
                    for sticker in stickers[page[0] * 48:(page[0] + 1) * 48]:
                        def send(item=sticker, package=pack):
                            if generation != self.generation or self.state.session != "ready":
                                return
                            self.core.request("messages.sticker", {"chat_id": chat_id, "package_id": item.get("package_id", package["id"]), "sticker_id": item["id"]}, lambda result: self.show_notice(self.error(result), True) if not result.get("ok") else None)
                            dialog.close()
                        choice = button("貼圖 " + str(sticker["id"]), send, "quiet")
                        choice.set_child(self.sticker_picture(sticker.get("url", ""), 85))
                        self.sticker_choices.append(choice)
                        grid.insert(choice, -1)
                    def change(amount):
                        page[0] += amount
                        render()
                    previous = button("上一頁", lambda: change(-1), "quiet")
                    previous.set_sensitive(page[0] > 0)
                    following = button("下一頁", lambda: change(1), "quiet")
                    following.set_sensitive((page[0] + 1) * 48 < len(stickers))
                    paging.append(previous)
                    paging.append(label(f"{page[0] + 1} / {max(1, (len(stickers) + 47) // 48)}", "muted"))
                    paging.append(following)
                def selected(*_):
                    page[0] = 0
                    render()
                choices.connect("notify::selected", selected)
                render()
            else:
                contents.append(label("目前沒有可使用的貼圖。", "muted"))
            dialog.set_child(scroll(contents))
            dialog.present()
        self.core.request("stickers.list", callback=loaded)

    def choose_file(self):
        if not self.core or not self.state.selected:
            return
        chat_id = self.state.selected
        generation = self.generation
        dialog = Gtk.FileChooserNative.new("傳送檔案", self.window, Gtk.FileChooserAction.OPEN, "傳送", "取消")
        self.track_dialog(dialog)
        self.file_chooser = dialog
        def selected(chooser, response):
            if generation == self.generation and self.state.session == "ready" and response == Gtk.ResponseType.ACCEPT:
                path = chooser.get_file().get_path()
                if path:
                    self.show_notice("正在傳送檔案…（上限 25 MiB）")
                    self.core.request("files.send", {"chat_id": chat_id, "path": path}, lambda result: self.show_notice(self.error(result), True) if not result.get("ok") else self.show_notice("檔案已傳送。" if self.state.mode != "demo" else "示範檔案已加入對話，未上傳到 LINE。"))
            chooser.destroy()
        dialog.connect("response", selected)
        dialog.show()

    def sticker_picture(self, url, size):
        picture = Gtk.Picture()
        picture.set_size_request(size, size)
        picture.set_can_shrink(True)
        if url.startswith("demo:"):
            picture.set_filename(str(ASSETS / "sticker.svg"))
        elif url:
            def loaded(data):
                try:
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                    if texture.get_width() <= 4096 and texture.get_height() <= 4096:
                        picture.set_paintable(texture)
                except GLib.Error:
                    pass
            self.pictures.load(url, loaded)
        return picture

    def download_file(self, message):
        generation = self.generation
        chooser = Gtk.FileChooserNative.new("另存檔案", self.window, Gtk.FileChooserAction.SAVE, "儲存", "取消")
        self.track_dialog(chooser)
        self.download_chooser = chooser
        chooser.set_current_name(Path(message.get("filename") or "LINE-attachment").name)
        def selected(dialog, response):
            if generation == self.generation and self.state.session == "ready" and response == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file and file.get_path():
                    self.show_notice("正在下載並驗證附件…")
                    self.core.request("files.download", {"chat_id": message["chat_id"], "message_id": message["id"], "path": file.get_path()}, lambda result: self.show_notice("檔案已儲存。" if result.get("ok") else self.error(result), not result.get("ok")))
            dialog.destroy()
        chooser.connect("response", selected)
        chooser.show()

    def shutdown_core(self, *_):
        self.pictures.closed = True
        if self.core:
            self.core.stop()
        self.window = None

    def check(self, name, condition):
        self.smoke["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            self.exit_status = 1

    def smoke_tick(self):
        try:
            return self.smoke_step()
        except Exception as error:
            self.smoke["exception"] = type(error).__name__ + ": " + str(error)
            self.exit_status = 1
            self.finish_smoke()
            return GLib.SOURCE_REMOVE

    def smoke_step(self):
        step = getattr(self, "smoke_phase", 0)
        started = getattr(self, "smoke_started", time.monotonic())
        self.smoke_started = started
        if time.monotonic() - started > 25:
            self.check("core becomes ready", False)
            self.finish_smoke()
            return GLib.SOURCE_REMOVE
        if step == 0:
            self.login_view("email")
            self.email_button.emit("clicked")
            self.check("email form validates empty credentials", "請輸入" in self.auth_status.get_text())
            self.login_view("qr")
            self.on_event("auth", {"stage": "pin", "pin": "1234", "message": "請在手機輸入驗證碼"})
            self.check("PIN view shows verification code", self.pin_label.get_visible() and self.pin_label.get_text() == "1234")
            self.auth_busy(False)
            self.smoke_phase = 1
        elif step == 1 and self.state.session == "ready" and self.state.chats:
            self.check("native workspace ready", self.pages.get_visible_child_name() == "workspace")
            self.check("demo is clearly identified", self.state.mode == "demo" and "離線展示" in self.mode_label.get_text())
            self.smoke_chat = next(iter(self.state.chats))
            name = self.state.chats[self.smoke_chat]["name"]
            self.search.set_text(name)
            self.render_list()
            self.check("search filters actual chat rows", self.chat_list.get_first_child() is not None)
            self.search.set_text("")
            self.render_list()
            self.select_chat(self.smoke_chat)
            self.compose.get_buffer().set_text("GTK 原生元件測試訊息")
            self.send_button.emit("clicked")
            self.smoke_phase = 2
        elif step == 2:
            messages = self.state.messages.get(self.smoke_chat, [])
            if any(item.get("text") == "GTK 原生元件測試訊息" and item.get("status") == "sent" for item in messages):
                self.check("text sends through core and resolves pending", not any(item.get("status") == "pending" for item in messages))
                self.check("messages deduplicate core event and reply", len([item for item in messages if item.get("text") == "GTK 原生元件測試訊息"]) == 1)
                self.compose.get_buffer().set_text("保留中的草稿")
                other_chat = next(key for key in self.state.chats if key != self.smoke_chat)
                self.select_chat(other_chat)
                self.select_chat(self.smoke_chat)
                self.check("draft restores after selecting another chat", self.state.drafts.get(self.smoke_chat) == "保留中的草稿")
                self.note_button.emit("clicked")
                self.note_editor.get_buffer().set_text("本機筆記 GTK 測試")
                self.note_save_button.emit("clicked")
                self.check("local note saved privately", self.notes.read(self.account_key(), self.smoke_chat) == "本機筆記 GTK 測試" and self.notes.path(self.account_key(), self.smoke_chat).stat().st_mode & 0o777 == 0o600)
                self.nav_buttons["contacts"].emit("clicked")
                self.check("contacts navigation uses live widgets", self.side_title.get_text() == "好友")
                self.navigate("settings")
                self.check("settings navigation shows capabilities", self.content.get_visible_child_name() == "settings")
                self.navigate("chats")
                self.select_chat(self.smoke_chat)
                self.compose.get_buffer().set_text("")
                self.check("return from settings shows conversation", self.content.get_visible_child_name() == "chat")
                self.render_list()
                self.sticker_button.emit("clicked")
                self.smoke_phase = 3
        elif step == 3 and getattr(self, "sticker_choices", []):
            self.check("SVG sticker renders in GTK", self.sticker_choices[0].get_child().get_paintable() is not None)
            self.sticker_choices[0].emit("clicked")
            self.smoke_phase = 4
        elif step == 4:
            if any(item.get("kind") == "sticker" for item in self.state.messages.get(self.smoke_chat, [])):
                self.check("sticker picker sends through core", True)
                fixture = Path(self.options.smoke_test) / "demo-file.txt"
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text("GTK demo attachment")
                self.core.request("files.send", {"chat_id": self.smoke_chat, "path": str(fixture.resolve())}, lambda reply: self.check("file send returns a real demo result", reply.get("ok")))
                self.smoke_phase = 5
        elif step == 5:
            if any(item.get("kind") == "file" for item in self.state.messages.get(self.smoke_chat, [])):
                self.check("attachment appears in conversation", True)
                self.smoke_phase = 6
                self.smoke["ready_for_screenshot"] = True
                self.write_smoke()
                self.screenshot_deadline = time.monotonic() + 3
        elif step == 6 and time.monotonic() >= self.screenshot_deadline:
            self.check_stale_dialogs()
            self.finish_smoke()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def check_stale_dialogs(self):
        class Recorder:
            def __init__(self):
                self.calls = []
            def request(self, method, params=None, callback=None):
                self.calls.append((method, params, callback))
        real_core = self.core
        recorder = Recorder()
        self.core = recorder
        try:
            self.choose_file()
            fixture = Gio.File.new_for_path(str((Path(self.options.smoke_test) / "demo-file.txt").resolve()))
            self.file_chooser.set_file(fixture)
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            self.check("stale send test has an actual selected file", self.file_chooser.get_file() is not None)
            self.generation += 1
            self.file_chooser.emit("response", Gtk.ResponseType.ACCEPT)
            self.check("stale file chooser cannot send across sessions", not recorder.calls)
            self.download_file({"chat_id": self.state.selected, "id": "old-file", "filename": "old.txt"})
            self.download_chooser.set_file(fixture)
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            self.check("stale download test has an actual selected file", self.download_chooser.get_file() is not None)
            self.generation += 1
            self.download_chooser.emit("response", Gtk.ResponseType.ACCEPT)
            self.check("stale download chooser cannot act across sessions", not recorder.calls)
            self.open_stickers()
            callback = recorder.calls.pop()[2]
            self.generation += 1
            before = len(self.dialogs)
            callback({"ok": True, "result": {"packs": []}})
            self.check("stale sticker catalog cannot open after account changes", len(self.dialogs) == before)
            self.open_stickers()
            callback = recorder.calls.pop()[2]
            callback({"ok": True, "result": {"packs": [{"id": "demo", "name": "Demo", "stickers": [{"id": "hello", "url": "demo:hello"}]}]}})
            self.generation += 1
            self.sticker_choices[0].emit("clicked")
            self.check("stale sticker button cannot send across sessions", not recorder.calls)
            dialogs = tuple(self.dialogs)
            self.clear_session()
            self.check("logout closes all account dialogs", not self.dialogs and all(not dialog.get_visible() for dialog in dialogs))
        finally:
            self.core = real_core

    def write_smoke(self):
        output = Path(self.options.smoke_test)
        output.mkdir(parents=True, exist_ok=True)
        self.smoke["passed"] = self.exit_status == 0
        (output / "results.json").write_text(json.dumps(self.smoke, ensure_ascii=False, indent=2))

    def finish_smoke(self):
        self.write_smoke()
        self.quit()

    def run(self, argv):
        status = super().run(argv)
        return self.exit_status or status
