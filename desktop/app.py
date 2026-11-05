"""Native GTK 4 LINE client whose layout mirrors index.html element by element."""
import base64
import datetime
import json
from pathlib import Path
import time
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import art
from .art import Art, EMOJI, MOODS
from .ipc import Core
from .model import KEEP_ID, Keep, Notes, Preferences, State, media_label
from .pictures import Pictures
from .ui import (H, V, Responsive, box, button, chat_avatar, clear, dot, file_badge, file_size,
                 icon, icon_button, icon_text, label, person_avatar, scroller, set_accessible_label)

ASSETS = Path(__file__).with_name("assets")
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
MAX_FILE = 25 * 1024 * 1024
MAX_QUEUE = 20
CENTER = Gtk.Align.CENTER
START = Gtk.Align.START
END = Gtk.Align.END
DEMO_NOTE = {"title": "週六，我們去山裡呼吸 🌿", "author": "demo-yiting",
             "body": "集合時間｜11/7（六）09:30\n集合地點｜捷運劍潭站 1 號出口\n\n帶上水、薄外套和一點好心情。\n步道走完，再找間小店喝咖啡。\n下雨就改成城市散步！"}
HELP_LINKS = (
    ("傳送訊息與檔案", "https://help.line.me/line/desktop/?contentId=20007005&lang=zh-Hant"),
    ("語音與視訊通話", "https://help.line.me/line/desktop/pc?contentId=20000270&lang=zh-Hant"),
    ("好友功能", "https://help.line.me/line/desktop/?contentId=20000369&lang=en"),
)


def local_time(milliseconds):
    try:
        return datetime.datetime.fromtimestamp(milliseconds / 1000) if milliseconds else None
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def clock(milliseconds):
    stamp = local_time(milliseconds)
    return stamp.strftime("%H:%M") if stamp else ""


def list_time(milliseconds):
    stamp = local_time(milliseconds)
    if not stamp:
        return "剛剛"
    days = (datetime.date.today() - stamp.date()).days
    if days <= 0:
        return stamp.strftime("%H:%M")
    if days == 1:
        return "昨天"
    if days < 7:
        return WEEKDAYS[stamp.weekday()]
    return f"{stamp.month}/{stamp.day}"


def day_label(milliseconds):
    stamp = local_time(milliseconds) or datetime.datetime.now()
    days = (datetime.date.today() - stamp.date()).days
    text = f"{stamp.month} 月 {stamp.day} 日 {WEEKDAYS[stamp.weekday()]}"
    if days == 0:
        return "今天 · " + text
    if days == 1:
        return "昨天 · " + text
    return text if stamp.year == datetime.date.today().year else f"{stamp.year} 年 " + text


class Application(Gtk.Application):
    def __init__(self, options):
        app_id = "org.matrixline.Desktop.Demo" if options.demo else "org.matrixline.Desktop.Live"
        if options.smoke_test:
            app_id += ".Smoke" + uuid.uuid4().hex[:8]
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.options = options
        self.state = State(mode="demo" if options.demo else "live")
        self.notes = Notes()
        self.prefs = Preferences()
        self.keep = Keep()
        self.core = None
        self.pictures = Pictures(self.dispatch)
        self.window = None
        self.section = "chats"
        self.filter = "all"
        self.detail_tab = "info"
        self.details_open = True
        self.details_auto = True
        self.message_search = ""
        self.persistent = None
        self.modal = None
        self.modal_kind = ""
        self.popover_message = None
        self.toast_timer = 0
        self.width_class = ""
        self.bubble_chars = 60
        self.demo_note_hidden = False
        self.dialogs = set()
        self.generation = 0
        self.smoke = {"checks": [], "mode": self.state.mode}
        self.exit_status = 0
        self.connect("activate", self.activate)
        self.connect("shutdown", self.shutdown_core)

    # ------------------------------------------------------------------ setup
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
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", False)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ASSETS / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.add_css_class("line-window")
        self.window.add_css_class(f"font-{self.prefs.settings['font_size']}")
        self.window.set_title("LINE" + (" · 離線展示" if self.options.demo else ""))
        self.window.set_default_size(1400, 880)
        self.window.set_size_request(1050, 650)
        self.window.set_titlebar(self.build_titlebar())
        self.overlay = Gtk.Overlay()
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.set_hexpand(True)
        self.pages.set_vexpand(True)
        self.overlay.set_child(self.pages)
        self.build_login()
        self.build_workspace()
        self.toast_label = label("", "toast", xalign=0.5)
        self.toast_label.set_halign(CENTER)
        self.toast_label.set_valign(END)
        self.toast_label.set_can_target(False)
        self.toast_label.set_visible(False)
        self.overlay.add_overlay(self.toast_label)
        self.window.set_child(self.overlay)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.window_key)
        self.window.add_controller(keys)
        self.pages.set_visible_child_name("login")
        self.window.present()
        try:
            self.core = Core(self.options.demo, self.dispatch, self.on_event)
            self.core.request("initialize", callback=self.initialized)
        except OSError:
            self.toast("無法啟動 LINE 核心。請先建置程式，或確認 LINE_APP_CORE 指向可執行檔。", True)
            self.auth_status.set_text("核心尚未啟動。請關閉後重新開啟程式。")
        if self.options.smoke_test:
            GLib.timeout_add(250, self.smoke_tick)

    def initialized(self, response):
        if not response.get("ok"):
            self.toast(self.error(response), True)
            return
        result = response.get("result", {})
        self.state.mode = result.get("mode", self.state.mode)
        self.state.capabilities = result.get("capabilities", {})
        self.update_capabilities()

    def build_titlebar(self):
        header = Gtk.HeaderBar()
        header.add_css_class("titlebar")
        header.set_decoration_layout("close,minimize,maximize:")
        header.set_title_widget(label("LINE", "titlebar-name"))
        if self.options.demo:
            pill = box(H, 8, "demo-pill", valign=CENTER)
            pill.append(dot())
            self.mode_label = label("離線展示 · 不連接 LINE")
            pill.append(self.mode_label)
            header.pack_end(pill)
        else:
            self.mode_label = label("桌面版體驗", "titlebar-end")
            header.pack_end(self.mode_label)
        return header

    # ------------------------------------------------------------------ login
    def build_login(self):
        page = box(V, 0, "login")
        top = box(H, 16, "presentation")
        mark = box(H, 0, valign=CENTER)
        mark.append(label("LINE Desktop", "wordmark"))
        mark.append(label(".", "wordmark-dot"))
        top.append(mark)
        tagline = label("通訊，簡單一點。", "wordmark-tagline", xalign=0)
        tagline.set_valign(CENTER)
        top.append(tagline)
        page.append(top)
        card = box(H, 0, "login-card", halign=CENTER, valign=CENTER, vexpand=True)
        card.set_margin_bottom(60)
        hero = box(V, 0, "login-art")
        hero.set_size_request(400, -1)
        hero.append(self.logo())
        hero.append(label("把日常，\n聊成喜歡的樣子。", "login-hero", wrap=True))
        hero.get_last_child().set_margin_top(26)
        lead = label("每一句問候，都是靠近彼此的一小步。", "login-lead", wrap=True)
        lead.set_margin_top(12)
        hero.append(lead)
        picture = Art("scene", "mountain", 324, 200, "r16")
        picture.set_margin_top(28)
        picture.set_margin_bottom(22)
        hero.append(picture)
        hero.append(label("保留一點時間，給在乎的人。", "login-lead"))
        card.append(hero)
        form = box(V, 14, "login-form")
        form.set_size_request(430, -1)
        form.append(label("歡迎回來", "side-title"))
        form.append(label("登入 LINE，繼續你的對話。", "subtle"))
        switches = box(H, 7)
        self.qr_tab = button(None, lambda: self.login_view("qr"), "filter", text="行動條碼登入")
        self.email_tab = button(None, lambda: self.login_view("email"), "filter", text="電子郵件登入")
        switches.append(self.qr_tab)
        switches.append(self.email_tab)
        form.append(switches)
        self.auth_pages = Gtk.Stack()
        qr = box(V, 14)
        self.qr_frame = box(V, 0, "qr-box", halign=START)
        self.qr_frame.set_size_request(240, 240)
        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(198, 198)
        self.qr_picture.set_visible(False)
        self.qr_frame.append(self.qr_picture)
        self.qr_placeholder = box(V, 12, valign=CENTER, vexpand=True)
        placeholder_icon = icon("image", 64)
        placeholder_icon.set_halign(CENTER)
        self.qr_placeholder.append(placeholder_icon)
        self.qr_placeholder.append(label("使用手機 LINE 掃描", "subtle", xalign=0.5))
        self.qr_frame.append(self.qr_placeholder)
        qr.append(self.qr_frame)
        self.qr_button = button(None, self.start_qr, "primary", text="產生登入行動條碼")
        self.qr_button.set_halign(START)
        qr.append(self.qr_button)
        qr.append(label("開啟手機 LINE › 設定 › 行動條碼登入", "subtle", wrap=True))
        self.auth_pages.add_named(qr, "qr")
        email = box(V, 0)
        self.email_entry = Gtk.Entry(placeholder_text="name@example.com")
        self.email_entry.set_input_purpose(Gtk.InputPurpose.EMAIL)
        email.append(self.field("電子郵件", self.email_entry))
        self.password_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.password_entry.set_property("placeholder-text", "LINE 帳號密碼")
        self.password_entry.connect("activate", lambda *_: self.start_email())
        email.append(self.field("密碼", self.password_entry))
        self.email_button = button(None, self.start_email, "primary", text="登入")
        self.email_button.set_halign(START)
        email.append(self.email_button)
        hint = label("請先在手機 LINE 設定電子郵件與密碼。", "subtle", wrap=True)
        hint.set_margin_top(12)
        email.append(hint)
        self.auth_pages.add_named(email, "email")
        form.append(self.auth_pages)
        self.pin_label = label("", "pin")
        self.pin_label.set_visible(False)
        form.append(self.pin_label)
        self.auth_status = label("帳號憑證由系統鑰匙圈保管。", "subtle", wrap=True)
        form.append(self.auth_status)
        self.cancel_login = button(None, self.cancel_auth, "secondary", text="取消登入")
        self.cancel_login.set_halign(START)
        self.cancel_login.set_visible(False)
        form.append(self.cancel_login)
        form.append(label("非官方 Linux 用戶端 · 僅顯示實際可用功能", "fine-print", wrap=True))
        card.append(form)
        page.append(card)
        self.pages.add_named(page, "login")
        self.login_view("qr")

    def logo(self):
        frame = box(V, 0, "line-logo", halign=START)
        frame.set_size_request(40, 40)
        text = label("LINE", xalign=0.5, )
        text.set_halign(CENTER)
        text.set_valign(CENTER)
        text.set_vexpand(True)
        frame.append(text)
        return frame

    def field(self, title, widget, css=""):
        wrapper = box(V, 0, "field " + css)
        wrapper.append(label(title, "field-label"))
        wrapper.append(widget)
        return wrapper

    def login_view(self, name):
        self.auth_pages.set_visible_child_name(name)
        for widget, selected in ((self.qr_tab, name == "qr"), (self.email_tab, name == "email")):
            widget.add_css_class("active") if selected else widget.remove_css_class("active")

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

    # -------------------------------------------------------------- workspace
    def build_workspace(self):
        self.app_box = Responsive(self.responsive, orientation=H)
        self.app_box.add_css_class("app")
        self.app_box.append(self.build_rail())
        self.app_box.append(self.build_sidebar())
        self.main_stack = Gtk.Stack()
        self.main_stack.set_hexpand(True)
        self.main_stack.add_named(self.build_chat_page(), "chat")
        self.alternate = box(V, 0)
        self.main_stack.add_named(scroller(self.alternate, "alternate"), "alternate")
        self.app_box.append(self.main_stack)
        self.pages.add_named(self.app_box, "workspace")

    def build_rail(self):
        rail = box(V, 9, "rail")
        rail.set_size_request(74, -1)
        logo = self.logo()
        logo.set_halign(CENTER)
        rail.append(logo)
        self.nav_buttons = {}
        for key, name, title in (("chats", "chat", "聊天"), ("friends", "friends", "好友"), ("calls", "phone", "通話"), ("keep", "bookmark", "Keep 筆記")):
            contents = box(V, 5, valign=CENTER)
            image = icon(name, 22)
            image.set_halign(CENTER)
            contents.append(image)
            contents.append(label(title, xalign=0.5, wrap=True, max_chars=6))
            layer = Gtk.Overlay()
            layer.set_child(contents)
            item = button(layer, lambda value=key: self.navigate(value), "rail-btn", title)
            item.set_halign(CENTER)
            if key == "chats":
                self.navdot = box(css="navdot", halign=END, valign=START)
                self.navdot.set_visible(False)
                self.navdot.set_can_target(False)
                layer.add_overlay(self.navdot)
            self.nav_buttons[key] = item
            rail.append(item)
        rail.append(box(vexpand=True))
        for name, title, callback in (("help", "使用說明", self.open_help), ("settings", "設定", self.open_settings)):
            item = icon_button(name, callback, title, 20, "rail-btn")
            item.set_halign(CENTER)
            rail.append(item)
        self.self_button = button(person_avatar("me", "me", 30, 16), lambda: self.open_profile(self.state.profile.get("id", "")), "rail-self", "我的個人檔案")
        self.self_button.set_halign(CENTER)
        rail.append(self.self_button)
        return rail

    def build_sidebar(self):
        self.sidebar = box(V, 0, "sidebar")
        self.sidebar.set_size_request(270, -1)
        top = box(V, 0, "side-top")
        title_row = box(H, 0)
        title_row.set_margin_bottom(21)
        self.side_title = label("聊天", "side-title")
        self.side_title.set_hexpand(True)
        title_row.append(self.side_title)
        self.side_action = icon_button("edit", self.side_button_clicked, "建立群組", 18)
        title_row.append(self.side_action)
        top.append(title_row)
        self.search = Gtk.Text(placeholder_text="搜尋好友或聊天室")
        self.search.set_hexpand(True)
        self.search.connect("changed", lambda *_: self.search_changed())
        top.append(self.searchbox(self.search))
        self.filter_row = box(H, 7)
        self.filter_row.set_margin_top(15)
        top.append(self.filter_row)
        self.sidebar.append(top)
        self.chat_list = box(V, 0)
        self.list_scroll = scroller(self.chat_list)
        self.sidebar.append(self.list_scroll)
        footer = box(H, 6, "side-footer")
        self.footer = footer
        footer.append(dot())
        self.connection = label("每一段對話，都在這裡。")
        footer.append(self.connection)
        self.sidebar.append(footer)
        return self.sidebar

    def searchbox(self, entry):
        container = box(H, 8, "searchbox")
        glyph = icon("search", 15)
        glyph.set_valign(CENTER)
        container.append(glyph)
        container.append(entry)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: entry.grab_focus())
        container.add_controller(click)
        return container

    def build_chat_page(self):
        page = box(V, 0)
        header = box(H, 0, "chat-header")
        heading = box(H, 12, valign=CENTER, hexpand=True)
        self.header_avatar_slot = box(valign=CENTER)
        heading.append(self.header_avatar_slot)
        titles = box(V, 6, valign=CENTER)
        title_line = box(H, 0)
        self.chat_title = label("讓對話，從這裡開始。", "chat-title", ellipsize=True, max_chars=26)
        title_line.append(self.chat_title)
        self.muted_tag = label(" · 已靜音", "muted-tag")
        self.muted_tag.set_visible(False)
        title_line.append(self.muted_tag)
        titles.append(title_line)
        subtitle = box(H, 6, "chat-subtitle")
        subtitle.append(dot())
        self.chat_subtitle = label("選擇左側聊天或好友")
        subtitle.append(self.chat_subtitle)
        titles.append(subtitle)
        heading.append(titles)
        header.append(heading)
        actions = box(H, 10, "header-actions", valign=CENTER)
        actions.append(icon_button("search", self.open_search, "搜尋對話", 18))
        self.call_buttons = [box(css="header-divider", valign=CENTER),
                             icon_button("phone", lambda: self.unsupported("此版本不提供語音通話。"), "語音通話", 19),
                             icon_button("video", lambda: self.unsupported("此版本不提供視訊通話。"), "視訊通話", 20)]
        for widget in self.call_buttons:
            actions.append(widget)
        actions.append(box(css="header-divider", valign=CENTER))
        self.details_toggle = icon_button("info", self.toggle_details, "聊天室資訊", 19)
        actions.append(self.details_toggle)
        header.append(actions)
        page.append(header)
        split = box(H, 0, vexpand=True)
        conversation = box(V, 0, "conversation", hexpand=True)
        self.search_bar = box(H, 10, "conversation-search")
        self.message_search_entry = Gtk.Text(placeholder_text="搜尋這段對話")
        self.message_search_entry.set_hexpand(True)
        self.message_search_entry.connect("changed", lambda *_: self.message_search_changed())
        search_box = self.searchbox(self.message_search_entry)
        search_box.set_hexpand(True)
        self.search_bar.append(search_box)
        self.search_count = label("", "search-count")
        self.search_count.set_valign(CENTER)
        self.search_bar.append(self.search_count)
        self.search_bar.append(icon_button("x", self.close_search, "關閉搜尋", 20))
        self.search_bar.set_visible(False)
        conversation.append(self.search_bar)
        self.history = box(V, 0, "messages")
        self.history_scroll = scroller(self.history)
        self.menu = Gtk.Popover(has_arrow=False, autohide=True)
        self.menu.add_css_class("line-popover")
        display = Gdk.Display.get_default()
        if display is not None and not display.is_composited():
            self.menu.add_css_class("flat")
        self.menu.set_position(Gtk.PositionType.BOTTOM)
        self.menu.set_parent(conversation)
        self.conversation = conversation
        self.menu.connect("closed", lambda *_: self.menu_closed())
        conversation_layer = Gtk.Overlay()
        conversation_layer.set_child(self.history_scroll)
        self.drag_overlay = label("放開檔案，分享此刻的小美好。", "drag-overlay", xalign=0.5)
        self.drag_overlay.set_can_target(False)
        self.drag_overlay.set_visible(False)
        conversation_layer.add_overlay(self.drag_overlay)
        conversation_layer.set_vexpand(True)
        conversation.append(conversation_layer)
        conversation.append(self.build_composer())
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("enter", lambda *_: self.drag_overlay.set_visible(self.can_attach()) or Gdk.DragAction.COPY)
        drop.connect("leave", lambda *_: self.drag_overlay.set_visible(False))
        drop.connect("drop", self.dropped)
        conversation.add_controller(drop)
        split.append(conversation)
        self.details = box(V, 0, "details")
        self.details.set_size_request(238, -1)
        self.detail_contents = box(V, 0)
        self.details.append(scroller(self.detail_contents))
        split.append(self.details)
        page.append(split)
        return page

    def build_composer(self):
        composer = box(V, 0, "composer")
        toolbar = box(H, 8, "composer-toolbar")
        self.sticker_button = icon_button("smile", self.open_stickers, "貼圖與表情符號")
        self.photo_button = icon_button("image", lambda: self.choose_files(images=True), "傳送照片（以檔案傳送）")
        self.file_button = icon_button("file", self.choose_files, "傳送檔案")
        crop = icon_button("crop", lambda: self.unsupported("此版本不提供擷取畫面。"), "擷取畫面")
        self.keep_button = icon_button("bookmark", lambda: self.select_chat(KEEP_ID), "Keep 筆記")
        for widget in (self.sticker_button, self.photo_button, self.file_button, crop, self.keep_button):
            toolbar.append(widget)
        end = label("讓日常，保持聯繫", "toolbar-end", xalign=1)
        end.set_hexpand(True)
        toolbar.append(end)
        composer.append(toolbar)
        row = box(H, 14, valign=END)
        self.compose = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.compose.add_css_class("compose-input")
        self.compose.set_accepts_tab(False)
        self.compose.get_buffer().connect("changed", self.draft_changed)
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self.composer_key)
        self.compose.add_controller(key)
        compose_scroll = Gtk.ScrolledWindow()
        compose_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        compose_scroll.set_min_content_height(49)
        compose_scroll.set_max_content_height(110)
        compose_scroll.set_propagate_natural_height(True)
        compose_scroll.set_child(self.compose)
        layer = Gtk.Overlay()
        layer.set_child(compose_scroll)
        layer.set_hexpand(True)
        self.compose_placeholder = label("輸入訊息，分享今天的大小事…", "compose-placeholder")
        self.compose_placeholder.set_valign(START)
        self.compose_placeholder.set_halign(START)
        self.compose_placeholder.set_can_target(False)
        layer.add_overlay(self.compose_placeholder)
        row.append(layer)
        send_icon = icon("send", 17)
        send_icon.set_halign(CENTER)
        send_icon.set_valign(CENTER)
        self.send_button = button(send_icon, self.send_text, "send", "傳送訊息")
        self.send_button.set_valign(END)
        row.append(self.send_button)
        composer.append(row)
        hints = box(H, 0, "composer-hint")
        self.enter_hint = label("")
        self.enter_hint.set_hexpand(True)
        hints.append(self.enter_hint)
        self.composer_note = label("", xalign=1)
        hints.append(self.composer_note)
        composer.append(hints)
        return composer

    # ---------------------------------------------------------- responsiveness
    def responsive(self, width):
        window_width = width
        if window_width >= 1600:
            name, side, detail = "size-xl", 310, 280
        elif window_width > 1450:
            name, side, detail = "size-l", 292, 254
        else:
            name, side, detail = "size-m", 270, 238
        if name != self.width_class:
            if self.width_class:
                self.app_box.remove_css_class(self.width_class)
            self.width_class = name
            self.app_box.add_css_class(name)
            self.sidebar.set_size_request(side, -1)
            self.details.set_size_request(detail, -1)
            self.detail_width = detail
        if self.details_auto:
            self.details_open = window_width > 1150
        self.details.set_visible(self.details_open)
        messages = window_width - 74 - side - (detail if self.details_open else 0) - 58
        chars = max(24, int(messages * 0.81 / 6.6))
        if chars != self.bubble_chars:
            self.bubble_chars = chars
            if self.state.selected and self.section != "friends":
                self.render_messages()
            if self.detail_tab == "info":
                self.render_details()

    # -------------------------------------------------------------- navigation
    def navigate(self, section):
        if section == "keep":
            self.select_chat(KEEP_ID)
            return
        self.section = section
        self.filter = "all"
        self.search.set_text("")
        self.render_rail()
        self.render_sidebar()
        self.main_stack.set_visible_child_name("alternate" if section in ("friends", "calls") else "chat")
        if section in ("friends", "calls"):
            self.render_alternate()

    def render_rail(self):
        active = "keep" if self.section == "chats" and self.state.selected == KEEP_ID else self.section
        for key, widget in self.nav_buttons.items():
            widget.add_css_class("active") if key == active else widget.remove_css_class("active")
            widget.queue_draw()
        self.navdot.set_visible(any(chat.get("unread") for chat in self.state.chats.values()))
        self.self_button.set_child(person_avatar(self.state.profile.get("id", "me"), self.state.profile.get("id", "me"), 30, 16))

    def render_sidebar(self):
        friends = self.section == "friends"
        self.side_title.set_text({"friends": "好友", "calls": "通話"}.get(self.section, "聊天"))
        self.side_action.set_child(icon("plus" if friends else "edit", 18))
        self.side_action.set_tooltip_text("加入好友" if friends else "建立群組")
        self.search.set_placeholder_text("搜尋好友姓名" if friends else "搜尋好友或聊天室")
        clear(self.filter_row)
        unread = sum(1 for chat in self.state.chats.values() if chat.get("unread"))
        options = [("all", "全部"), ("favorite", "我的最愛")] if friends else [("all", "全部"), ("unread", "未讀"), ("group", "群組")]
        for value, title in options:
            contents = box(H, 4)
            contents.append(label(title))
            if value == "unread":
                contents.append(label(str(unread), "count"))
            widget = button(contents, lambda value=value: self.set_filter(value), "filter")
            if value == self.filter:
                widget.add_css_class("active")
            self.filter_row.append(widget)
        self.render_list()

    def set_filter(self, value):
        self.filter = value
        self.render_sidebar()
        if self.section == "friends":
            self.render_alternate()

    def search_changed(self):
        self.render_list()
        if self.section == "friends":
            self.render_alternate()

    def side_button_clicked(self):
        if self.section == "friends":
            self.unsupported("此版本尚不支援新增好友。請在手機 LINE 加入後重新整理。")
        else:
            self.unsupported("此版本尚不支援建立群組。請在手機 LINE 建立後重新整理。")

    def visible_friends(self):
        query = self.search.get_text().casefold().strip()
        rows = [c for c in self.state.contacts.values() if query in str(c.get("name", "")).casefold()]
        if self.filter == "favorite":
            rows = [c for c in rows if self.prefs.has("favorites", c["id"])]
        return rows

    def visible_chats(self):
        query = self.search.get_text().casefold().strip()
        rows = []
        for chat in self.state.chats.values():
            names = [str(chat.get("name", ""))] + [self.state.name_of(member) for member in chat.get("members") or []]
            if query and not any(query in name.casefold() for name in names):
                continue
            if self.filter == "unread" and not chat.get("unread"):
                continue
            if self.filter == "group" and chat.get("kind") not in ("group", "room"):
                continue
            rows.append(chat)
        rows.sort(key=lambda chat: (chat["id"] == KEEP_ID, -int(chat.get("updated_at", 0) or 0)))
        return rows

    def render_list(self):
        clear(self.chat_list)
        if self.section == "friends":
            rows = self.visible_friends()
            self.chat_list.append(self.list_label("好友", len(rows)))
            for contact in rows:
                self.chat_list.append(self.friend_row(contact))
            if not rows:
                self.chat_list.append(label("沒有符合的好友\n試試其他搜尋關鍵字。", "empty", xalign=0.5))
            return
        rows = self.visible_chats()
        pinned = [chat for chat in rows if self.prefs.has("pinned", chat["id"])]
        others = [chat for chat in rows if not self.prefs.has("pinned", chat["id"])]
        if pinned:
            self.chat_list.append(self.list_label("置頂聊天", None, "pin"))
            for chat in pinned:
                self.chat_list.append(self.chat_row(chat))
        if others:
            self.chat_list.append(self.list_label("所有聊天", len(others), spaced=bool(pinned)))
            for chat in others:
                self.chat_list.append(self.chat_row(chat))
        if not rows:
            self.chat_list.append(label("沒有找到聊天室\n試試其他關鍵字或篩選。", "empty", xalign=0.5))

    def list_label(self, text, count=None, glyph=None, spaced=False):
        row = box(H, 6, "list-label" + (" spaced" if spaced else ""))
        if glyph:
            image = icon(glyph, 10)
            image.set_valign(CENTER)
            row.append(image)
        title = label(text)
        title.set_hexpand(True)
        row.append(title)
        if count is not None:
            row.append(label(str(count), "count"))
        return row

    def chat_row(self, chat):
        contents = box(H, 12)
        contents.append(chat_avatar(chat, self.state.profile.get("id", ""), 43, 16))
        info = box(V, 0, valign=CENTER, hexpand=True)
        info.set_size_request(80, -1)
        head = box(H, 7)
        head.set_margin_bottom(7)
        name = label(chat.get("name", "未命名對話"), "chat-name", ellipsize=True)
        name.set_hexpand(True)
        head.append(name)
        head.append(label(list_time(chat.get("updated_at", 0)), "chat-time"))
        info.append(head)
        bottom = box(H, 6)
        preview = label(str(chat.get("last_message") or "開始你們的第一段對話").replace("\n", " "), "chat-preview", ellipsize=True)
        preview.set_hexpand(True)
        bottom.append(preview)
        if self.prefs.has("muted", chat["id"]):
            bottom.append(icon("belloff", 11, "muted-icon"))
        if chat.get("unread"):
            badge = label(str(chat["unread"]), "unread", xalign=0.5)
            badge.set_valign(CENTER)
            bottom.append(badge)
        info.append(bottom)
        contents.append(info)
        title = chat.get("name", "對話") + (f"，{chat['unread']} 則未讀" if chat.get("unread") else "")
        row = button(contents, lambda value=chat["id"]: self.select_chat(value), "chat-row", title)
        row.set_tooltip_text(None)
        if chat["id"] == self.state.selected and self.section != "friends":
            row.add_css_class("active")
        return row

    def friend_row(self, contact):
        contents = box(H, 12)
        contents.append(person_avatar(contact["id"], self.state.profile.get("id", ""), 43, 16))
        info = box(V, 0, valign=CENTER, hexpand=True)
        info.set_size_request(80, -1)
        head = box(H, 7)
        head.set_margin_bottom(7)
        head.append(label(contact.get("name", "好友"), "chat-name", ellipsize=True))
        if self.prefs.has("favorites", contact["id"]):
            star = icon("star", 12, "star")
            star.set_valign(CENTER)
            head.append(star)
        info.append(head)
        info.append(label(contact.get("status") or "　", "chat-preview", ellipsize=True))
        contents.append(info)
        row = button(contents, lambda value=contact["id"]: self.open_profile(value), "chat-row", contact.get("name", "好友"))
        row.set_tooltip_text(None)
        return row

    # --------------------------------------------------------------- chat view
    def select_chat(self, chat_id):
        if chat_id == KEEP_ID and KEEP_ID not in self.state.chats:
            return
        if chat_id not in self.state.chats:
            return
        self.section = "chats"
        self.state.selected = chat_id
        chat = self.state.chats[chat_id]
        chat["unread"] = 0
        self.detail_tab = "info"
        self.close_search()
        self.main_stack.set_visible_child_name("chat")
        self.compose.get_buffer().set_text(self.state.drafts.get(chat_id, ""))
        self.render_rail()
        self.render_sidebar()
        self.render_header()
        self.render_messages()
        self.render_details()
        self.update_send()
        if chat_id == KEEP_ID:
            return
        generation = self.generation

        def loaded(response):
            if generation != self.generation:
                return
            if response.get("ok"):
                for message in response.get("result", {}).get("messages", []):
                    self.state.add_message(message)
                if self.state.selected == chat_id:
                    self.state.chats.get(chat_id, {})["unread"] = 0
                    self.render_messages()
                    self.render_details()
                    self.mark_read()
            else:
                self.toast(self.error(response), True)
        if self.core:
            self.core.request("messages.list", {"chat_id": chat_id, "limit": 50}, loaded)

    def ensure_direct(self, contact_id):
        contact = self.state.contacts.get(contact_id)
        if contact and contact_id not in self.state.chats:
            self.state.chats[contact_id] = {"id": contact_id, "name": contact.get("name", "好友"), "kind": "direct", "last_message": "", "unread": 0, "updated_at": 0}
        return contact_id if contact_id in self.state.chats else None

    def current_chat(self):
        return self.state.chats.get(self.state.selected)

    def members_of(self, chat):
        own = self.state.profile.get("id", "me")
        if not chat:
            return []
        if chat["id"] == KEEP_ID:
            return [own]
        if chat.get("kind") == "direct":
            return [own, chat["id"]]
        return list(chat.get("members") or [])

    def member_count(self, chat):
        if not chat:
            return 0
        return int(chat.get("member_count") or len(self.members_of(chat)))

    def render_header(self):
        chat = self.current_chat()
        clear(self.header_avatar_slot)
        self.header_avatar_slot.append(chat_avatar(chat, self.state.profile.get("id", ""), 40, 13))
        self.chat_title.set_text(chat.get("name", "對話") if chat else "讓對話，從這裡開始。")
        self.muted_tag.set_visible(bool(chat and self.prefs.has("muted", chat["id"])))
        if not chat:
            subtitle = "選擇左側聊天或好友"
        elif chat["id"] == KEEP_ID:
            subtitle = "只屬於你的私人記事空間"
        elif chat.get("kind") in ("group", "room"):
            subtitle = f"{self.member_count(chat)} 位成員 · 生活的小事，一起分享"
        else:
            subtitle = "你們的每一段日常"
        self.chat_subtitle.set_text(subtitle)
        for widget in self.call_buttons:
            widget.set_visible(not chat or chat["id"] != KEEP_ID)
        hint = "Enter 傳送 · Shift + Enter 換行" if self.prefs.settings["enter_send"] else "Enter 換行 · 點擊箭頭傳送"
        self.enter_hint.set_text(hint)
        if chat and chat["id"] == KEEP_ID:
            self.composer_note.set_text("只保存在這台電腦")
        else:
            self.composer_note.set_text("離線展示 · 不會實際傳送" if self.state.mode == "demo" else "Letter Sealing 加密傳送")

    def history_for(self, chat_id):
        return self.state.messages.get(chat_id, [])

    def render_messages(self):
        clear(self.history)
        chat = self.current_chat()
        history = self.history_for(self.state.selected) if chat else []
        query = self.message_search.casefold()
        matches = 0
        first_hit = None
        if not history:
            self.history.append(self.date_divider(day_label(int(time.time() * 1000))))
            empty = label("第一句問候，讓故事開始。\n傳送訊息，分享你的今天。" if chat else "每一天，都有值得分享的小事。\n選擇左側對話，開始今天的問候。", "empty", xalign=0.5)
            self.history.append(empty)
        day = None
        for message in history:
            moment = day_label(message.get("time", 0))
            if moment != day:
                day = moment
                self.history.append(self.date_divider(moment))
            hit = bool(query and query in str(message.get("text", "")).casefold())
            matches += hit
            row, bubble = self.message_widget(message, chat, hit)
            if hit and first_hit is None:
                first_hit = bubble
            self.history.append(row)
        self.search_count.set_text(f"{matches} 則結果" if self.message_search else "")
        if first_hit is not None:
            GLib.idle_add(self.scroll_to, first_hit)
        else:
            GLib.idle_add(self.scroll_bottom)

    def date_divider(self, text):
        pill = label(text, "date-divider", xalign=0.5)
        pill.set_halign(CENTER)
        return pill

    def message_widget(self, message, chat, highlighted=False):
        own = bool(message.get("mine"))
        kind = message.get("kind") or "text"
        if kind == "system":
            notice = label(message.get("text") or "系統訊息", "bubble system", wrap=True, xalign=0.5, max_chars=self.bubble_chars)
            notice.set_halign(CENTER)
            wrapper = box(V, 0, "message")
            wrapper.append(notice)
            return wrapper, notice
        row = box(H, 9, "message" + (" own" if own else ""), halign=END if own else START)
        if not own:
            row.append(person_avatar(message.get("sender_id") or message.get("sender_name"), self.state.profile.get("id", ""), 28, 10))
        main = box(V, 0)
        if not own:
            main.append(label(message.get("sender_name") or self.state.name_of(message.get("sender_id")), "message-sender"))
        line = box(H, 7)
        bubble = self.bubble(message, kind)
        if highlighted:
            bubble.add_css_class("search-highlight")
        meta = box(V, 0, "message-meta", valign=END)
        if own and chat and chat["id"] != KEEP_ID:
            status = message.get("status")
            text, css = {"pending": ("傳送中…", "read"), "failed": ("傳送失敗", "failed")}.get(status, ("已傳送", "read"))
            meta.append(label(text, css, xalign=1))
        meta.append(label(clock(message.get("time", 0)), xalign=1 if own else 0))
        actions = self.message_actions(message, chat)
        parts = [bubble, meta] + ([actions] if actions else [])
        for widget in (reversed(parts) if own else parts):
            line.append(widget)
        main.append(line)
        row.append(main)
        return row, bubble

    def bubble(self, message, kind):
        own = bool(message.get("mine"))
        url = str(message.get("url") or "")
        if kind == "sticker":
            holder = box(V, 0, "bubble sticker-bubble")
            if url.startswith("demo:bear-"):
                holder.append(Art("bear", url.rsplit("-", 1)[-1], 120, 108))
            else:
                holder.append(self.sticker_picture(url, 120, 108))
            return holder
        if kind == "image" and url == "demo:collage":
            holder = box(V, 0, "bubble image-bubble")
            holder.set_overflow(Gtk.Overflow.HIDDEN)
            holder.set_size_request(282, -1)
            grid = box(H, 3)
            grid.append(Art("scene", "mountain", 163, 150))
            side = box(V, 3)
            side.append(Art("scene", "trail", 116, 74))
            side.append(Art("scene", "coffee", 116, 73))
            grid.append(side)
            holder.append(grid)
            holder.append(label(message.get("text") or "", "image-caption", wrap=True))
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self.open_lightbox(0))
            holder.add_controller(click)
            holder.set_cursor_from_name("zoom-in")
            return holder
        if kind == "file":
            holder = box(V, 0, "bubble")
            attachment = box(H, 11, "attachment")
            attachment.set_size_request(215, -1)
            attachment.append(file_badge(message.get("filename")))
            names = box(V, 0, valign=CENTER, hexpand=True)
            names.append(label(message.get("filename") or "檔案", "file-name", wrap=True, max_chars=26))
            names.append(label(file_size(message.get("size")) + (" · 示範檔案" if self.state.mode == "demo" else ""), "file-size"))
            attachment.append(names)
            download = icon_button("download", lambda item=message: self.download_file(item), "另存檔案", 15)
            download.set_valign(CENTER)
            attachment.append(download)
            holder.append(attachment)
            return holder
        text = message.get("text") or media_label(message)
        if kind == "image":
            text = "[照片] 此版本尚無法預覽照片"
        widget = label(text, "bubble", wrap=True, max_chars=self.bubble_chars, selectable=True)
        widget.set_halign(END if own else START)
        return widget

    def message_actions(self, message, chat):
        options = self.menu_options(message, chat)
        if not options:
            return None
        widget = icon_button("more", None, "訊息操作", 15, "message-actions")
        widget.set_valign(CENTER)
        widget.connect("clicked", lambda source, item=message: self.show_menu(source, item))
        return widget

    def menu_options(self, message, chat):
        options = []
        if message.get("kind") in ("text", None) and message.get("text"):
            options.append(("copy", "複製文字", lambda: self.copy_text(message.get("text", ""))))
            options.append(("forward", "轉傳", lambda: self.open_forward(message)))
        if message.get("kind") == "file":
            options.append(("download", "另存檔案", lambda: self.download_file(message)))
        return options

    def show_menu(self, source, message):
        contents = box(V, 0)
        for glyph, title, callback in self.menu_options(message, self.current_chat()):
            contents.append(button(icon_text(glyph, title, 14, 9), lambda action=callback: (self.menu.popdown(), action()), None))
        self.menu.set_child(contents)
        ok, bounds = source.compute_bounds(self.conversation)
        if ok:
            rect = Gdk.Rectangle()
            rect.x, rect.y = int(bounds.get_x()), int(bounds.get_y())
            rect.width, rect.height = int(bounds.get_width()), int(bounds.get_height())
            self.menu.set_pointing_to(rect)
        source.add_css_class("open")
        self.popover_message = source
        self.menu.popup()

    def menu_closed(self):
        if self.popover_message is not None:
            self.popover_message.remove_css_class("open")
            self.popover_message = None

    def copy_text(self, text):
        self.window.get_clipboard().set(text)
        self.toast("已複製訊息")

    def scroll_bottom(self):
        adjustment = self.history_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def scroll_to(self, widget):
        ok, bounds = widget.compute_bounds(self.history)
        if ok:
            adjustment = self.history_scroll.get_vadjustment()
            adjustment.set_value(max(0, bounds.get_y() - adjustment.get_page_size() / 2))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ search bar
    def open_search(self):
        if not self.current_chat():
            return
        self.search_bar.set_visible(True)
        self.message_search_entry.grab_focus()

    def close_search(self):
        if not self.search_bar.get_visible() and not self.message_search:
            return
        self.search_bar.set_visible(False)
        self.message_search = ""
        self.message_search_entry.set_text("")
        self.render_messages()

    def message_search_changed(self):
        self.message_search = self.message_search_entry.get_text().strip()
        if self.current_chat():
            self.render_messages()

    # -------------------------------------------------------------- composer
    def draft_changed(self, *_):
        buffer = self.compose.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if self.state.selected:
            self.state.drafts[self.state.selected] = text
        self.compose_placeholder.set_visible(not text)
        self.update_send()

    def can_send_text(self):
        chat_id = self.state.selected
        if chat_id == KEEP_ID:
            return True
        return bool(chat_id and self.state.session == "ready" and self.state.capabilities.get("text"))

    def update_send(self):
        draft = self.state.drafts.get(self.state.selected, "")
        self.send_button.set_sensitive(bool(self.can_send_text() and draft.strip()))
        self.compose.set_editable(bool(self.state.selected))

    def composer_key(self, controller, keyval, keycode, modifiers):
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or not self.prefs.settings["enter_send"]:
            return False
        if modifiers & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            return False
        event = controller.get_current_event()
        if event is not None and self.compose.im_context_filter_keypress(event):
            return True
        if self.send_button.get_sensitive():
            self.send_text()
        return True

    def send_text(self):
        chat_id = self.state.selected
        text = self.state.drafts.get(chat_id, "")
        if not self.send_button.get_sensitive() or not text.strip():
            return
        self.deliver_text(chat_id, text, from_composer=True)

    def deliver_text(self, chat_id, text, from_composer=False):
        if chat_id == KEEP_ID:
            item = self.keep.add(text.strip())
            message = self.keep.messages()[-1]
            assert message["id"] == item["id"]
            self.state.add_message(message)
            self.state.chats[KEEP_ID].update(self.keep.chat())
            if from_composer:
                self.state.drafts[chat_id] = ""
                self.compose.get_buffer().set_text("")
            self.after_message(chat_id)
            return
        if not self.core:
            return
        if len(text.encode()) > 65536:
            self.toast("訊息太長，請分成幾段傳送。", True)
            return
        temporary = "pending-" + uuid.uuid4().hex
        generation = self.generation
        message = {"id": temporary, "chat_id": chat_id, "text": text, "mine": True, "time": int(time.time() * 1000), "kind": "text", "status": "pending"}
        self.state.add_message(message)
        if from_composer:
            self.state.drafts[chat_id] = ""
            self.compose.get_buffer().set_text("")
        self.after_message(chat_id)

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
                if from_composer:
                    self.state.drafts[chat_id] = text + ("\n" + self.state.drafts[chat_id] if self.state.drafts.get(chat_id) else "")
                    if self.state.selected == chat_id:
                        self.compose.get_buffer().set_text(self.state.drafts[chat_id])
                self.toast(self.error(response), True)
            self.after_message(chat_id)
        self.core.request("messages.send", {"chat_id": chat_id, "text": text}, sent)

    def after_message(self, chat_id):
        if self.state.selected == chat_id and self.main_stack.get_visible_child_name() == "chat":
            self.render_messages()
            self.render_details()
        self.render_list()
        self.render_rail()

    # ---------------------------------------------------------------- details
    def toggle_details(self):
        self.details_auto = False
        self.details_open = not self.details_open
        self.details.set_visible(self.details_open)
        self.responsive(self.app_box.last_width or self.window.get_width())

    def set_detail_tab(self, tab):
        self.detail_tab = tab
        self.render_details()

    def toggle_pref(self, key):
        chat = self.current_chat()
        if not chat:
            return
        try:
            enabled = self.prefs.toggle(key, chat["id"])
        except OSError:
            self.toast("無法儲存偏好設定。請確認資料目錄的權限。", True)
            return
        if key == "pinned":
            self.toast("已將聊天置頂" if enabled else "已取消置頂")
        else:
            self.toast("已關閉此聊天室通知" if enabled else "已開啟此聊天室通知")
        self.render_header()
        self.render_details()
        self.render_list()

    def render_details(self):
        clear(self.detail_contents)
        chat = self.current_chat()
        profile = box(V, 0, "detail-profile")
        portrait = chat_avatar(chat, self.state.profile.get("id", ""), 68, 23)
        portrait.set_halign(CENTER)
        profile.append(portrait)
        profile.append(label(chat.get("name", "你的對話空間") if chat else "你的對話空間", "detail-name", wrap=True, xalign=0.5))
        if not chat:
            caption = "好好聊天，慢慢靠近。"
        elif chat["id"] == KEEP_ID:
            caption = "隨手記下，每一個靈感"
        elif chat.get("kind") in ("group", "room"):
            caption = f"{self.member_count(chat)} 位成員 · 在一起的好時光"
        else:
            caption = (self.state.contacts.get(chat["id"]) or {}).get("status") or "你們的每一段日常"
        profile.append(label(caption, "detail-caption", wrap=True, xalign=0.5))
        self.detail_contents.append(profile)
        quick = box(H, 26, "quick-actions", halign=CENTER)
        muted = bool(chat and self.prefs.has("muted", chat["id"]))
        pinned = bool(chat and self.prefs.has("pinned", chat["id"]))
        for glyph, title, on, callback in (("belloff" if muted else "bell", "已關閉通知" if muted else "通知", muted, lambda: self.toggle_pref("muted")),
                                           ("pin", "已置頂" if pinned else "置頂", pinned, lambda: self.toggle_pref("pinned")),
                                           ("search", "搜尋", False, self.open_search)):
            contents = box(V, 7)
            circle = box(V, 0, "quick-circle", halign=CENTER)
            image = icon(glyph, 16)
            image.set_halign(CENTER)
            image.set_valign(CENTER)
            image.set_vexpand(True)
            circle.append(image)
            contents.append(circle)
            contents.append(label(title, xalign=0.5))
            widget = button(contents, callback, "quick-action" + (" on" if on else ""), title)
            widget.set_sensitive(bool(chat))
            quick.append(widget)
        self.detail_contents.append(quick)
        tabs = box(H, 0, "detail-tabs")
        for key, title in (("info", "資訊"), ("notes", "筆記本"), ("albums", "相簿"), ("files", "檔案")):
            contents = box(V, 0)
            contents.append(label(title, xalign=0.5))
            contents.append(box(css="tab-line"))
            widget = button(contents, lambda value=key: self.set_detail_tab(value), "detail-tab" + (" active" if key == self.detail_tab else ""))
            widget.set_hexpand(True)
            tabs.append(widget)
        self.detail_contents.append(tabs)
        content = box(V, 0, "detail-content")
        if chat:
            {"info": self.detail_info, "notes": self.detail_notes, "albums": self.detail_albums, "files": self.detail_files}[self.detail_tab](content, chat)
        self.detail_contents.append(content)

    def section_label(self, text, action=None, callback=None, glyph=None, chevron=False):
        row = box(H, 6, "section-label")
        if glyph:
            image = icon(glyph, 12)
            image.set_valign(CENTER)
            row.append(image)
        title = label(text)
        title.set_hexpand(True)
        row.append(title)
        if action:
            contents = box(H, 3)
            contents.append(label(action))
            if chevron:
                image = icon("chevron", 9)
                image.set_valign(CENTER)
                contents.append(image)
            row.append(button(contents, callback, None, action))
        return row

    def account_key(self):
        return self.state.mode + ":" + str(self.state.profile.get("id", "anonymous"))

    def note_for(self, chat):
        if not chat:
            return None
        note = self.notes.read(self.account_key(), chat["id"])
        if note is None and self.state.mode == "demo" and chat["id"] == "demo-weekend" and not self.demo_note_hidden:
            note = dict(DEMO_NOTE, updated=int(time.time() * 1000))
        return note

    def note_card(self, chat, note):
        card = box(V, 0, "note-card")
        tag = box(H, 4, "note-tag", halign=START)
        tag_icon = icon("pin", 9)
        tag_icon.set_valign(CENTER)
        tag.append(tag_icon)
        tag.append(label("置頂公告" if note.get("author") else "只在這台電腦"))
        body = box(V, 0)
        body.append(tag)
        body.append(label(note["title"], "note-title", wrap=True))
        text = note["body"]
        body.append(label(text[:88] + ("…" if len(text) > 88 else ""), "note-body", wrap=True))
        footer = box(H, 6, "note-footer")
        own = self.state.profile.get("id", "me")
        author = note.get("author") or own
        footer.append(person_avatar(author, own, 17, 6))
        footer.append(label(f"{'你' if author == own else self.state.name_of(author)} · {list_time(note.get('updated'))}"))
        body.append(footer)
        card.append(button(body, lambda: self.open_note(chat), None, "查看記事"))
        controls = box(H, 12, "note-controls")
        controls.set_margin_top(12)
        controls.append(button(None, lambda: self.edit_note(chat), None, text="編輯"))
        controls.append(button(None, lambda: self.confirm_delete_note(chat), None, text="刪除"))
        card.append(controls)
        return card

    def detail_info(self, content, chat):
        note = self.note_for(chat)
        content.append(self.section_label("記事與公告", "查看全部", lambda: self.set_detail_tab("notes"), "pin", True))
        if note:
            content.append(self.note_card(chat, note))
        else:
            empty = label("還沒有記事，把重要的小事記下來。", "subtle", wrap=True)
            empty.set_margin_bottom(22)
            content.append(empty)
        content.append(self.section_label("共享的片刻", "查看全部", lambda: self.set_detail_tab("albums")))
        scenes = self.chat_scenes(chat)
        if scenes:
            grid = box(H, 4)
            grid.set_margin_bottom(23)
            cell = max(48, (getattr(self, "detail_width", 238) - 36 - 8) // 3)
            for index, name in enumerate(scenes[:3]):
                tile = Art("scene", name, cell, cell, "media-cell")
                frame = button(tile, lambda value=index: self.open_lightbox(value), None, "查看照片")
                grid.append(frame)
            content.append(grid)
        else:
            empty = label("下一個值得收藏的瞬間，等你分享。", "subtle", wrap=True)
            empty.set_margin_bottom(23)
            content.append(empty)
        content.append(self.section_label(f"{self.member_count(chat)} 位成員", "查看", lambda: self.open_members(chat)))
        members = box(H, 7)
        own = self.state.profile.get("id", "me")
        listed = self.members_of(chat)
        for member in listed[:5]:
            members.append(button(person_avatar(member, own, 29, 11), lambda value=member: self.open_profile(value), None, self.state.name_of(member)))
        note_text = label("一起，在這裡。", "members-note", xalign=1)
        note_text.set_hexpand(True)
        note_text.set_valign(CENTER)
        members.append(note_text)
        content.append(members)

    def chat_scenes(self, chat):
        if any(str(m.get("url")) == "demo:collage" for m in self.history_for(chat["id"])):
            return ["mountain", "trail", "coffee"]
        return []

    def detail_notes(self, content, chat):
        add = icon_button("plus", lambda: self.edit_note(chat), "新增記事", 16)
        row = box(H, 0, "section-label")
        title = label("記下重要的事")
        title.set_hexpand(True)
        title.set_valign(CENTER)
        row.append(title)
        row.append(add)
        content.append(row)
        note = self.note_for(chat)
        if note:
            content.append(self.note_card(chat, note))
        else:
            content.append(label("還沒有記事\n記下你們的小小計畫。", "empty", xalign=0.5))
        content.append(button(None, lambda: self.edit_note(chat), "secondary", text="編輯記事" if note else "＋ 新增記事"))
        content.append(padded_label("記事只保存在這台電腦，不會同步到 LINE 記事本，也不會傳送給聊天成員。"))

    def detail_albums(self, content, chat):
        content.append(self.section_label("收藏一起走過的風景"))
        scenes = self.chat_scenes(chat)
        if scenes:
            card = button(None, lambda: self.open_lightbox(0), None, "查看照片")
            frame = box(V, 0, "note-card")
            frame.set_overflow(Gtk.Overflow.HIDDEN)
            frame.append(Art("scene", scenes[0], getattr(self, "detail_width", 238) - 38, 100))
            frame.append(label("聊天中的照片", "note-title"))
            frame.append(label(f"{len(scenes)} 張照片 · 離線展示", "file-size"))
            card.set_child(frame)
            content.append(card)
        else:
            content.append(label("相簿還是空的\n此版本尚不支援 LINE 相簿。", "empty", xalign=0.5))

    def detail_files(self, content, chat):
        files = [m for m in self.history_for(chat["id"]) if m.get("kind") == "file"]
        content.append(self.section_label(f"{len(files)} 個共享檔案"))
        for message in files:
            row = box(H, 8, "detail-file")
            row.append(file_badge(message.get("filename"), True))
            names = box(V, 0, hexpand=True)
            names.append(label(message.get("filename") or "檔案", "file-name", wrap=True, max_chars=18))
            names.append(label(file_size(message.get("size")), "file-size"))
            row.append(names)
            download = icon_button("download", lambda item=message: self.download_file(item), "另存檔案", 14)
            download.set_valign(CENTER)
            row.append(download)
            content.append(row)
        if not files:
            content.append(label("還沒有共享檔案\n從對話中傳送第一個檔案。", "empty", xalign=0.5))

    # ----------------------------------------------------------- alternates
    def render_alternate(self):
        clear(self.alternate)
        if self.section == "friends":
            self.alternate.append(label("讓聯繫，更靠近。", "view-title"))
            self.alternate.append(label("一句簡單的問候，就是一段好日常的開始。", "view-lead"))
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=15, row_spacing=15, min_children_per_line=1, max_children_per_line=6)
            grid.add_css_class("friend-grid")
            grid.set_valign(START)
            for contact in self.visible_friends():
                card = box(H, 13, "friend-card")
                card.set_size_request(210, -1)
                card.append(person_avatar(contact["id"], self.state.profile.get("id", ""), 43, 16))
                names = box(V, 0, valign=CENTER, hexpand=True)
                names.append(label(contact.get("name", "好友"), "friend-name", ellipsize=True))
                names.append(label(contact.get("status") or "　", "friend-status", ellipsize=True))
                card.append(button(names, lambda value=contact["id"]: self.open_profile(value), None, contact.get("name")))
                chat_button = icon_button("chat", lambda value=contact["id"]: self.open_direct(value), "傳送訊息", 18)
                chat_button.set_valign(CENTER)
                card.append(chat_button)
                grid.insert(card, -1)
            self.alternate.append(grid)
            if not self.visible_friends():
                self.alternate.append(label("沒有符合的好友\n試試其他搜尋關鍵字。", "empty", xalign=0.5))
        else:
            self.alternate.append(label("熟悉的聲音，一直都在。", "view-title"))
            self.alternate.append(label("通話紀錄 · 此版本不提供語音與視訊通話。", "view-lead"))
            empty = box(V, 0, "empty", halign=CENTER)
            empty.set_margin_top(35)
            glyph = icon("phone", 36)
            glyph.set_halign(CENTER)
            empty.append(glyph)
            empty.append(label("還沒有通話紀錄\n收到的來電通知會顯示在聊天中。", xalign=0.5))
            choose = button(None, lambda: self.navigate("friends"), "text-button", text="選擇好友")
            choose.set_halign(CENTER)
            choose.set_margin_top(15)
            empty.append(choose)
            self.alternate.append(empty)

    def open_direct(self, contact_id):
        chat_id = self.ensure_direct(contact_id)
        if chat_id:
            self.close_modal()
            self.select_chat(chat_id)

    # ----------------------------------------------------------------- modals
    def open_modal(self, kind, title, body, actions=None, wide=False, css=""):
        self.close_modal()
        layer = box(V, 0, "modal-overlay", hexpand=True, vexpand=True)
        card = box(V, 0, "modal " + css, halign=CENTER, valign=CENTER, vexpand=True)
        card.set_size_request(600 if wide else 460, -1)
        content = box(V, 0)
        head = box(H, 0, "modal-head")
        heading = label(title, "modal-title", wrap=True)
        heading.set_hexpand(True)
        heading.set_valign(CENTER)
        head.append(heading)
        head.append(icon_button("x", self.close_modal, "關閉", 18))
        content.append(head)
        content.append(body)
        if actions is not None:
            content.append(actions)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(max(300, int((self.window.get_height() or 800) * 0.9) - 52))
        scrolled.set_child(content)
        card.append(scrolled)
        layer.append(card)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda gesture, n, x, y: self.close_modal() if layer.pick(x, y, Gtk.PickFlags.DEFAULT) is layer else None)
        layer.add_controller(click)
        self.overlay.add_overlay(layer)
        self.pages.set_can_focus(False)
        self.modal = layer
        self.modal_kind = kind
        GLib.idle_add(lambda: self.modal is layer and layer.child_focus(Gtk.DirectionType.TAB_FORWARD) and False)
        return layer

    def close_modal(self):
        if not self.modal:
            return
        self.overlay.remove_overlay(self.modal)
        self.modal = None
        self.modal_kind = ""
        self.pages.set_can_focus(True)

    def modal_actions(self, primary=None, primary_text="確定", secondary_text="取消", error=False, center=False):
        wrapper = box(V, 0)
        message = label("", "modal-error", wrap=True)
        if error:
            wrapper.append(message)
        row = box(H, 9, "modal-actions", halign=CENTER if center else END)
        if secondary_text:
            row.append(button(None, self.close_modal, "secondary", text=secondary_text))
        if primary:
            row.append(button(None, primary, "primary", text=primary_text))
        wrapper.append(row)
        wrapper.error_label = message
        return wrapper

    def window_key(self, controller, keyval, keycode, modifiers):
        if keyval == Gdk.KEY_Escape:
            if self.menu.get_visible():
                self.menu.popdown()
                return True
            if self.modal:
                self.close_modal()
                return True
            if self.search_bar.get_visible():
                self.close_search()
                return True
        if keyval in (Gdk.KEY_r, Gdk.KEY_R) and modifiers & Gdk.ModifierType.CONTROL_MASK:
            self.refresh()
            return True
        if self.modal_kind == "lightbox" and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.open_lightbox(self.lightbox_index + (1 if keyval == Gdk.KEY_Right else -1))
            return True
        return False

    def open_help(self):
        body = box(V, 0)
        blocks = []
        if self.state.mode == "demo":
            blocks.append(("這是一份離線展示", "所有訊息、好友與貼圖都是示範資料，不會連線至 LINE，也不會傳給其他人。重新啟動程式即重設；本機記事仍會保存在這台電腦。"))
        blocks += [
            ("非官方的 LINE Linux 用戶端", "以 GTK 4 原生元件重現 LINE 桌面版的聊天體驗，透過內建通訊核心直接連線 LINE，不需要瀏覽器。文字與檔案使用 Letter Sealing 加密傳送。"),
            ("可以試試這些小事", "傳送訊息、貼圖和表情符號；拖入檔案並確認傳送；在聊天室資訊寫下記事；用 Keep 筆記記下自己的小靈感。懸停在訊息旁的「⋯」可複製或轉傳。按 Ctrl + R 重新整理聊天與好友。"),
            ("保存在哪裡", "登入憑證交給系統鑰匙圈保管，密碼不會儲存。記事、Keep 筆記與偏好設定只保存在這台電腦，不會同步到 LINE。"),
            ("目前的限制", "此版本不提供語音與視訊通話、LINE 記事本與相簿、收回、回覆或回應訊息，也無法預覽照片。"),
        ]
        for title, text in blocks:
            block = box(V, 0, "help-block")
            block.append(label(title, "help-title"))
            block.append(label(text, "help-text", wrap=True))
            body.append(block)
        links = box(V, 0, "help-block last")
        links.append(label("官方使用說明", "help-title"))
        row = box(H, 0)
        for title, uri in HELP_LINKS:
            row.append(button(None, lambda value=uri: self.open_uri(value), "link", text=title))
        links.append(row)
        body.append(links)
        self.open_modal("help", "日常的聯繫，從這裡開始。", body, self.modal_actions(self.close_modal, "開始聊聊", None), wide=True)

    def open_uri(self, uri):
        try:
            Gtk.UriLauncher.new(uri).launch(self.window, None, None, None)
        except AttributeError:
            Gtk.show_uri(self.window, uri, Gdk.CURRENT_TIME)

    def open_settings(self):
        body = box(V, 0)
        name = Gtk.Entry()
        name.set_text(self.state.profile.get("name", "尚未登入"))
        name.set_editable(False)
        name.set_can_focus(False)
        body.append(self.field("顯示名稱", name))
        switches = {}
        for key, title in (("notifications", "訊息通知"), ("enter_send", "按 Enter 傳送訊息")):
            row = box(H, 0, "toggle-line")
            text = label(title)
            text.set_hexpand(True)
            row.append(text)
            switch = Gtk.Switch(active=self.prefs.settings[key], valign=CENTER)
            set_accessible_label(switch, title)
            switches[key] = switch
            row.append(switch)
            body.append(row)
        sizes = [(13, "小"), (14, "標準"), (16, "大")]
        size_choice = Gtk.DropDown.new_from_strings([title for _, title in sizes])
        size_choice.set_selected([value for value, _ in sizes].index(self.prefs.settings["font_size"]))
        size_field = self.field("介面文字大小", size_choice)
        size_field.set_margin_top(20)
        body.append(size_field)
        body.append(label("顯示名稱由 LINE 帳號提供。設定保存在這台電腦；訊息通知使用桌面系統通知。", "subtle", wrap=True))
        account = box(V, 0, "help-block last")
        account.set_margin_top(8)
        account.append(label("帳號與連線", "help-title"))
        if self.state.mode == "demo":
            stored = "離線展示帳號 · 不連接 LINE，也不會寫入鑰匙圈。"
        elif self.persistent is None:
            stored = "尚未登入。"
        else:
            stored = "已使用系統鑰匙圈，下次可恢復登入。" if self.persistent else "本次登入僅保留在記憶體。關閉程式後需要重新登入。"
        account.append(label(stored, "help-text", wrap=True))
        capabilities = self.state.capabilities
        titles = {"text": "文字", "stickers": "貼圖", "files": "檔案", "calls": "通話", "notes": "LINE 記事本", "albums": "相簿"}
        ready = "、".join(title for key, title in titles.items() if capabilities.get(key)) or "—"
        missing = "、".join(title for key, title in titles.items() if not capabilities.get(key)) or "—"
        account.append(label(f"可使用：{ready} · 尚未支援：{missing}", "help-text", wrap=True))
        controls = box(H, 9)
        controls.set_margin_top(10)
        refresh = button(None, lambda: (self.close_modal(), self.refresh()), "secondary", text="重新整理聊天與好友")
        refresh.set_sensitive(self.state.session == "ready")
        controls.append(refresh)
        self.logout_button = button(None, lambda: (self.close_modal(), self.logout()), "secondary danger", text="登出")
        self.logout_button.set_sensitive(self.state.session != "logged_out")
        controls.append(self.logout_button)
        account.append(controls)
        body.append(account)

        def save():
            try:
                font = sizes[size_choice.get_selected()][0]
                self.window.remove_css_class(f"font-{self.prefs.settings['font_size']}")
                self.prefs.save_settings(notifications=switches["notifications"].get_active(), enter_send=switches["enter_send"].get_active(), font_size=font)
                self.window.add_css_class(f"font-{font}")
            except OSError:
                self.toast("無法儲存設定。請確認資料目錄的權限。", True)
                return
            self.close_modal()
            self.render_header()
            self.toast("設定已儲存")
        self.settings_save = save
        self.open_modal("settings", "讓 LINE，更像你。", body, self.modal_actions(save, "儲存設定", "關閉"))

    def open_profile(self, person_id):
        own = self.state.profile.get("id", "")
        me = bool(person_id) and person_id == own
        contact = self.state.profile if me else self.state.contacts.get(person_id)
        if not contact:
            if person_id in self.state.chats:
                contact = {"id": person_id, "name": self.state.chats[person_id].get("name", "成員"), "status": ""}
            else:
                self.toast("無法取得這位成員的個人檔案。")
                return
        card = box(V, 0)
        portrait = person_avatar(person_id or "me", own or "me", 68, 23)
        portrait.set_halign(CENTER)
        portrait.set_margin_bottom(13)
        card.append(portrait)
        card.append(label(contact.get("name", "LINE"), "profile-name", xalign=0.5, wrap=True))
        card.append(label(contact.get("status") or ("把日子過成喜歡的樣子。" if me else "　"), "profile-status", xalign=0.5, wrap=True))
        if me:
            actions = self.modal_actions(lambda: (self.close_modal(), self.open_settings()), "設定", None, center=True)
        else:
            favorite = self.prefs.has("favorites", person_id)
            star = button(None, lambda: self.toggle_favorite(person_id), "text-button", text="★ 已加入我的最愛" if favorite else "☆ 加入我的最愛")
            star.set_halign(CENTER)
            card.append(star)
            actions = box(H, 9, "modal-actions", halign=CENTER)
            actions.append(button(icon_text("phone", "語音通話", 16), lambda: self.unsupported("此版本不提供語音通話。"), "secondary"))
            actions.append(button(None, lambda: self.open_direct(person_id), "primary", text="傳送訊息"))
        self.open_modal("profile", "個人檔案", card, actions)

    def toggle_favorite(self, person_id):
        try:
            self.prefs.toggle("favorites", person_id)
        except OSError:
            self.toast("無法儲存偏好設定。請確認資料目錄的權限。", True)
            return
        self.open_profile(person_id)
        self.render_list()
        if self.section == "friends":
            self.render_alternate()

    def open_members(self, chat):
        body = box(V, 0)
        own = self.state.profile.get("id", "")
        members = self.members_of(chat)
        for member in members:
            row = box(H, 12)
            row.append(person_avatar(member, own, 43, 16))
            name = label(self.state.name_of(member) + ("（你）" if member == own else ""))
            name.set_hexpand(True)
            name.set_valign(CENTER)
            row.append(name)
            chevron = icon("chevron", 15)
            chevron.set_valign(CENTER)
            row.append(chevron)
            item = button(row, lambda value=member: self.open_profile(value), "chat-row")
            body.append(item)
        if len(members) < self.member_count(chat):
            body.append(label(f"另有 {self.member_count(chat) - len(members)} 位成員未列出。", "subtle", wrap=True))
        self.open_modal("members", f"{chat.get('name', '聊天室')}的成員", body)

    def open_forward(self, message):
        body = box(V, 0)
        for chat in self.visible_chats():
            if chat["id"] == self.state.selected:
                continue
            row = box(H, 12)
            row.append(chat_avatar(chat, self.state.profile.get("id", ""), 43, 16))
            name = label(chat.get("name", "對話"))
            name.set_hexpand(True)
            name.set_valign(CENTER)
            row.append(name)
            chevron = icon("chevron", 15)
            chevron.set_valign(CENTER)
            row.append(chevron)
            body.append(button(row, lambda target=chat: self.forward(message, target), "chat-row contact-choice"))
        self.open_modal("forward", "把這則訊息，分享給…", body)

    def forward(self, message, chat):
        if chat["id"] != KEEP_ID and not self.can_send_text_to(chat["id"]):
            self.toast("目前無法傳送訊息，請等待連線完成。", True)
            return
        self.close_modal()
        self.deliver_text(chat["id"], message.get("text", ""))
        self.toast(f"已轉傳到「{chat.get('name', '對話')}」")

    def can_send_text_to(self, chat_id):
        return chat_id == KEEP_ID or bool(self.state.session == "ready" and self.state.capabilities.get("text"))

    # ------------------------------------------------------------------ notes
    def open_note(self, chat):
        note = self.note_for(chat)
        if not note:
            self.edit_note(chat)
            return
        body = box(V, 0)
        author = note.get("author")
        who = self.state.name_of(author) + " · 示範記事" if author else "你 · 只保存在這台電腦"
        body.append(label(f"{who} · {list_time(note.get('updated'))}", "subtle"))
        text = label(note["body"], "help-text", wrap=True, selectable=True)
        text.set_margin_top(10)
        body.append(text)
        actions = box(H, 9, "modal-actions", halign=END)
        actions.append(button(None, lambda: self.confirm_delete_note(chat), "secondary danger", text="刪除"))
        actions.append(button(None, lambda: self.edit_note(chat), "primary", text="編輯記事"))
        self.open_modal("note-view", note["title"], body, actions)

    def edit_note(self, chat=None):
        chat = chat or self.current_chat()
        if not chat:
            return
        chat_id = chat["id"]
        account = self.account_key()
        generation = self.generation
        note = self.note_for(chat)
        body = box(V, 0)
        title = Gtk.Entry(placeholder_text="記事標題", max_length=80)
        title.set_text(note["title"] if note else "")
        body.append(self.field("標題", title))
        editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        editor.set_size_request(-1, 120)
        editor.get_buffer().set_text(note["body"] if note else "")
        body.append(self.field("內容", editor))
        body.append(label("記事只保存在這台電腦，不會同步到 LINE 記事本，也不會傳送給聊天成員。", "subtle", wrap=True))
        actions = None

        def save():
            if generation != self.generation:
                return
            buffer = editor.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            heading = title.get_text().strip()
            if not heading and not text.strip():
                actions.error_label.set_text("請填寫標題或內容。")
                return
            try:
                self.notes.save(account, chat_id, heading, text)
            except OSError:
                actions.error_label.set_text("無法儲存記事。請確認資料目錄的權限。")
                return
            self.close_modal()
            if self.state.selected == chat_id:
                self.render_details()
            self.toast("記事已保存在這台電腦")
        actions = self.modal_actions(save, "儲存記事", error=True)
        self.note_title, self.note_editor, self.note_save = title, editor, save
        self.open_modal("note-edit", "編輯記事" if note else "記下，值得記得的小事。", body, actions)

    def confirm_delete_note(self, chat):
        note = self.note_for(chat)
        if not note:
            return
        account, chat_id = self.account_key(), chat["id"]

        def delete():
            self.notes.delete(account, chat_id)
            if self.state.mode == "demo" and chat_id == "demo-weekend":
                self.demo_note_hidden = True
            self.close_modal()
            self.render_details()
            self.toast("記事已刪除")
        body = label(f"「{note['title']}」會從這台電腦移除。", "subtle", wrap=True)
        self.open_modal("note-delete", "刪除這則記事？", body, self.modal_actions(delete, "刪除記事"))

    # --------------------------------------------------------------- lightbox
    def open_lightbox(self, index):
        scenes = self.chat_scenes(self.current_chat()) if self.current_chat() else []
        if not scenes:
            return
        self.lightbox_index = index % len(scenes)
        body = box(V, 0)
        picture = Art("scene", scenes[self.lightbox_index], 548, 400, "r8")
        picture.set_halign(CENTER)
        body.append(picture)
        controls = box(H, 25, halign=CENTER)
        controls.set_margin_top(18)
        controls.append(icon_button("back", lambda: self.open_lightbox(self.lightbox_index - 1), "上一張", 20))
        controls.append(label(f"{self.lightbox_index + 1} / {len(scenes)}", "subtle"))
        controls.append(icon_button("chevron", lambda: self.open_lightbox(self.lightbox_index + 1), "下一張", 20))
        body.append(controls)
        self.open_modal("lightbox", "聊天中的照片 · 離線展示", body, wide=True)

    # ------------------------------------------------------------- stickers
    def open_stickers(self):
        chat = self.current_chat()
        if not chat:
            return
        if chat["id"] == KEEP_ID:
            self.open_sticker_modal([], KEEP_ID, self.generation, emoji_only=True)
            return
        if not self.core or self.state.session != "ready":
            return
        chat_id = chat["id"]
        generation = self.generation

        def loaded(response):
            if generation != self.generation or self.state.session != "ready":
                return
            if not response.get("ok"):
                self.toast(self.error(response), True)
                return
            self.open_sticker_modal(response.get("result", {}).get("packs", []), chat_id, generation)
        self.core.request("stickers.list", callback=loaded)

    def open_sticker_modal(self, packs, chat_id, generation, emoji_only=False):
        def first(index):
            stickers = packs[index].get("stickers", []) if isinstance(index, int) else []
            return (packs[index], stickers[0]) if stickers else None
        state = {"tab": "emoji" if emoji_only or not packs else 0, "page": 0}
        state["selected"] = first(state["tab"])
        body = box(V, 0)
        tabs = box(H, 8, "sticker-tabs")
        tab_scroll = Gtk.ScrolledWindow()
        tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        tab_scroll.set_child(tabs)
        body.append(tab_scroll)
        content = box(V, 0)
        body.append(content)
        footer = box(V, 0)
        self.sticker_choices = []

        def send():
            item = state["selected"]
            if not item or generation != self.generation or self.state.session != "ready":
                return
            pack, sticker = item
            self.core.request("messages.sticker", {"chat_id": chat_id, "package_id": sticker.get("package_id") or pack["id"], "sticker_id": sticker["id"]},
                              lambda result: self.toast(self.error(result), True) if not result.get("ok") else self.after_message(chat_id))
            self.close_modal()

        def render():
            clear(tabs)
            clear(content)
            clear(footer)
            self.sticker_choices = []
            for index, pack in enumerate(packs):
                widget = button(None, lambda value=index: choose_tab(value), "filter" + (" active" if state["tab"] == index else ""), text=pack.get("name", "貼圖"))
                tabs.append(widget)
            tabs.append(button(None, lambda: choose_tab("emoji"), "filter" + (" active" if state["tab"] == "emoji" else ""), text="表情符號"))
            if state["tab"] == "emoji":
                grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
                for index, glyph in enumerate(EMOJI):
                    grid.attach(button(None, lambda value=glyph: self.insert_emoji(value), "emoji", f"插入 {glyph}", text=glyph), index % 7, index // 7, 1, 1)
                content.append(grid)
                hint = label("選擇表情符號後，會加入訊息輸入框。", "subtle")
                hint.set_margin_top(12)
                content.append(hint)
                return
            pack = packs[state["tab"]]
            stickers = pack.get("stickers", [])
            demo_pack = pack.get("id") == "demo"
            grid = Gtk.Grid(column_spacing=9, row_spacing=9, column_homogeneous=True)
            for index, sticker in enumerate(stickers[state["page"] * 48:(state["page"] + 1) * 48]):
                tile = box(V, 2)
                url = str(sticker.get("url", ""))
                if url.startswith("demo:bear-"):
                    picture = Art("bear", url.rsplit("-", 1)[-1], 88, 85)
                else:
                    picture = self.sticker_picture(url, 88, 85)
                picture.set_halign(CENTER)
                tile.append(picture)
                mood = MOODS[int(url.rsplit("-", 1)[-1]) % 8] if url.startswith("demo:bear-") else ""
                if mood:
                    tile.append(label(mood, xalign=0.5))
                selected = state["selected"] and state["selected"][1] is sticker
                widget = button(tile, lambda value=sticker: pick(value), "sticker-tile" + (" selected" if selected else ""), f"預覽 {mood or '貼圖'}")
                self.sticker_choices.append(widget)
                grid.attach(widget, index % 4, index // 4, 1, 1)
            content.append(grid)
            pages = max(1, (len(stickers) + 47) // 48)
            if pages > 1:
                paging = box(H, 8, halign=CENTER)
                paging.set_margin_top(12)
                previous = icon_button("back", lambda: turn(-1), "上一頁", 18)
                previous.set_sensitive(state["page"] > 0)
                following = icon_button("chevron", lambda: turn(1), "下一頁", 18)
                following.set_sensitive(state["page"] + 1 < pages)
                paging.append(previous)
                paging.append(label(f"{state['page'] + 1} / {pages}", "subtle"))
                paging.append(following)
                content.append(paging)
            if state["selected"]:
                sticker = state["selected"][1]
                url = str(sticker.get("url", ""))
                name = f"「{MOODS[int(url.rsplit('-', 1)[-1]) % 8]}」" if url.startswith("demo:bear-") else "貼圖"
                caption = f"已選擇{name} · {pack.get('name', '貼圖')}" + (" · 原創示範貼圖" if demo_pack else "")
            else:
                caption = "選擇一張貼圖，再按傳送。"
            hint = label(caption, "subtle")
            hint.set_margin_top(15)
            content.append(hint)
            footer.append(self.modal_actions(send, "傳送貼圖"))
            self.sticker_send_button = footer.get_first_child().get_last_child().get_last_child()
            self.sticker_send_button.set_sensitive(bool(state["selected"]))

        def choose_tab(value):
            state.update(tab=value, page=0, selected=first(value))
            render()

        def turn(amount):
            state["page"] += amount
            render()

        def pick(sticker):
            pack = packs[state["tab"]]
            state["selected"] = (pack, sticker)
            render()
        render()
        body.append(footer)
        self.open_modal("stickers", "用表情，說點心裡話。", body)

    def insert_emoji(self, glyph):
        self.close_modal()
        buffer = self.compose.get_buffer()
        buffer.insert_at_cursor(glyph)
        self.compose.grab_focus()

    def sticker_picture(self, url, width, height):
        picture = Gtk.Picture()
        picture.set_size_request(width, height)
        picture.set_can_shrink(True)
        if url:
            def loaded(data):
                try:
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                    if texture.get_width() <= 4096 and texture.get_height() <= 4096:
                        picture.set_paintable(texture)
                except GLib.Error:
                    pass
            self.pictures.load(url, loaded)
        return picture

    # ------------------------------------------------------------------ files
    def can_attach(self):
        chat = self.current_chat()
        return bool(chat and chat["id"] != KEEP_ID and self.core and self.state.session == "ready" and self.state.capabilities.get("files"))

    def choose_files(self, images=False):
        chat = self.current_chat()
        if chat and chat["id"] == KEEP_ID:
            self.toast("Keep 筆記目前只保存文字。")
            return
        if not self.can_attach():
            return
        chat_id = chat["id"]
        generation = self.generation
        dialog = Gtk.FileChooserNative.new("傳送照片" if images else "傳送檔案", self.window, Gtk.FileChooserAction.OPEN, "選擇", "取消")
        dialog.set_select_multiple(True)
        if images:
            pictures = Gtk.FileFilter()
            pictures.set_name("照片")
            pictures.add_mime_type("image/*")
            dialog.add_filter(pictures)
        self.track_dialog(dialog)
        self.file_chooser = dialog

        def selected(chooser, response):
            if generation == self.generation and self.state.session == "ready" and response == Gtk.ResponseType.ACCEPT:
                files = chooser.get_files()
                paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
                self.queue_files([path for path in paths if path], chat_id)
            chooser.destroy()
        dialog.connect("response", selected)
        dialog.show()

    def dropped(self, target, value, x, y):
        self.drag_overlay.set_visible(False)
        if not self.can_attach():
            return False
        paths = [item.get_path() for item in value.get_files() if item.get_path()]
        self.queue_files(paths, self.state.selected)
        return True

    def queue_files(self, paths, chat_id):
        accepted = []
        for path in paths[:MAX_QUEUE]:
            try:
                info = Path(path).stat()
            except OSError:
                continue
            if Path(path).is_file() and info.st_size <= MAX_FILE:
                accepted.append({"path": path, "name": Path(path).name, "size": info.st_size})
        if len(accepted) != len(paths):
            self.toast("每次最多 20 個檔案，單檔上限 25 MiB；資料夾無法傳送。")
        if not accepted:
            return
        self.open_queue(accepted, chat_id)

    def open_queue(self, files, chat_id):
        chat = self.state.chats.get(chat_id)
        if not chat:
            return
        generation = self.generation
        body = box(V, 0)
        note = "離線展示 · 不會上傳到 LINE" if self.state.mode == "demo" else "以 Letter Sealing 加密傳送"
        lead = label(f"傳送至「{chat.get('name', '對話')}」 · {note}。", "subtle", wrap=True)
        lead.set_margin_bottom(18)
        body.append(lead)
        queue = box(V, 9)
        body.append(queue)
        holder = {}

        def render():
            clear(queue)
            for index, item in enumerate(files):
                row = box(H, 12, "queue-item")
                if item["name"].lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif", "webp"):
                    picture = Gtk.Picture.new_for_filename(item["path"])
                    picture.set_size_request(50, 50)
                    picture.set_can_shrink(True)
                    try:
                        picture.set_content_fit(Gtk.ContentFit.COVER)
                    except AttributeError:
                        pass
                    picture.add_css_class("r6")
                    picture.set_overflow(Gtk.Overflow.HIDDEN)
                    row.append(picture)
                else:
                    row.append(file_badge(item["name"]))
                names = box(V, 0, valign=CENTER, hexpand=True)
                names.append(label(item["name"], "queue-name", wrap=True))
                size = label(file_size(item["size"]), "subtle")
                size.set_margin_top(5)
                names.append(size)
                row.append(names)
                remove = icon_button("x", lambda value=index: (files.pop(value), render()), f"移除 {item['name']}", 16)
                remove.set_valign(CENTER)
                row.append(remove)
                queue.append(row)
            if not files:
                queue.append(label("尚未選擇檔案。", "empty", xalign=0.5))
            if holder.get("send"):
                holder["send"].set_sensitive(bool(files))
                holder["send"].get_child().set_text(f"傳送 {len(files)} 個檔案")

        def send():
            if generation != self.generation or self.state.session != "ready" or not files:
                return
            self.close_modal()
            self.send_files(list(files), chat_id, generation)
        actions = self.modal_actions(send, f"傳送 {len(files)} 個檔案")
        holder["send"] = actions.get_last_child().get_last_child()
        self.queue_send_button = holder["send"]
        render()
        self.open_modal("files", "準備好分享了嗎？", body, actions)

    def send_files(self, files, chat_id, generation):
        remaining = list(files)
        results = {"ok": 0}

        def next_file(response=None):
            if generation != self.generation:
                return
            if response is not None:
                if response.get("ok"):
                    results["ok"] += 1
                else:
                    self.toast(self.error(response), True)
                self.after_message(chat_id)
            if not remaining:
                if results["ok"]:
                    self.toast(f"已傳送 {results['ok']} 個檔案" if self.state.mode != "demo" else f"已加入 {results['ok']} 個示範檔案，未上傳到 LINE。")
                return
            item = remaining.pop(0)
            self.toast(f"正在傳送「{item['name']}」…")
            self.core.request("files.send", {"chat_id": chat_id, "path": item["path"]}, next_file)
        next_file()

    def download_file(self, message):
        if self.state.mode == "demo":
            self.toast("示範檔案不會下載。登入 LINE 後即可另存收到的檔案。")
            return
        generation = self.generation
        chooser = Gtk.FileChooserNative.new("另存檔案", self.window, Gtk.FileChooserAction.SAVE, "儲存", "取消")
        self.track_dialog(chooser)
        self.download_chooser = chooser
        chooser.set_current_name(Path(message.get("filename") or "LINE-attachment").name)

        def selected(dialog, response):
            if generation == self.generation and self.state.session == "ready" and response == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file and file.get_path():
                    self.toast("正在下載並驗證附件…")
                    self.core.request("files.download", {"chat_id": message["chat_id"], "message_id": message["id"], "path": file.get_path()},
                                      lambda result: self.toast("檔案已儲存。" if result.get("ok") else self.error(result), not result.get("ok")))
            dialog.destroy()
        chooser.connect("response", selected)
        chooser.show()

    # --------------------------------------------------------------- session
    def update_capabilities(self):
        files = bool(self.state.capabilities.get("files"))
        self.file_button.set_sensitive(files)
        self.photo_button.set_sensitive(files)
        self.sticker_button.set_sensitive(bool(self.state.capabilities.get("stickers")))
        self.update_send()

    def refresh(self):
        if self.core and self.state.session == "ready":
            self.core.request("chats.refresh", callback=lambda reply: self.toast("已重新整理") if reply.get("ok") else self.toast(self.error(reply), True))

    def mark_read(self):
        messages = [m for m in self.state.messages.get(self.state.selected, []) if not str(m["id"]).startswith("pending-")]
        if messages and self.core and self.state.selected != KEEP_ID:
            self.core.request("messages.read", {"chat_id": self.state.selected, "message_id": messages[-1]["id"]})

    def logout(self):
        if not self.core:
            return

        def finished(response):
            if not response.get("ok"):
                self.toast(self.error(response), True)
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
        self.close_modal()
        self.menu.popdown()
        self.state.clear()
        self.persistent = None
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
        self.section = "chats"
        self.render_rail()
        self.render_sidebar()
        self.render_header()
        self.render_messages()
        self.render_details()
        self.update_send()

    def track_dialog(self, dialog):
        self.dialogs.add(dialog)
        signal = "response" if isinstance(dialog, Gtk.NativeDialog) else "close-request"

        def closed(*_):
            self.dialogs.discard(dialog)
            return False
        dialog.connect(signal, closed)

    def set_footer(self, text, level=""):
        self.connection.set_text(text)
        for name in ("warning", "error"):
            self.footer.remove_css_class(name)
        if level:
            self.footer.add_css_class(level)

    def load_local(self):
        demo = self.state.mode == "demo"
        account = self.account_key()
        if self.prefs.account != account:
            self.prefs.load(account, demo)
        if self.keep.account != account:
            self.keep.load(account, demo)
            self.state.messages[KEEP_ID] = self.keep.messages()
        self.state.chats[KEEP_ID] = self.keep.chat()

    def on_event(self, event, data):
        if event == "session":
            state = data.get("state", "error")
            if state == "logged_out":
                self.clear_session()
                self.auth_status.set_text(data.get("message") or "使用手機 LINE 掃描行動條碼，或以電子郵件登入。")
                return
            self.state.session = state
            if data.get("profile"):
                self.state.profile = data["profile"]
            if "persistent" in data and self.state.mode != "demo":
                self.persistent = bool(data["persistent"])
            if state == "ready":
                self.pages.set_visible_child_name("workspace")
                self.auth_busy(False)
                self.password_entry.set_text("")
                self.qr_picture.set_paintable(None)
                self.pin_label.set_text("")
                if self.state.mode != "demo" and self.persistent is False:
                    self.set_footer("已連線 · 登入未保存至鑰匙圈", "warning")
                else:
                    self.set_footer("每一段對話，都在這裡。")
            elif state == "connecting":
                self.set_footer("正在重新連線…", "warning")
            elif state == "error":
                self.set_footer("連線已中斷", "error")
                self.toast(data.get("message", "連線發生錯誤"), True)
                self.auth_busy(False)
                self.auth_status.set_text(data.get("message", "請重新登入。"))
            self.update_send()
        elif event == "snapshot":
            if self.state.session == "logged_out":
                return
            self.state.snapshot(data)
            self.load_local()
            if self.state.selected and self.state.selected in self.state.chats:
                self.state.chats[self.state.selected]["unread"] = 0
            self.render_rail()
            self.render_sidebar()
            if self.section in ("friends", "calls"):
                self.render_alternate()
            if not self.state.selected or self.state.selected not in self.state.chats:
                rows = self.visible_chats()
                pinned = [chat for chat in rows if self.prefs.has("pinned", chat["id"])]
                first = (pinned or rows or [None])[0]
                if first:
                    self.select_chat(first["id"])
            else:
                self.render_header()
                self.render_details()
        elif event == "message":
            if self.state.session == "logged_out":
                return
            message = data.get("message", {})
            if not self.state.add_message(message):
                return
            chat_id = str(message.get("chat_id", ""))
            self.notify(message)
            if chat_id == self.state.selected:
                self.after_message(chat_id)
                self.mark_read()
            else:
                self.render_list()
                self.render_rail()
                self.render_sidebar()
        elif event == "notice":
            self.toast(data.get("message", ""), data.get("level") == "error")
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

    def notify(self, message):
        chat_id = str(message.get("chat_id", ""))
        chat = self.state.chats.get(chat_id)
        if message.get("mine") or not chat or not self.prefs.settings["notifications"] or self.prefs.has("muted", chat_id):
            return
        if self.window.is_active() and chat_id == self.state.selected:
            return
        notification = Gio.Notification.new(chat.get("name", "LINE"))
        body = message.get("text") if message.get("kind") == "text" else media_label(message)
        if chat.get("kind") == "group":
            body = f"{message.get('sender_name') or '成員'}：{body}"
        notification.set_body(str(body)[:200])
        self.send_notification("chat-" + chat_id[:64], notification)

    def toast(self, text, error=False):
        if not text:
            return
        self.toast_label.set_text(text)
        self.toast_label.add_css_class("error") if error else self.toast_label.remove_css_class("error")
        self.toast_label.set_visible(True)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)

        def hide():
            self.toast_timer = 0
            self.toast_label.set_visible(False)
            return GLib.SOURCE_REMOVE
        self.toast_timer = GLib.timeout_add(6000 if error else 3200, hide)

    def unsupported(self, text):
        self.toast(text)

    @staticmethod
    def error(response):
        return response.get("error", {}).get("message", "操作失敗，請稍後再試。")

    def shutdown_core(self, *_):
        self.pictures.closed = True
        if self.core:
            self.core.stop()
        self.window = None

    # ------------------------------------------------------------ smoke test
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

    def labels_in(self, widget):
        found = []
        child = widget.get_first_child()
        while child:
            if isinstance(child, Gtk.Label):
                found.append(child.get_text())
            found.extend(self.labels_in(child))
            child = child.get_next_sibling()
        return found

    def smoke_step(self):
        step = getattr(self, "smoke_phase", 0)
        started = getattr(self, "smoke_started", time.monotonic())
        self.smoke_started = started
        if time.monotonic() - started > 30:
            self.check("smoke finishes in time (phase %d)" % step, False)
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
        elif step == 1 and self.state.session == "ready" and len(self.state.chats) > 1 and self.state.selected:
            self.check("native workspace ready", self.pages.get_visible_child_name() == "workspace")
            self.check("demo is clearly identified", self.state.mode == "demo" and "離線展示" in self.mode_label.get_text())
            sidebar = self.labels_in(self.chat_list)
            self.check("chat list shows pinned and all sections", "置頂聊天" in sidebar and "所有聊天" in sidebar)
            self.check("reference chat is selected first", self.state.selected == "demo-weekend" and self.chat_title.get_text() == "週末散步計畫")
            self.check("artwork renders through the SVG loader", art.texture("bear", 0, 120, 108, None).get_width() == 120)
            self.check("details panel shows tabs and members", {"資訊", "筆記本", "相簿", "檔案", "4 位成員"} <= set(self.labels_in(self.detail_contents)))
            self.search.set_text("怡庭")
            self.check("search filters chat rows by title or member", "陳怡庭" in self.labels_in(self.chat_list) and "我們這一家" not in self.labels_in(self.chat_list))
            self.search.set_text("")
            self.set_filter("group")
            self.check("group filter keeps only groups", "陳怡庭" not in self.labels_in(self.chat_list) and "我們這一家" in self.labels_in(self.chat_list))
            self.set_filter("all")
            self.smoke_chat = self.state.selected
            self.compose.get_buffer().set_text("GTK 原生元件測試訊息")
            self.send_button.emit("clicked")
            self.smoke_phase = 2
        elif step == 2:
            messages = self.state.messages.get(self.smoke_chat, [])
            if any(item.get("text") == "GTK 原生元件測試訊息" and item.get("status") == "sent" for item in messages):
                self.check("text sends through core and resolves pending", not any(item.get("status") == "pending" for item in messages))
                self.check("messages deduplicate core event and reply", len([item for item in messages if item.get("text") == "GTK 原生元件測試訊息"]) == 1)
                self.check("group preview shows own prefix", self.state.chats[self.smoke_chat]["last_message"] == "你：GTK 原生元件測試訊息")
                self.compose.get_buffer().set_text("保留中的草稿")
                self.select_chat("demo-yiting")
                self.select_chat(self.smoke_chat)
                self.check("draft restores after selecting another chat", self.state.drafts.get(self.smoke_chat) == "保留中的草稿")
                self.compose.get_buffer().set_text("")
                self.edit_note(self.current_chat())
                self.note_title.set_text("週六集合")
                self.note_editor.get_buffer().set_text("本機筆記 GTK 測試")
                self.note_save()
                note = self.notes.read(self.account_key(), self.smoke_chat)
                self.check("local note saved privately", note and note["body"] == "本機筆記 GTK 測試" and self.notes.path(self.account_key(), self.smoke_chat).stat().st_mode & 0o777 == 0o600)
                self.check("note card appears in details", "週六集合" in self.labels_in(self.detail_contents) and self.modal is None)
                self.toggle_pref("muted")
                self.check("mute toggles locally", self.prefs.has("muted", self.smoke_chat) and self.muted_tag.get_visible())
                self.toggle_pref("muted")
                self.open_search()
                self.message_search_entry.set_text("咖啡")
                self.check("conversation search counts matches", self.search_count.get_text() == "2 則結果")
                self.close_search()
                self.select_chat(KEEP_ID)
                self.compose.get_buffer().set_text("Keep 本機測試")
                self.send_button.emit("clicked")
                self.check("Keep memo stays local", any(m.get("text") == "Keep 本機測試" for m in self.state.messages[KEEP_ID]) and self.keep.items[-1]["text"] == "Keep 本機測試")
                self.navigate("friends")
                self.check("friends view uses live widgets", self.side_title.get_text() == "好友" and self.main_stack.get_visible_child_name() == "alternate" and "讓聯繫，更靠近。" in self.labels_in(self.alternate))
                self.open_profile("demo-kai")
                self.check("profile modal opens", self.modal_kind == "profile")
                self.open_direct("demo-kai")
                self.check("message button opens a direct chat", self.state.selected == "demo-kai" and self.modal is None)
                self.open_settings()
                self.check("settings modal shows options", self.modal_kind == "settings")
                self.close_modal()
                self.select_chat(self.smoke_chat)
                self.check("return to conversation", self.main_stack.get_visible_child_name() == "chat")
                self.sticker_button.emit("clicked")
                self.smoke_phase = 3
        elif step == 3 and self.modal_kind == "stickers" and getattr(self, "sticker_choices", []):
            self.check("eight reference stickers listed", len(self.sticker_choices) == 8)
            self.sticker_choices[0].emit("clicked")
            self.sticker_send_button.emit("clicked")
            self.smoke_phase = 4
        elif step == 4:
            if len([item for item in self.state.messages.get(self.smoke_chat, []) if item.get("kind") == "sticker"]) >= 2:
                self.check("sticker picker sends through core", True)
                fixture = Path(self.options.smoke_test) / "demo-file.txt"
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text("GTK demo attachment")
                self.queue_files([str(fixture.resolve())], self.smoke_chat)
                self.check("file queue modal opens", self.modal_kind == "files")
                self.queue_send_button.emit("clicked")
                self.smoke_phase = 5
        elif step == 5:
            if any(item.get("kind") == "file" and item.get("filename") == "demo-file.txt" for item in self.state.messages.get(self.smoke_chat, [])):
                self.check("attachment appears in conversation", True)
                self.set_detail_tab("files")
                self.check("files tab lists attachments", "demo-file.txt" in self.labels_in(self.detail_contents))
                self.set_detail_tab("info")
                self.smoke_phase = 6
                self.smoke["ready_for_screenshot"] = True
                self.write_smoke()
                self.screenshot_deadline = time.monotonic() + 3
        elif step == 6 and time.monotonic() >= self.screenshot_deadline:
            self.check_stale_dialogs()
            self.finish_smoke()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def wait_for(predicate, seconds=3):
        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while not predicate():
            if time.monotonic() > deadline:
                return False
            context.iteration(False) or time.sleep(0.01)
        return True

    def check_stale_dialogs(self):
        class Recorder:
            def __init__(self):
                self.calls = []

            def request(self, method, params=None, callback=None):
                self.calls.append((method, params, callback))
        real_core, real_mode = self.core, self.state.mode
        recorder = Recorder()
        self.core = recorder
        try:
            self.choose_files()
            fixture = Gio.File.new_for_path(str((Path(self.options.smoke_test) / "demo-file.txt").resolve()))
            self.file_chooser.set_select_multiple(False)
            self.file_chooser.set_file(fixture)
            self.check("stale send test has an actual selected file", self.wait_for(lambda: self.file_chooser.get_files().get_n_items() == 1))
            self.generation += 1
            self.file_chooser.emit("response", Gtk.ResponseType.ACCEPT)
            self.check("stale file chooser cannot queue across sessions", not recorder.calls and self.modal_kind != "files")
            self.state.mode = "live"
            self.download_file({"chat_id": self.state.selected, "id": "old-file", "filename": "old.txt"})
            self.state.mode = real_mode
            self.download_chooser.set_file(fixture)
            self.check("stale download test has an actual selected file", self.wait_for(lambda: self.download_chooser.get_file() is not None))
            self.generation += 1
            self.download_chooser.emit("response", Gtk.ResponseType.ACCEPT)
            self.check("stale download chooser cannot act across sessions", not recorder.calls)
            self.open_stickers()
            callback = recorder.calls.pop()[2]
            self.generation += 1
            callback({"ok": True, "result": {"packs": []}})
            self.check("stale sticker catalog cannot open after account changes", self.modal_kind != "stickers")
            self.open_stickers()
            callback = recorder.calls.pop()[2]
            callback({"ok": True, "result": {"packs": [{"id": "demo", "name": "Demo", "stickers": [{"id": "bear-1", "url": "demo:bear-1"}]}]}})
            self.sticker_choices[0].emit("clicked")
            self.generation += 1
            self.sticker_send_button.emit("clicked")
            self.check("stale sticker button cannot send across sessions", not recorder.calls)
            self.edit_note(self.current_chat())
            dialogs = tuple(self.dialogs)
            self.clear_session()
            self.check("logout closes modals and account dialogs", self.modal is None and not self.dialogs and all(not dialog.get_visible() for dialog in dialogs))
        finally:
            self.core = real_core
            self.state.mode = real_mode

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


def padded_label(text):
    widget = label(text, "subtle", wrap=True)
    widget.set_margin_top(14)
    return widget
