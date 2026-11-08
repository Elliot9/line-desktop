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
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from . import art
from . import avatar as avatar_module
from . import ui as ui_module
from .art import Art, BASIC_EMOJI, MOODS
from .ipc import Core
from .model import KEEP_ID, Keep, Notes, Preferences, State, media_label
from .pictures import Pictures
from .ui import (H, V, Responsive, box, button, chat_avatar, clear, dot, file_badge, file_size,
                 icon, icon_button, icon_text, label, person_avatar, scroller, set_accessible_label)

ASSETS = Path(__file__).with_name("assets")
# Core messages that only mean "you are on the login page"; not shown as errors.
LOGIN_ROUTINE = {"請使用手機 LINE 掃描 QR code 登入。", "已取消登入。", "已登出。"}
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


class InlineEmoji(GObject.Object, Gdk.Paintable):
    """A LINE emoji (sticon) placed in the composer: drawn at text size and
    carrying the reference the core needs to send it."""

    def __init__(self, texture, size, ref):
        super().__init__()
        self.texture, self.size, self.ref = texture, size, ref

    def do_snapshot(self, snapshot, width, height):
        self.texture.snapshot(snapshot, width, height)

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_get_flags(self):
        return Gdk.PaintableFlags.SIZE | Gdk.PaintableFlags.CONTENTS


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
        avatar_module.LOADER = self.pictures
        ui_module.PEOPLE = self.state.avatar_of
        self.window = None
        self.section = "chats"
        self.filter = "all"
        self.detail_tab = "info"
        self.details_open = False
        self.details_auto = True
        self.message_search = ""
        self.persistent = None
        self.restoring = False
        self.modal = None
        self.modal_kind = ""
        self.popover_message = None
        self.collapsed = {"official"}
        self.stick_bottom = True
        self.recent_stickers = [] if self.options.demo else list(self.prefs.settings["recent_stickers"])
        self.snap_pending = False
        self.pending_images = []
        self.image_textures = {}
        self.image_bytes = {}
        self.replying = None
        self.screenshot_signal = 0
        self.context_popover = None
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
        # Window/dock icon: the installed launcher icon is named after the
        # application id; the bundled SVG covers runs without --install-menu.
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon_dir = GLib.get_user_cache_dir() + "/matrix-line/icons"
        try:
            if self.options.smoke_test:
                raise OSError("smoke runs use a throwaway application id")
            Path(icon_dir).mkdir(parents=True, exist_ok=True)
            target = Path(icon_dir) / f"{self.get_application_id()}.svg"
            source = (ASSETS / "app-icon.svg").read_bytes()
            if not target.exists() or target.read_bytes() != source:
                target.write_bytes(source)
            theme.add_search_path(icon_dir)
        except OSError:
            pass
        Gtk.Window.set_default_icon_name(self.get_application_id())
        for sheet in ("style.css", "dark.css"):
            provider = Gtk.CssProvider()
            provider.load_from_path(str(ASSETS / sheet))
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.add_css_class("line-window")
        self.system_dark = False
        self.watch_color_scheme()
        self.apply_theme()
        self.window.add_css_class(f"font-{self.prefs.settings['font_size']}")
        self.window.set_title("LINE" + (" · 離線展示" if self.options.demo else ""))
        self.window.set_default_size(1100, 760)
        self.window.set_size_request(760, 520)
        self.window.set_titlebar(self.build_titlebar())
        # Closing the window keeps LINE running so messages and notifications
        # still arrive (Ctrl+Q or 「結束 LINE」 quits). Demo mode simply exits.
        self.window.set_hide_on_close(not self.options.demo)
        self.window.connect("notify::is-active", self.focus_changed)
        for name, handler, accels in (("quit", lambda *_: self.quit(), ["<Control>q"]),
                                      ("open-chat", self.open_chat_action, [])):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s") if name == "open-chat" else None)
            action.connect("activate", handler)
            self.add_action(action)
            if accels:
                self.set_accels_for_action("app." + name, accels)
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
        self.show_restoring(not self.options.demo)
        self.window.present()
        try:
            self.core = Core(self.options.demo, self.dispatch, self.on_event)
            self.core.request("initialize", callback=self.initialized)
        except OSError:
            self.show_restoring(False)
            self.toast("無法啟動 LINE 核心。請先建置程式，或確認 LINE_APP_CORE 指向可執行檔。", True)
            self.login_message("核心尚未啟動。請關閉後重新開啟程式。")
        if self.options.smoke_test:
            GLib.timeout_add(250, self.smoke_tick)

    def open_chat_action(self, _action, value):
        chat_id = value.get_string()
        if self.window:
            self.window.present()
            if chat_id in self.state.chats:
                self.navigate("chats")
                self.select_chat(chat_id)

    def initialized(self, response):
        if not response.get("ok"):
            self.show_restoring(False)
            self.toast(self.error(response), True)
            return
        result = response.get("result", {})
        self.state.mode = result.get("mode", self.state.mode)
        self.state.capabilities = result.get("capabilities", {})
        self.update_capabilities()

    def build_titlebar(self):
        header = Gtk.HeaderBar()
        header.add_css_class("titlebar")
        # Follow the desktop's button layout (GNOME: ":close", KDE etc.).
        header.set_title_widget(label("LINE", "titlebar-name"))
        if self.options.demo:
            pill = box(H, 8, "demo-pill", valign=CENTER)
            pill.append(dot())
            self.mode_label = label("離線展示 · 不連接 LINE")
            pill.append(self.mode_label)
            header.pack_end(pill)
        else:
            self.mode_label = label("", "titlebar-end")
            header.pack_end(self.mode_label)
        return header

    # ------------------------------------------------------------------ login
    # Mirrors the official LINE desktop login window: e-mail form on the left,
    # a QR code that is generated automatically on the right.
    def build_login(self):
        page = box(H, 0, "login")
        left = box(V, 0, "login-main", hexpand=True)
        self.login_left = Gtk.Stack(vexpand=True)
        left.append(self.login_left)

        form = box(V, 0, halign=CENTER, valign=CENTER)
        form.set_size_request(300, -1)
        form.append(label("LINE", "login-logo", xalign=0.5))
        fields = box(V, 0, "login-fields")
        self.email_entry = Gtk.Entry(placeholder_text="電子郵件帳號")
        self.email_entry.set_input_purpose(Gtk.InputPurpose.EMAIL)
        self.email_entry.connect("activate", lambda *_: self.password_entry.grab_focus())
        self.email_entry.connect("changed", lambda *_: self.update_login_button())
        fields.append(self.email_entry)
        fields.append(Gtk.Separator())
        self.password_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.password_entry.set_property("placeholder-text", "密碼")
        self.password_entry.connect("activate", lambda *_: self.start_email())
        self.password_entry.connect("changed", lambda *_: self.update_login_button())
        fields.append(self.password_entry)
        form.append(fields)
        self.email_button = button(None, self.start_email, "login-submit", text="登入")
        form.append(self.email_button)
        options = box(H, 0, "login-options")
        self.auto_login = Gtk.CheckButton(label="自動登入", active=self.prefs.settings["auto_login"])
        self.auto_login.add_css_class("login-check")
        self.auto_login.set_hexpand(True)
        self.auto_login.connect("toggled", lambda check: self.prefs.save_settings(auto_login=check.get_active()))
        options.append(self.auto_login)
        options.append(button(None, self.reset_password, "login-link", text="重設密碼"))
        form.append(options)
        self.auth_status = label("", "login-status", wrap=True, xalign=0.5)
        self.auth_status.set_visible(False)
        form.append(self.auth_status)
        self.login_left.add_named(form, "form")

        pin = box(V, 0, "login-pin-page", halign=CENTER, valign=CENTER)
        pin.set_size_request(300, -1)
        pin.append(label("LINE", "login-logo", xalign=0.5))
        pin.append(label("請在手機上輸入驗證碼", "login-qr-title", xalign=0.5))
        self.pin_label = label("", "pin", xalign=0.5)
        pin.append(self.pin_label)
        self.pin_hint = label("請在 3 分鐘內於手機 LINE 輸入上方驗證碼。", "login-qr-text", wrap=True, xalign=0.5)
        pin.append(self.pin_hint)
        self.cancel_login = button(None, self.cancel_auth, "login-cancel", text="取消")
        self.cancel_login.set_halign(CENTER)
        pin.append(self.cancel_login)
        self.login_left.add_named(pin, "pin")

        busy = box(V, 16, halign=CENTER, valign=CENTER)
        busy.append(label("LINE", "login-logo", xalign=0.5))
        self.restore_spinner = Gtk.Spinner()
        self.restore_spinner.set_size_request(28, 28)
        busy.append(self.restore_spinner)
        self.busy_label = label("", "login-qr-text", xalign=0.5)
        busy.append(self.busy_label)
        self.login_left.add_named(busy, "busy")
        page.append(left)

        side = box(V, 0, "login-side")
        side.set_size_request(280, -1)
        side.set_hexpand(False)
        tools = box(H, 4, "login-tools", halign=END)
        tools.append(icon_button("help", self.open_help, "使用說明", 22, "login-tool"))
        menu_button = Gtk.MenuButton()
        menu_button.add_css_class("login-tool")
        menu_button.set_child(icon("menu", 22))
        menu_button.set_tooltip_text("選單")
        menu = Gtk.Popover(has_arrow=False)
        menu.add_css_class("line-popover")
        items = box(V, 0)
        for title, callback in (("使用說明", self.open_help), ("結束 LINE", self.quit)):
            items.append(button(label(title), lambda action=callback: (menu.popdown(), action()), None))
        menu.set_child(items)
        menu_button.set_popover(menu)
        tools.append(menu_button)
        side.append(tools)
        qr = box(V, 0, halign=CENTER, valign=CENTER, vexpand=True)
        qr.set_size_request(220, -1)
        self.qr_frame = Gtk.Overlay(halign=CENTER)
        self.qr_frame.add_css_class("login-qr")
        # Gtk.Image scales the paintable to pixel_size; Gtk.Picture would
        # grow to the PNG's own size.
        self.qr_picture = Gtk.Image(pixel_size=150)
        self.qr_picture.set_size_request(150, 150)
        self.qr_frame.set_child(self.qr_picture)
        self.qr_placeholder = Gtk.Spinner(halign=CENTER, valign=CENTER)
        self.qr_placeholder.set_size_request(28, 28)
        self.qr_frame.add_overlay(self.qr_placeholder)
        self.qr_button = button(None, self.start_qr, "login-qr-retry", text="重新產生")
        self.qr_button.set_halign(CENTER)
        self.qr_button.set_valign(CENTER)
        self.qr_button.set_visible(False)
        self.qr_frame.add_overlay(self.qr_button)
        qr.append(self.qr_frame)
        qr.append(label("使用行動條碼登入", "login-qr-title", xalign=0.5))
        hint = label("請在手機上開啟LINE應用程式，點選搜尋欄位旁的掃描圖示，再掃描此行動條碼。", "login-qr-text", wrap=True, xalign=0.5)
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_max_width_chars(16)
        hint.set_width_chars(16)
        qr.append(hint)
        side.append(qr)
        page.append(side)
        self.pages.add_named(page, "login")
        self.auth_method = None
        self.qr_failed = False
        self.reset_qr()
        self.update_login_button()

    def field(self, title, widget, css=""):
        wrapper = box(V, 0, "field " + css)
        wrapper.append(label(title, "field-label"))
        wrapper.append(widget)
        return wrapper

    def show_restoring(self, restoring):
        self.restoring = restoring
        self.show_login_busy("正在恢復上次的登入…" if restoring else None)

    def show_login_busy(self, text):
        """Left pane: a spinner with text, or back to the form when None."""
        busy = text is not None
        if busy:
            self.busy_label.set_text(text)
        self.restore_spinner.set_spinning(busy)
        self.login_left.set_visible_child_name("busy" if busy else "form")

    def login_message(self, text, error=True):
        self.auth_status.set_text(text or "")
        self.auth_status.set_visible(bool(text))
        if error:
            self.auth_status.add_css_class("error")
        else:
            self.auth_status.remove_css_class("error")

    def update_login_button(self):
        ready = bool(self.email_entry.get_text().strip() and self.password_entry.get_text())
        self.email_button.set_sensitive(ready)

    def reset_qr(self):
        """QR area: a spinner while a code is on its way, or 重新產生 after a failure."""
        self.qr_picture.clear()
        self.qr_placeholder.set_visible(not self.qr_failed)
        self.qr_placeholder.set_spinning(not self.qr_failed)
        self.qr_button.set_visible(self.qr_failed)

    def start_qr(self):
        if not self.core or self.restoring:
            return
        self.auth_method = "qr"
        self.qr_failed = False
        self.reset_qr()
        self.core.request("login.qr", {"remember": self.auto_login.get_active()}, self.auth_reply)

    def start_email(self):
        email = self.email_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not email or not password:
            self.login_message("請輸入電子郵件與密碼。")
            return
        if self.core:
            self.auth_method = "email"
            self.login_message("")
            self.show_login_busy("正在登入，請稍候…")
            self.reset_qr()
            self.core.request("login.email", {"email": email, "password": password, "remember": self.auto_login.get_active()}, self.auth_reply)
        self.password_entry.set_text("")

    def reset_password(self):
        self.login_message("請在手機 LINE 的「設定 › 帳號 › 密碼」重新設定密碼。", error=False)

    def auth_busy(self, busy):
        if not busy:
            self.show_login_busy(None)
            self.pin_label.set_text("")

    def auth_reply(self, response):
        if not response.get("ok"):
            self.auth_failed(self.error(response))

    def auth_failed(self, text):
        self.auth_busy(False)
        if self.auth_method == "qr":
            self.qr_failed = True
            self.reset_qr()
        self.login_message(text)

    def cancel_auth(self):
        self.password_entry.set_text("")
        if self.core:
            self.core.request("login.cancel", callback=self.auth_reply)
        self.auth_busy(False)
        self.login_message("")

    def login_ready_for_input(self):
        """Back on the login page: offer a fresh QR code like the official app."""
        self.auth_busy(False)
        if self.options.demo or self.restoring:
            return
        if self.qr_failed:
            self.reset_qr()
        else:
            self.start_qr()

    # -------------------------------------------------------------- workspace
    def build_workspace(self):
        self.app_box = Responsive(self.responsive, orientation=H)
        self.app_box.add_css_class("app")
        self.app_box.append(self.build_rail())
        self.app_box.append(self.build_sidebar())
        self.main_stack = Gtk.Stack()
        self.main_stack.set_hexpand(True)
        self.main_stack.add_named(self.build_chat_page(), "chat")
        welcome = box(V, 0, "welcome", vexpand=True, valign=CENTER, halign=CENTER)
        welcome.append(label("LINE", "welcome-mark", xalign=0.5))
        self.main_stack.add_named(welcome, "empty")
        self.app_box.append(self.main_stack)
        self.pages.add_named(self.app_box, "workspace")

    def build_rail(self):
        # LINE PC: a narrow navy strip with three filled glyphs (好友, 聊天,
        # 加入好友) on top and Keep, settings and "•••" at the bottom.
        rail = box(V, 6, "rail")
        rail.set_size_request(62, -1)
        self.nav_buttons = {}
        for key, name, title, accel in (("friends", "rail-friends", "好友", "Ctrl+1"), ("chats", "rail-chats", "聊天", "Ctrl+2"), ("add", "rail-add", "加入好友", "Ctrl+3")):
            layer = Gtk.Overlay()
            glyph = icon(name, 26)
            glyph.set_halign(CENTER)
            glyph.set_valign(CENTER)
            layer.set_child(glyph)
            item = button(layer, lambda value=key: self.navigate(value), "rail-btn", f"{title}（{accel}）")
            item.set_halign(CENTER)
            if key == "chats":
                self.navdot = label("", "navdot", xalign=0.5)
                self.navdot.set_halign(END)
                self.navdot.set_valign(START)
                self.navdot.set_visible(False)
                self.navdot.set_can_target(False)
                layer.add_overlay(self.navdot)
            self.nav_buttons[key] = item
            rail.append(item)
        rail.append(box(vexpand=True))
        keep = icon_button("bookmark", lambda: self.navigate("keep"), "Keep", 20, "rail-btn small")
        keep.set_halign(CENTER)
        self.nav_buttons["keep"] = keep
        rail.append(keep)
        self.more_button = Gtk.MenuButton()
        self.more_button.add_css_class("rail-btn")
        self.more_button.add_css_class("small")
        self.more_button.set_child(icon("dots", 20))
        self.more_button.set_tooltip_text("更多")
        self.more_button.set_halign(CENTER)
        self.more_button.set_direction(Gtk.ArrowType.RIGHT)
        menu = Gtk.Popover(has_arrow=False)
        menu.add_css_class("line-popover")
        items = box(V, 0)
        for title, callback in (("設定", self.open_settings), ("使用說明", self.open_help), ("登出", self.logout), ("結束 LINE", self.quit)):
            items.append(button(label(title), lambda action=callback: (menu.popdown(), action()), None))
        menu.set_child(items)
        self.more_button.set_popover(menu)
        rail.append(self.more_button)
        self.self_button = button(person_avatar("me", "me", 30, 16), lambda: self.open_profile(self.state.profile.get("id", "")), "rail-self", "我的個人檔案")
        self.self_button.set_visible(False)
        return rail

    def build_sidebar(self):
        self.sidebar = box(V, 0, "sidebar")
        self.sidebar.set_size_request(270, -1)
        top = box(V, 0, "side-top")
        title_row = box(H, 0, "side-title-row")
        self.side_title = label("聊天", "side-title")
        self.side_title.set_hexpand(True)
        title_row.append(self.side_title)
        self.side_action = icon_button("edit", self.side_button_clicked, "建立群組", 18)
        title_row.append(self.side_action)
        top.append(title_row)
        self.search = Gtk.Text(placeholder_text="搜尋聊天和訊息")
        self.search.set_hexpand(True)
        self.search.connect("changed", lambda *_: self.search_changed())
        top.append(self.searchbox(self.search))
        self.filter_row = box(H, 4)
        self.filter_row.set_margin_top(10)
        top.append(self.filter_row)
        self.sidebar.append(top)
        self.chat_list = box(V, 0)
        self.list_scroll = scroller(self.chat_list)
        self.sidebar.append(self.list_scroll)
        footer = box(H, 6, "side-footer")
        self.footer = footer
        footer.append(dot())
        self.connection = label("")
        footer.set_visible(False)
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
        titles = box(V, 2, valign=CENTER)
        title_line = box(H, 6)
        self.chat_title = label("讓對話，從這裡開始。", "chat-title", ellipsize=True, max_chars=26)
        title_line.append(self.chat_title)
        self.muted_tag = icon("belloff", 13, "muted-tag")
        self.muted_tag.set_visible(False)
        title_line.append(self.muted_tag)
        titles.append(title_line)
        subtitle = box(H, 6, "chat-subtitle")
        self.chat_subtitle = label("")
        subtitle.append(self.chat_subtitle)
        self.subtitle_row = subtitle
        titles.append(subtitle)
        heading.append(titles)
        header.append(heading)
        actions = box(H, 10, "header-actions", valign=CENTER)
        actions.append(icon_button("search", self.open_search, "搜尋對話", 18))
        self.call_buttons = [icon_button("phone", lambda: self.unsupported("此版本不提供語音與視訊通話，請使用手機或官方 LINE。"), "通話", 19)]
        for widget in self.call_buttons:
            actions.append(widget)
        self.details_toggle = icon_button("more-v", self.toggle_details, "選單", 19)
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
        vertical = self.history_scroll.get_vadjustment()
        vertical.connect("changed", self.history_resized)
        vertical.connect("value-changed", self.history_scrolled)
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
        self.details.set_visible(False)
        self.detail_contents = box(V, 0)
        self.details.append(scroller(self.detail_contents))
        split.append(self.details)
        page.append(split)
        return page

    def build_composer(self):
        # LINE PC: the text area sits on top; the toolbar (attach, Keep,
        # screenshot ▾ … emoji) runs along the bottom edge.
        composer = box(V, 0, "composer")
        self.reply_bar = box(H, 8, "reply-bar")
        self.reply_bar.set_visible(False)
        self.reply_text = label("", "reply-text", ellipsize=True)
        self.reply_text.set_hexpand(True)
        self.reply_bar.append(icon("reply", 14))
        self.reply_bar.append(self.reply_text)
        self.reply_bar.append(icon_button("x", self.cancel_reply, "取消回覆", 14))
        composer.append(self.reply_bar)
        self.tray = box(H, 8, "tray")
        self.tray.set_visible(False)
        composer.append(self.tray)
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
        compose_scroll.set_min_content_height(60)
        compose_scroll.set_max_content_height(160)
        compose_scroll.set_propagate_natural_height(True)
        compose_scroll.set_child(self.compose)
        layer = Gtk.Overlay()
        layer.set_child(compose_scroll)
        layer.set_hexpand(True)
        self.compose_placeholder = label("輸入訊息", "compose-placeholder")
        self.compose_placeholder.set_valign(START)
        self.compose_placeholder.set_halign(START)
        self.compose_placeholder.set_can_target(False)
        layer.add_overlay(self.compose_placeholder)
        composer.append(layer)
        toolbar = box(H, 4, "composer-toolbar")
        self.file_button = icon_button("clip", self.choose_files, "傳送檔案", 20)
        self.photo_button = icon_button("image", lambda: self.choose_files(images=True), "傳送照片", 20)
        self.keep_button = icon_button("bookmark", self.save_draft_to_keep, "儲存至 Keep", 19)
        self.screenshot_button = icon_button("crop", self.take_screenshot, "擷取畫面", 19)
        for widget in (self.file_button, self.photo_button, self.keep_button, self.screenshot_button):
            toolbar.append(widget)
        toolbar.append(box(hexpand=True))
        self.sticker_button = icon_button("smile", self.open_stickers, "貼圖、表情貼", 21)
        toolbar.append(self.sticker_button)
        self.sticker_popover = Gtk.Popover(has_arrow=False, autohide=True, position=Gtk.PositionType.TOP)
        self.sticker_popover.add_css_class("line-popover")
        self.sticker_popover.set_parent(self.sticker_button)
        self.sticker_popover.connect("closed", lambda *_: self.compose.grab_focus())
        send_icon = icon("send", 16)
        send_icon.set_halign(CENTER)
        send_icon.set_valign(CENTER)
        self.send_button = button(send_icon, self.send_text, "send", "傳送（Enter）")
        self.send_button.set_valign(CENTER)
        toolbar.append(self.send_button)
        composer.append(toolbar)
        self.composer_progress = label("", "composer-progress")
        self.composer_progress.set_visible(False)
        composer.insert_child_after(self.composer_progress, self.tray)
        return composer

    def save_draft_to_keep(self):
        draft = self.state.drafts.get(self.state.selected, "").strip()
        if not draft:
            self.select_chat(KEEP_ID)
            return
        self.keep.add(draft)
        self.state.chats.get(KEEP_ID, {}).update(self.keep.chat())
        self.toast("已儲存至 Keep")

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
            self.details_open = False
        self.details.set_visible(self.details_open)
        messages = window_width - 62 - side - (detail if self.details_open else 0) - 58
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
        if section == "add":
            self.unsupported("此版本無法加入好友。請在手機 LINE 以 ID、QR 碼或電話號碼加入後，這裡會自動出現。")
            return
        self.section = section
        self.filter = "all"
        self.search.set_text("")
        self.render_rail()
        self.render_sidebar()
        if section == "friends":
            # The friends page keeps the right pane: an open chat stays open.
            if not self.state.selected:
                self.main_stack.set_visible_child_name("empty")
        elif self.state.selected:
            self.main_stack.set_visible_child_name("chat")
        else:
            self.main_stack.set_visible_child_name("empty")

    def render_rail(self):
        active = "keep" if self.section == "chats" and self.state.selected == KEEP_ID else self.section
        for key, widget in self.nav_buttons.items():
            widget.add_css_class("active") if key == active else widget.remove_css_class("active")
            widget.queue_draw()
        unread = sum(int(chat.get("unread") or 0) for chat in self.state.chats.values() if not self.chat_muted(chat))
        self.navdot.set_text("99+" if unread > 99 else str(unread))
        self.navdot.set_visible(unread > 0)

    def chat_muted(self, chat):
        return bool(chat.get("muted")) or self.prefs.has("muted", chat.get("id"))

    def render_sidebar(self):
        friends = self.section == "friends"
        self.side_title.set_text("好友" if friends else "聊天")
        self.side_action.set_child(icon("plus" if friends else "edit", 18))
        self.side_action.set_tooltip_text("加入好友" if friends else "建立群組")
        self.search.set_placeholder_text("以姓名搜尋" if friends else "搜尋聊天和訊息")
        clear(self.filter_row)
        self.filter_row.set_visible(not friends)
        options = [] if friends else [("all", "全部"), ("friends", "好友"), ("group", "群組"), ("unread", "未讀")]
        for value, title in options:
            widget = button(label(title), lambda value=value: self.set_filter(value), "filter")
            if value == self.filter:
                widget.add_css_class("active")
            self.filter_row.append(widget)
        self.render_list()

    def set_filter(self, value):
        self.filter = value
        self.render_sidebar()

    def search_changed(self):
        self.render_list()

    def side_button_clicked(self):
        if self.section == "friends":
            self.unsupported("此版本尚不支援新增好友。請在手機 LINE 加入後重新整理。")
        else:
            self.unsupported("此版本尚不支援建立群組。請在手機 LINE 建立後重新整理。")

    def visible_friends(self):
        query = self.search.get_text().casefold().strip()
        rows = [c for c in self.state.contacts.values() if query in str(c.get("name", "")).casefold()]
        if self.filter == "favorite":
            rows = [c for c in rows if c.get("favorite") or self.prefs.has("favorites", c["id"])]
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
            if self.filter == "friends" and (chat.get("kind") != "direct" or chat["id"] not in self.state.contacts):
                continue
            # LINE lists only conversations that contain messages.
            if chat.get("has_messages") is False and not self.state.messages.get(chat["id"]) and chat["id"] != self.state.selected:
                continue
            rows.append(chat)
        rows.sort(key=lambda chat: (chat["id"] == KEEP_ID, -int(chat.get("updated_at", 0) or 0)))
        return rows

    def render_list(self):
        clear(self.chat_list)
        if self.section == "friends":
            self.render_friend_list()
            return
        rows = self.visible_chats()
        rows.sort(key=lambda chat: (not self.prefs.has("pinned", chat["id"]), chat["id"] == KEEP_ID, -int(chat.get("updated_at", 0) or 0)))
        for chat in rows:
            self.chat_list.append(self.chat_row(chat))
        if not rows:
            self.chat_list.append(label("沒有聊天" if not self.search.get_text() else "找不到符合的聊天", "empty", xalign=0.5))

    def render_friend_list(self):
        query = self.search.get_text().casefold().strip()
        own = self.state.profile
        if not query and own:
            self.chat_list.append(self.friend_row(own, profile=True))
        contacts = self.visible_friends()
        # Like the phone app, official accounts get their own section and do
        # not count as friends.
        friends = [c for c in contacts if not c.get("official")]
        official = [c for c in contacts if c.get("official")]
        favorites = [c for c in contacts if c.get("favorite") or self.prefs.has("favorites", c["id"])]
        groups = [c for c in self.state.chats.values() if c.get("kind") in ("group", "room") and query in str(c.get("name", "")).casefold()]
        groups.sort(key=lambda c: str(c.get("name", "")))
        for key, title, rows, builder in (("favorites", "我的最愛", favorites, self.friend_row),
                                          ("groups", "群組", groups, self.group_row),
                                          ("friends", "好友", friends, self.friend_row),
                                          ("official", "官方帳號", official, self.friend_row)):
            if not rows:
                continue
            collapsed = key in self.collapsed and not query
            self.chat_list.append(self.section_header(key, title, len(rows), collapsed))
            if not collapsed:
                for row in rows:
                    self.chat_list.append(builder(row))
        if not contacts and not groups:
            self.chat_list.append(label("找不到符合的好友" if query else "還沒有好友", "empty", xalign=0.5))

    def section_header(self, key, title, count, collapsed):
        contents = box(H, 6)
        name = label(f"{title} {count}")
        name.set_hexpand(True)
        contents.append(name)
        chevron = icon("chevron", 12)
        chevron.add_css_class("collapsed" if collapsed else "expanded")
        contents.append(chevron)
        return button(contents, lambda: self.toggle_section(key), "list-label", f"{title}，{count} 個")

    def toggle_section(self, key):
        self.collapsed ^= {key}
        self.render_list()

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
        contents.append(chat_avatar(chat, self.state.profile.get("id", ""), 48))
        info = box(V, 4, valign=CENTER, hexpand=True)
        info.set_size_request(80, -1)
        head = box(H, 4)
        title = chat.get("name", "未命名對話")
        if chat.get("kind") in ("group", "room"):
            title += f" ({self.member_count(chat)})"
        name = label(title, "chat-name", ellipsize=True)
        head.append(name)
        if self.chat_muted(chat):
            head.append(icon("belloff", 12, "muted-icon"))
        if self.prefs.has("pinned", chat["id"]):
            head.append(icon("pin", 12, "muted-icon"))
        spacer = box(hexpand=True)
        head.append(spacer)
        head.append(label(list_time(chat.get("updated_at", 0)), "chat-time"))
        info.append(head)
        bottom = box(H, 6)
        preview = label(str(chat.get("last_message") or "").replace("\n", " "), "chat-preview", ellipsize=True)
        preview.set_hexpand(True)
        bottom.append(preview)
        if chat.get("unread"):
            count = int(chat["unread"])
            badge = label("999+" if count > 999 else str(count), "unread" + (" muted" if self.chat_muted(chat) else ""), xalign=0.5)
            badge.set_valign(CENTER)
            bottom.append(badge)
        info.append(bottom)
        contents.append(info)
        title = chat.get("name", "對話") + (f"，{chat['unread']} 則未讀" if chat.get("unread") else "")
        row = button(contents, lambda value=chat["id"]: self.select_chat(value), "chat-row", title)
        row.set_tooltip_text(None)
        if chat["id"] == self.state.selected and self.section != "friends":
            row.add_css_class("active")
        if chat["id"] != KEEP_ID:
            self.attach_context(row, lambda: self.chat_menu(chat))
        return row

    def chat_menu(self, chat):
        pinned = self.prefs.has("pinned", chat["id"])
        muted = self.chat_muted(chat)
        options = [("pin", "取消置頂" if pinned else "置頂", lambda: self.toggle_pref("pinned", chat)),
                   ("bell" if muted else "belloff", "開啟通知" if muted else "關閉通知", lambda: self.toggle_pref("muted", chat))]
        if chat.get("unread"):
            options.append(("check", "標為已讀", lambda: self.read_chat(chat)))
        if self.state.capabilities.get("chat_delete", True):
            options.append(("trash", "刪除聊天", lambda: self.confirm_delete_chat(chat)))
        return options

    def read_chat(self, chat):
        """Explicit 「標為已讀」 from the list menu sends the read receipt."""
        history = self.state.messages.get(chat["id"]) or []
        chat["unread"] = 0
        if self.core and chat["id"] != KEEP_ID and history:
            self.core.request("messages.read", {"chat_id": chat["id"], "message_id": history[-1]["id"]})
        self.render_list()
        self.render_rail()

    def confirm_delete_chat(self, chat):
        body = label("刪除後，這個聊天室的所有訊息將從這台電腦和你的帳號中移除，對方的紀錄不受影響。", "subtle", wrap=True)

        def delete():
            self.close_modal()
            if not self.core:
                return
            def done(response):
                if not response.get("ok"):
                    self.toast(self.error(response), True)
                    return
                self.state.messages.pop(chat["id"], None)
                self.state.chats.pop(chat["id"], None)
                if self.state.selected == chat["id"]:
                    self.state.selected = None
                    self.main_stack.set_visible_child_name("empty")
                self.render_list()
                self.render_rail()
            self.core.request("chats.delete", {"chat_id": chat["id"]}, done)
        self.open_modal("delete-chat", "刪除聊天", body, self.modal_actions(delete, "刪除"))

    def friend_row(self, contact, profile=False):
        contents = box(H, 12)
        contents.append(person_avatar(contact.get("id"), self.state.profile.get("id", ""), 52 if profile else 40))
        info = box(V, 3, valign=CENTER, hexpand=True)
        info.append(label(contact.get("name", "好友"), "chat-name" + (" profile" if profile else ""), ellipsize=True))
        status = label(contact.get("status") or "", "chat-preview", ellipsize=True)
        status.set_visible(bool(contact.get("status")))
        info.append(status)
        contents.append(info)
        row = button(contents, None, "chat-row friend" + (" own-profile" if profile else ""), contact.get("name", "好友"))
        row.set_tooltip_text(None)
        person_id = contact.get("id", "")
        # LINE PC: one click shows the profile, a double click opens the chat.
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda gesture, n, x, y: self.open_direct(person_id) if n >= 2 and not profile else (self.open_profile(person_id) if n == 1 else None))
        row.add_controller(click)
        if not profile:
            self.attach_context(row, lambda: self.friend_menu(contact))
        return row

    def group_row(self, chat):
        contents = box(H, 12)
        contents.append(chat_avatar(chat, self.state.profile.get("id", ""), 40))
        name = label(f"{chat.get('name', '群組')} ({self.member_count(chat)})", "chat-name", ellipsize=True)
        name.set_valign(CENTER)
        name.set_hexpand(True)
        contents.append(name)
        row = button(contents, None, "chat-row friend", chat.get("name", "群組"))
        row.set_tooltip_text(None)
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda gesture, n, x, y: self.select_chat(chat["id"]) if n >= 1 else None)
        row.add_controller(click)
        return row

    def friend_menu(self, contact):
        favorite = self.prefs.has("favorites", contact["id"])
        return [("chat", "聊天", lambda: self.open_direct(contact["id"])),
                ("star", "從我的最愛中移除" if favorite else "加到我的最愛", lambda: self.toggle_favorite(contact["id"], reopen=False)),
                ("info", "個人檔案", lambda: self.open_profile(contact["id"]))]

    def attach_context(self, widget, options):
        """Right click (or long press on touch) shows a LINE-style menu."""
        def show(x, y):
            entries = options()
            if not entries:
                return
            popover = Gtk.Popover(has_arrow=False)
            popover.add_css_class("line-popover")
            contents = box(V, 0)
            for glyph, title, callback in entries:
                contents.append(button(icon_text(glyph, title, 14, 9), lambda action=callback: (popover.popdown(), action()), "danger" if glyph == "trash" else None))
            popover.set_child(contents)
            popover.set_parent(widget)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.set_position(Gtk.PositionType.BOTTOM)
            popover.connect("closed", lambda *_: GLib.idle_add(lambda: popover.unparent() if popover.get_parent() else None))
            self.context_popover = popover
            popover.popup()
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda gesture, n, x, y: show(x, y))
        widget.add_controller(click)
        press = Gtk.GestureLongPress(touch_only=True)
        press.connect("pressed", lambda gesture, x, y: show(x, y))
        widget.add_controller(press)

    # --------------------------------------------------------------- chat view
    def select_chat(self, chat_id):
        if chat_id == KEEP_ID and KEEP_ID not in self.state.chats:
            return
        if chat_id not in self.state.chats:
            return
        # Opening a chat from the friends page keeps the friends list, as LINE does.
        if self.section != "friends" or chat_id == KEEP_ID:
            self.section = "chats"
        self.state.selected = chat_id
        chat = self.state.chats[chat_id]
        chat["unread"] = 0
        self.detail_tab = "info"
        self.close_search()
        if getattr(self, "tray", None) is not None:
            self.clear_tray()
            self.cancel_reply()
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
        self.header_avatar_slot.append(chat_avatar(chat, self.state.profile.get("id", ""), 36))
        self.chat_title.set_text(chat.get("name", "對話") if chat else "讓對話，從這裡開始。")
        if chat and chat.get("kind") in ("group", "room"):
            self.chat_title.set_text(f"{chat.get('name', '群組')} ({self.member_count(chat)})")
        self.muted_tag.set_visible(bool(chat and self.chat_muted(chat)))
        unsealed = bool(chat and self.chat_unencrypted(chat["id"]))
        self.chat_subtitle.set_text("🔓 此聊天未加密" if unsealed else "")
        self.chat_subtitle.set_tooltip_text("對方未啟用 Letter Sealing（官方帳號或超大群組），文字訊息以未加密方式傳送。" if unsealed else None)
        self.subtitle_row.set_visible(unsealed)
        for widget in self.call_buttons:
            widget.set_visible(not chat or chat["id"] != KEEP_ID)

    def chat_unencrypted(self, chat_id):
        # The latest text message decides: LINE only sends text unsealed when
        # the chat has no Letter Sealing keys.
        for item in reversed(self.history_for(chat_id)):
            if item.get("kind") == "text" and not str(item.get("id", "")).startswith("pending-"):
                return bool(item.get("unencrypted"))
        return False

    def history_for(self, chat_id):
        return self.state.messages.get(chat_id, [])

    def render_messages(self):
        clear(self.history)
        chat = self.current_chat()
        history = self.history_for(self.state.selected) if chat else []
        query = self.message_search.casefold()
        matches = 0
        first_hit = None
        by_id = {m.get("id"): m for m in history}
        if not history:
            self.history.append(label("沒有訊息" if chat else "", "empty", xalign=0.5))
        day = None
        previous = None
        for message in history:
            moment = day_label(message.get("time", 0))
            if moment != day:
                day = moment
                previous = None
                self.history.append(self.date_divider(moment))
            hit = bool(query and query in str(message.get("text", "")).casefold())
            matches += hit
            # LINE groups consecutive messages by one sender within a minute
            # or so: only the first shows the avatar and name.
            grouped = bool(previous and previous.get("kind") != "system" and message.get("kind") != "system"
                           and previous.get("mine") == message.get("mine") and previous.get("sender_id") == message.get("sender_id")
                           and abs(int(message.get("time", 0)) - int(previous.get("time", 0))) < 5 * 60 * 1000)
            row, bubble = self.message_widget(message, chat, hit, grouped, by_id)
            if hit and first_hit is None:
                first_hit = bubble
            self.history.append(row)
            previous = message
        self.search_count.set_text(f"{matches} 則結果" if self.message_search else "")
        if first_hit is not None:
            GLib.idle_add(self.scroll_to, first_hit)
        else:
            GLib.idle_add(self.scroll_bottom)

    def date_divider(self, text):
        pill = label(text, "date-divider", xalign=0.5)
        pill.set_halign(CENTER)
        return pill

    def read_label(self, message, chat):
        """已讀 for a direct chat, 已讀 N for groups (op 55 receipts)."""
        if not chat or chat["id"] == KEEP_ID or message.get("status") in ("pending", "failed"):
            return ""
        readers = chat.get("read_by") or {}
        stamp = int(message.get("time", 0))
        count = sum(1 for reader, at in readers.items() if reader != self.state.profile.get("id") and int(at or 0) >= stamp)
        if not count:
            return ""
        return "已讀" if chat.get("kind") == "direct" else f"已讀 {count}"

    def message_widget(self, message, chat, highlighted=False, grouped=False, by_id=None):
        own = bool(message.get("mine"))
        kind = message.get("kind") or "text"
        if kind == "system" or message.get("unsent"):
            text = message.get("text") or "系統訊息"
            if message.get("unsent"):
                who = "你" if own else (message.get("sender_name") or self.state.name_of(message.get("sender_id")))
                text = f"{who}已收回訊息"
            notice = label(text, "system-notice", wrap=True, xalign=0.5, max_chars=self.bubble_chars)
            notice.set_halign(CENTER)
            wrapper = box(V, 0, "message system")
            wrapper.append(notice)
            return wrapper, notice
        row = box(H, 8, "message" + (" own" if own else "") + (" grouped" if grouped else ""), halign=END if own else START)
        if not own:
            if grouped:
                spacer = box()
                spacer.set_size_request(36, -1)
                row.append(spacer)
            else:
                face = person_avatar(message.get("sender_id"), self.state.profile.get("id", ""), 36)
                face_button = button(face, lambda value=message.get("sender_id"): self.open_profile(value), "avatar-button", "個人檔案")
                face_button.set_valign(START)
                row.append(face_button)
        main = box(V, 0)
        if not own and not grouped and chat and chat.get("kind") in ("group", "room"):
            main.append(label(message.get("sender_name") or self.state.name_of(message.get("sender_id")), "message-sender"))
        line = box(H, 6)
        bubble = self.bubble(message, kind, by_id or {})
        if highlighted:
            bubble.add_css_class("search-highlight")
        if not grouped and kind == "text":
            bubble.add_css_class("tail")
        meta = box(V, 0, "message-meta", valign=END)
        if own and chat and chat["id"] != KEEP_ID:
            status = message.get("status")
            if status == "pending":
                meta.append(label("傳送中", "read", xalign=1))
            elif status == "failed":
                meta.append(label("傳送失敗", "failed", xalign=1))
            else:
                read = self.read_label(message, chat)
                if read:
                    meta.append(label(read, "read", xalign=1))
        meta.append(label(clock(message.get("time", 0)), xalign=1 if own else 0))
        parts = [bubble, meta]
        for widget in (reversed(parts) if own else parts):
            line.append(widget)
        main.append(line)
        row.append(main)
        self.attach_context(bubble, lambda item=message: self.message_menu(item, chat))
        return row, bubble

    def emoji_text(self, message):
        """Builds a TextView-free paragraph with LINE emoji (sticons) inline.
        Text with no emoji stays a selectable label."""
        text = str(message.get("text") or "")
        runs = message.get("emoji") or []
        if not runs:
            return None
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, column_spacing=0, row_spacing=0, max_children_per_line=40)
        flow.set_valign(START)
        chars = list(text)
        cursor = 0
        only_emoji = all(ch in "\ufffc \n" for ch in text)
        size = 44 if only_emoji and len(runs) <= 3 else 20
        for run in sorted(runs, key=lambda r: r.get("start", 0)):
            start, end = int(run.get("start", 0)), int(run.get("end", 0))
            if start > cursor:
                for word in "".join(chars[cursor:start]).split("\n"):
                    if word:
                        flow.insert(label(word, "emoji-text"), -1)
            flow.insert(self.sticker_picture(run.get("url", ""), size, size), -1)
            cursor = max(cursor, end)
        if cursor < len(chars):
            rest = "".join(chars[cursor:])
            if rest.strip():
                flow.insert(label(rest, "emoji-text"), -1)
        return flow, only_emoji

    def reply_quote(self, message, by_id):
        target = by_id.get(message.get("reply_to"))
        quote = box(V, 2, "reply-quote")
        if target:
            name = "你" if target.get("mine") else (target.get("sender_name") or self.state.name_of(target.get("sender_id")))
            quote.append(label(name, "reply-name", ellipsize=True))
            quote.append(label(self.summary(target), "reply-body", ellipsize=True, max_chars=30))
        else:
            quote.append(label("回覆的訊息", "reply-body"))
        return quote

    def bubble(self, message, kind, by_id=None):
        own = bool(message.get("mine"))
        url = str(message.get("url") or "")
        if kind == "sticker":
            holder = box(V, 0, "bubble sticker-bubble")
            if url.startswith("demo:bear-"):
                holder.append(Art("bear", url.rsplit("-", 1)[-1], 130, 117))
            else:
                holder.append(self.sticker_picture(url, 130, 130))
            return holder
        if kind == "image":
            return self.image_bubble(message)
        if kind == "file":
            holder = box(V, 0, "bubble file-bubble")
            attachment = box(H, 11, "attachment")
            attachment.set_size_request(215, -1)
            attachment.append(file_badge(message.get("filename")))
            names = box(V, 0, valign=CENTER, hexpand=True)
            names.append(label(message.get("filename") or "檔案", "file-name", wrap=True, max_chars=26))
            names.append(label(file_size(message.get("size")), "file-size"))
            attachment.append(names)
            download = icon_button("download", lambda item=message: self.download_file(item), "另存新檔", 15)
            download.set_valign(CENTER)
            attachment.append(download)
            holder.append(attachment)
            return holder
        holder = box(V, 4, "bubble")
        holder.set_halign(END if own else START)
        if message.get("reply_to"):
            holder.append(self.reply_quote(message, by_id or {}))
        built = self.emoji_text(message) if kind == "text" else None
        if built:
            flow, only_emoji = built
            if only_emoji and not message.get("reply_to"):
                holder.add_css_class("sticker-bubble")
            holder.append(flow)
            return holder
        text = message.get("text") or media_label(message)
        body = label(text, "bubble-text", wrap=True, max_chars=self.bubble_chars, selectable=True)
        holder.append(body)
        return holder

    def image_bubble(self, message):
        """Photo thumbnail sized from MEDIA_THUMB_INFO, loaded lazily through
        images.fetch (the core downloads and decrypts)."""
        width, height = int(message.get("width") or 0), int(message.get("height") or 0)
        if width <= 0 or height <= 0:
            width, height = 240, 180
        scale = min(240 / width, 240 / height, 1.0)
        width, height = max(80, int(width * scale)), max(60, int(height * scale))
        holder = box(V, 0, "bubble image-bubble")
        holder.set_overflow(Gtk.Overflow.HIDDEN)
        url = str(message.get("url") or "")
        if url == "demo:collage":
            grid = box(H, 2)
            grid.append(Art("scene", "mountain", 140, 180))
            side = box(V, 2)
            side.append(Art("scene", "trail", 98, 89))
            side.append(Art("scene", "coffee", 98, 89))
            grid.append(side)
            holder.append(grid)
        else:
            picture = Gtk.Picture()
            picture.set_size_request(width, height)
            picture.set_can_shrink(True)
            try:
                picture.set_content_fit(Gtk.ContentFit.COVER)
            except AttributeError:
                pass
            holder.append(picture)
            self.load_image(message, picture, preview=True)
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda *_: self.open_viewer(message))
        holder.add_controller(click)
        holder.set_cursor_from_name("pointer")
        return holder

    def load_image(self, message, picture, preview=False, done=None):
        url = str(message.get("url") or "")
        if url.startswith("file://"):
            texture = None
            try:
                texture = Gdk.Texture.new_from_filename(url[7:])
            except GLib.Error:
                pass
            if texture:
                picture.set_paintable(texture)
                if done:
                    done(texture)
            return
        key = (message.get("id"), preview)
        cached = self.image_textures.get(key) or self.image_textures.get((message.get("id"), False))
        if cached:
            picture.set_paintable(cached)
            if done:
                done(cached)
            return
        if not self.core or str(message.get("id", "")).startswith("pending-"):
            return

        def fetched(response):
            if not response.get("ok"):
                return
            try:
                data = base64.b64decode(response["result"]["data"])
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except (KeyError, ValueError, GLib.Error):
                return
            if len(self.image_textures) > 120:
                self.image_textures.pop(next(iter(self.image_textures)))
            self.image_textures[key] = texture
            if not preview:
                self.image_bytes[message.get("id")] = data
            picture.set_paintable(texture)
            if done:
                done(texture)
        self.core.request("images.fetch", {"chat_id": message.get("chat_id") or self.state.selected, "message_id": message.get("id"), "preview": preview}, fetched)

    def open_viewer(self, message):
        """LINE PC opens photos in their own window with save/copy."""
        if str(message.get("url") or "") == "demo:collage":
            self.open_lightbox(0)
            return
        viewer = Gtk.Window(title="照片", transient_for=self.window)
        viewer.set_default_size(820, 640)
        viewer.add_css_class("photo-viewer")
        layout = box(V, 0)
        picture = Gtk.Picture()
        picture.set_vexpand(True)
        picture.set_can_shrink(True)
        layout.append(picture)
        bar = box(H, 6, "viewer-bar", halign=CENTER)
        bar.append(button(icon_text("download", "儲存", 15), lambda: self.save_image(message, viewer), "viewer-btn"))
        bar.append(button(icon_text("copy", "複製", 15), lambda: self.copy_image(message), "viewer-btn"))
        bar.append(button(icon_text("forward", "分享", 15), lambda: (viewer.close(), self.open_forward(message)), "viewer-btn"))
        layout.append(bar)
        viewer.set_child(layout)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", lambda c, keyval, *_: (viewer.close(), True)[1] if keyval == Gdk.KEY_Escape else False)
        viewer.add_controller(key)
        self.load_image(message, picture, preview=False)
        viewer.present()

    def image_data(self, message):
        url = str(message.get("url") or "")
        if url.startswith("file://"):
            try:
                return Path(url[7:]).read_bytes()
            except OSError:
                return None
        return self.image_bytes.get(message.get("id"))

    def copy_image(self, message):
        texture = self.image_textures.get((message.get("id"), False)) or self.image_textures.get((message.get("id"), True))
        if texture is None and str(message.get("url") or "").startswith("file://"):
            try:
                texture = Gdk.Texture.new_from_filename(message["url"][7:])
            except GLib.Error:
                texture = None
        if texture is None:
            self.toast("照片尚未載入完成。")
            return
        # PyGObject has no Clipboard.set_texture; offer the texture itself
        # plus PNG bytes for apps that only accept image/png.
        providers = [Gdk.ContentProvider.new_for_value(texture)]
        try:
            providers.append(Gdk.ContentProvider.new_for_bytes("image/png", texture.save_to_png_bytes()))
        except (AttributeError, GLib.Error):
            pass
        self.window.get_clipboard().set_content(Gdk.ContentProvider.new_union(providers))
        self.toast("已複製照片")

    def save_image(self, message, parent=None):
        def write(data):
            dialog = Gtk.FileChooserNative.new("儲存照片", parent or self.window, Gtk.FileChooserAction.SAVE, "儲存", "取消")
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(int(message.get("time", 0)) / 1000))
            extension = "png" if data[:4] == b"\x89PNG" else ("gif" if data[:3] == b"GIF" else "jpg")
            dialog.set_current_name(f"LINE_{stamp}.{extension}")
            self.track_dialog(dialog)

            def chosen(chooser, response):
                if response == Gtk.ResponseType.ACCEPT and chooser.get_file():
                    try:
                        Path(chooser.get_file().get_path()).write_bytes(data)
                        self.toast("照片已儲存")
                    except OSError as error:
                        self.toast(f"無法儲存：{error.strerror}", True)
                chooser.destroy()
            dialog.connect("response", chosen)
            dialog.show()
        data = self.image_data(message)
        if data:
            write(data)
            return
        holder = Gtk.Picture()
        self.load_image(message, holder, preview=False, done=lambda texture: write(self.image_data(message) or bytes(texture.save_to_png_bytes().get_data())))

    def message_menu(self, message, chat):
        """Right-click / long-press menu (LINE PC order)."""
        options = []
        kind = message.get("kind") or "text"
        if message.get("unsent") or kind == "system":
            return options
        pending = str(message.get("id", "")).startswith("pending-")
        if kind == "text" and message.get("text"):
            options.append(("copy", "複製", lambda: self.copy_text(str(message.get("text", "")).replace("\ufffc", ""))))
        if kind == "image":
            options.append(("copy", "複製", lambda: self.copy_image(message)))
            options.append(("download", "另存新檔", lambda: self.save_image(message)))
        if kind == "file":
            options.append(("download", "另存新檔", lambda: self.download_file(message)))
        if chat and chat["id"] != KEEP_ID and not pending and self.state.capabilities.get("reply", True):
            options.append(("reply", "回覆", lambda: self.start_reply(message)))
        if not pending and kind in ("text", "image", "sticker"):
            options.append(("forward", "分享", lambda: self.open_forward(message)))
        if kind == "text" and chat and chat["id"] != KEEP_ID:
            options.append(("bookmark", "儲存至 Keep", lambda: self.keep_message(message)))
        if (message.get("mine") and chat and chat["id"] != KEEP_ID and not pending
                and time.time() * 1000 - int(message.get("time", 0)) < 24 * 3600 * 1000):
            options.append(("history", "收回", lambda: self.confirm_unsend(message)))
        if chat and chat["id"] == KEEP_ID:
            options.append(("trash", "刪除", lambda: self.delete_keep(message)))
        return options

    def keep_message(self, message):
        self.keep.add(str(message.get("text", "")).replace("\ufffc", ""))
        self.state.chats.get(KEEP_ID, {}).update(self.keep.chat())
        self.render_list()
        self.toast("已儲存至 Keep")

    def delete_keep(self, message):
        try:
            self.keep.remove(message.get("id"))
        except OSError:
            self.toast("無法刪除 Keep 筆記。", True)
            return
        self.state.chats.get(KEEP_ID, {}).update(self.keep.chat())
        self.state.messages[KEEP_ID] = [m for m in self.state.messages.get(KEEP_ID, []) if m.get("id") != message.get("id")]
        self.after_message(KEEP_ID)

    def confirm_unsend(self, message):
        body = label("收回後，所有人的聊天室都會顯示「已收回訊息」。", "subtle", wrap=True)

        def unsend():
            self.close_modal()
            def done(response):
                if not response.get("ok"):
                    self.toast(self.error(response), True)
                    return
                message["unsent"] = True
                self.after_message(message.get("chat_id") or self.state.selected)
            self.core.request("messages.unsend", {"chat_id": message.get("chat_id") or self.state.selected, "message_id": message["id"]}, done)
        self.open_modal("unsend", "要收回訊息嗎？", body, self.modal_actions(unsend, "收回"))

    def show_menu(self, source, message):
        options = self.message_menu(message, self.current_chat())
        if not options:
            return
        contents = box(V, 0)
        for glyph, title, callback in options:
            contents.append(button(icon_text(glyph, title, 14, 9), lambda action=callback: (self.menu.popdown(), action()), None))
        self.menu.set_child(contents)
        ok, bounds = source.compute_bounds(self.conversation)
        if ok:
            rect = Gdk.Rectangle()
            rect.x, rect.y = int(bounds.get_x()), int(bounds.get_y())
            rect.width, rect.height = int(bounds.get_width()), int(bounds.get_height())
            self.menu.set_pointing_to(rect)
        self.popover_message = source
        self.menu.popup()

    def menu_closed(self):
        self.popover_message = None

    def copy_text(self, text):
        self.window.get_clipboard().set(text)
        self.toast("已複製訊息")

    def scroll_bottom(self):
        adjustment = self.history_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        # Layout of new rows (pictures, wrapping) can grow the history after
        # this idle; stick to the bottom until the next frame settles.
        self.stick_bottom = True
        return GLib.SOURCE_REMOVE

    def history_resized(self, adjustment):
        # "changed" fires during size-allocate, where a new value is not
        # applied to the viewport; snap on the next idle instead.
        if self.stick_bottom and not self.snap_pending:
            self.snap_pending = True
            self.history_scroll.add_tick_callback(lambda *_: self.snap_bottom())

    def snap_bottom(self):
        self.snap_pending = False
        if self.stick_bottom:
            adjustment = self.history_scroll.get_vadjustment()
            bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
            # The value may already equal bottom while the viewport kept an
            # allocation from before the resize; force a real value change.
            adjustment.set_value(max(0, bottom - 1))
            adjustment.set_value(bottom)
        return GLib.SOURCE_REMOVE

    def history_scrolled(self, adjustment):
        bottom = adjustment.get_upper() - adjustment.get_page_size()
        self.stick_bottom = adjustment.get_value() >= bottom - 40

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
    def compose_content(self):
        """The composer's text with U+FFFC for each LINE emoji, and their refs."""
        buffer = self.compose.get_buffer()
        text = buffer.get_slice(buffer.get_start_iter(), buffer.get_end_iter(), False)
        refs = []
        if "\ufffc" in text:
            cursor = buffer.get_start_iter()
            while not cursor.is_end():
                if text[cursor.get_offset()] == "\ufffc":
                    refs.append(getattr(cursor.get_paintable(), "ref", None))
                cursor.forward_char()
        return text, refs

    @staticmethod
    def emoji_plain(text, refs):
        """Alt text in place of each emoji, as LINE shows them in plain text."""
        parts = text.split("\ufffc")
        if len(parts) != len(refs) + 1:
            return text.replace("\ufffc", "")
        return "".join(part + ((refs[i] or {}).get("text", "") if i < len(refs) else "") for i, part in enumerate(parts))

    def draft_changed(self, *_):
        text = self.emoji_plain(*self.compose_content())
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
        ready = bool(self.can_send_text() and (draft.strip() or self.pending_images))
        self.send_button.set_sensitive(ready)
        self.send_button.set_visible(ready)
        self.compose.set_editable(bool(self.state.selected))

    def composer_key(self, controller, keyval, keycode, modifiers):
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and modifiers & Gdk.ModifierType.CONTROL_MASK and self.can_attach():
            if self.paste_clipboard():
                return True
        if keyval == Gdk.KEY_Escape and self.replying:
            self.cancel_reply()
            return True
        if keyval == Gdk.KEY_BackSpace and self.pending_images and not self.state.drafts.get(self.state.selected):
            self.remove_image(len(self.pending_images) - 1)
            return True
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        if modifiers & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            return False
        # Enter sends (Shift+Enter breaks a line); with the option off, LINE
        # sends with Alt+Enter and plain Enter breaks a line.
        alt = bool(modifiers & Gdk.ModifierType.ALT_MASK)
        if alt == self.prefs.settings["enter_send"]:
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
        if not self.send_button.get_sensitive():
            return
        emoji = None
        if chat_id != KEEP_ID:
            raw, refs = self.compose_content()
            if refs and all(refs):
                text, emoji = raw, refs
        images = list(self.pending_images)
        reply = (self.replying or {}).get("id", "")
        if images and chat_id != KEEP_ID:
            self.clear_tray()
            self.send_images(chat_id, images)
        if text.strip():
            self.deliver_text(chat_id, text, from_composer=True, reply_to=reply, emoji=emoji)
        self.cancel_reply()
        self.update_send()

    def deliver_text(self, chat_id, text, from_composer=False, reply_to="", emoji=None):
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
        message = {"id": temporary, "chat_id": chat_id, "text": text, "mine": True, "time": int(time.time() * 1000), "kind": "text", "status": "pending", "reply_to": reply_to}
        plain = text
        if emoji:
            spots = [index for index, char in enumerate(text) if char == "\ufffc"]
            message["emoji"] = [{"start": at, "end": at + 1, "url": ref.get("url", "")} for at, ref in zip(spots, emoji)]
            plain = self.emoji_plain(text, emoji)
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
                    self.state.drafts[chat_id] = plain + ("\n" + self.state.drafts[chat_id] if self.state.drafts.get(chat_id) else "")
                    if self.state.selected == chat_id:
                        self.compose.get_buffer().set_text(self.state.drafts[chat_id])
                self.toast(self.error(response), True)
            self.after_message(chat_id)
        params = {"chat_id": chat_id, "text": text}
        if emoji:
            params["emoji"] = [{"package_id": ref["package_id"], "sticker_id": ref["sticker_id"]} for ref in emoji]
        if reply_to and not str(reply_to).startswith("pending-"):
            params["reply_to"] = reply_to
        self.core.request("messages.send", params, sent)

    def after_message(self, chat_id):
        if self.state.selected == chat_id and self.main_stack.get_visible_child_name() == "chat":
            self.render_header()
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

    def toggle_pref(self, key, chat=None):
        chat = chat or self.current_chat()
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
        if not chat or chat["id"] == KEEP_ID:
            caption = ""
        elif chat.get("kind") in ("group", "room"):
            caption = f"成員 {self.member_count(chat)}"
        else:
            caption = (self.state.contacts.get(chat["id"]) or {}).get("status") or ""
        if caption:
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
        card.set_overflow(Gtk.Overflow.HIDDEN)
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
        control = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
        if keyval in (Gdk.KEY_r, Gdk.KEY_R) and control:
            self.refresh()
            return True
        if control and not self.modal and self.pages.get_visible_child_name() == "workspace":
            # LINE PC shortcuts: Ctrl+1 好友, Ctrl+2 聊天, Ctrl+3 加入好友.
            sections = {Gdk.KEY_1: "friends", Gdk.KEY_2: "chats", Gdk.KEY_3: "add",
                        Gdk.KEY_KP_1: "friends", Gdk.KEY_KP_2: "chats", Gdk.KEY_KP_3: "add"}
            if keyval in sections:
                self.navigate(sections[keyval])
                return True
            if keyval in (Gdk.KEY_f, Gdk.KEY_F) and shift:
                self.search.grab_focus()
                return True
            if keyval in (Gdk.KEY_f, Gdk.KEY_F) and self.current_chat() and self.main_stack.get_visible_child_name() == "chat":
                self.open_search()
                return True
            if keyval in (Gdk.KEY_w, Gdk.KEY_W) and self.current_chat():
                self.close_chat()
                return True
            if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
                self.cycle_chat(-1 if shift or keyval == Gdk.KEY_ISO_Left_Tab else 1)
                return True
            if keyval in (Gdk.KEY_s, Gdk.KEY_S) and shift and self.current_chat():
                self.take_screenshot()
                return True
        if self.modal_kind == "lightbox" and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.open_lightbox(self.lightbox_index + (1 if keyval == Gdk.KEY_Right else -1))
            return True
        return False

    # ----------------------------------------------------------------- theme
    def watch_color_scheme(self):
        """螢幕顯示模式 follows the desktop: read the portal's color-scheme
        (1 = prefer dark) and listen for changes; fall back to GTK's own
        prefer-dark setting outside a portal session."""
        settings = Gtk.Settings.get_default()
        if settings is not None:
            self.system_dark = bool(settings.get_property("gtk-application-prefer-dark-theme")) or \
                str(settings.get_property("gtk-theme-name") or "").lower().endswith("dark")
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                  "org.freedesktop.portal.Settings", "ReadOne",
                                  GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                                  GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 500, None)
            self.system_dark = self.unwrap_variant(reply.unpack()[0]) == 1
            bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Settings", "SettingChanged",
                                 "/org/freedesktop/portal/desktop", None, Gio.DBusSignalFlags.NONE, self.color_scheme_changed)
        except GLib.Error:
            pass

    @staticmethod
    def unwrap_variant(value):
        while isinstance(value, GLib.Variant):
            value = value.unpack()
        return value

    def color_scheme_changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        namespace, key, value = parameters.unpack()
        if (namespace, key) == ("org.freedesktop.appearance", "color-scheme"):
            self.system_dark = self.unwrap_variant(value) == 1
            self.apply_theme()

    def apply_theme(self):
        mode = self.prefs.settings.get("theme", "system")
        dark = self.system_dark if mode == "system" else mode == "dark"
        self.window.add_css_class("dark") if dark else self.window.remove_css_class("dark")
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", dark)

    def close_chat(self):
        """Ctrl+W: close the open chat room (back to the blank pane)."""
        self.close_search()
        self.state.selected = None
        self.main_stack.set_visible_child_name("empty")
        self.render_list()

    def cycle_chat(self, step):
        """Ctrl+Tab / Ctrl+Shift+Tab: next or previous chat in list order."""
        rows = self.visible_chats()
        rows.sort(key=lambda chat: (not self.prefs.has("pinned", chat["id"]), chat["id"] == KEEP_ID, -int(chat.get("updated_at", 0) or 0)))
        if not rows:
            return
        ids = [chat["id"] for chat in rows]
        index = ids.index(self.state.selected) + step if self.state.selected in ids else (0 if step > 0 else -1)
        if self.section != "chats":
            self.navigate("chats")
        self.select_chat(ids[index % len(ids)])

    def open_help(self):
        body = box(V, 0)
        blocks = []
        if self.state.mode == "demo":
            blocks.append(("這是一份離線展示", "所有訊息、好友與貼圖都是示範資料，不會連線至 LINE，也不會傳給其他人。重新啟動程式即重設；本機記事仍會保存在這台電腦。"))
        blocks += [
            ("非官方的 LINE Linux 用戶端", "以 GTK 4 原生元件重現 LINE 桌面版的聊天體驗，透過內建通訊核心直接連線 LINE，不需要瀏覽器。文字與檔案使用 Letter Sealing 加密傳送。"),
            ("快捷鍵", "Ctrl+1 好友 · Ctrl+2 聊天 · Ctrl+3 加入好友 · Ctrl+Tab／Ctrl+Shift+Tab 切換聊天室 · Ctrl+Shift+F 搜尋列表 · Ctrl+F 搜尋聊天室 · Ctrl+W 關閉聊天室 · Ctrl+Shift+S 擷取畫面 · Ctrl+V 貼上圖片 · Ctrl+R 重新整理 · Ctrl+Q 結束 LINE"),
            ("保存在哪裡", "登入憑證交給系統鑰匙圈保管，密碼不會儲存。記事、Keep 筆記與偏好設定只保存在這台電腦，不會同步到 LINE。"),
            ("目前的限制", "此版本不提供語音與視訊通話、加入好友、LINE 記事本與相簿、社群、編輯訊息與訊息回應，也無法載入更早的聊天記錄。"),
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
        for key, title in (("notifications", "訊息通知"), ("enter_send", "按 Enter 傳送訊息（關閉時改按 Alt+Enter）")):
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
        themes = [("system", "跟隨系統"), ("light", "淺色"), ("dark", "深色")]
        theme_choice = Gtk.DropDown.new_from_strings([title for _, title in themes])
        theme_choice.set_selected([value for value, _ in themes].index(self.prefs.settings.get("theme", "system")))
        body.append(self.field("螢幕顯示模式", theme_choice))
        self.theme_choice = theme_choice
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
        self.logout_button.set_sensitive(self.state.session != "logged_out" and self.state.mode != "demo")
        controls.append(self.logout_button)
        account.append(controls)
        body.append(account)

        def save():
            try:
                font = sizes[size_choice.get_selected()][0]
                self.window.remove_css_class(f"font-{self.prefs.settings['font_size']}")
                self.prefs.save_settings(notifications=switches["notifications"].get_active(), enter_send=switches["enter_send"].get_active(), font_size=font,
                                         theme=themes[theme_choice.get_selected()][0])
                self.window.add_css_class(f"font-{font}")
                self.apply_theme()
            except OSError:
                self.toast("無法儲存設定。請確認資料目錄的權限。", True)
                return
            self.close_modal()
            self.render_header()
            self.toast("設定已儲存")
        self.settings_save = save
        self.open_modal("settings", "設定", body, self.modal_actions(save, "儲存設定", "關閉"))

    def open_profile(self, person_id):
        """LINE profile card: cover, large round photo, name, status message
        and a row of round actions underneath."""
        own = self.state.profile.get("id", "")
        me = bool(person_id) and person_id == own
        contact = self.state.profile if me else (self.state.contacts.get(person_id) or self.state.people.get(person_id))
        friend = me or person_id in self.state.contacts
        if not contact:
            if person_id in self.state.chats:
                contact = {"id": person_id, "name": self.state.chats[person_id].get("name", "成員"), "status": ""}
            else:
                self.toast("無法取得這位成員的個人檔案。")
                return
        card = box(V, 0, "profile-card")
        top = Gtk.Overlay()
        base = box(V, 0)
        base.append(box(V, 0, "profile-cover"))
        base.append(box(V, 0, "profile-cover-gap"))
        top.set_child(base)
        portrait = person_avatar(person_id or "me", own or "me", 104)
        portrait.set_halign(CENTER)
        portrait.set_valign(Gtk.Align.END)
        portrait.add_css_class("profile-photo")
        top.add_overlay(portrait)
        card.append(top)
        card.append(label(contact.get("name", "LINE"), "profile-name", xalign=0.5, wrap=True))
        status = contact.get("status") or ("" if not me else "把日子過成喜歡的樣子。")
        if status:
            card.append(label(status, "profile-status", xalign=0.5, wrap=True))
        actions = box(H, 28, "profile-actions", halign=CENTER)

        def action(name, text, callback):
            item = box(V, 6)
            circle = box(V, 0, "profile-action-icon", halign=CENTER)
            circle.append(icon(name, 22))
            item.append(circle)
            item.append(label(text, "profile-action-label", xalign=0.5))
            actions.append(button(item, callback, "profile-action", text))
        if me:
            action("chat", "Keep 筆記", lambda: (self.close_modal(), self.select_chat(KEEP_ID)))
            action("settings", "設定", lambda: (self.close_modal(), self.open_settings()))
        elif friend:
            action("chat", "聊天", lambda: self.open_direct(person_id))
            action("phone", "語音通話", lambda: self.unsupported("此版本不提供語音通話。"))
            favorite = self.prefs.has("favorites", person_id)
            action("star", "已加入最愛" if favorite else "加入最愛", lambda: self.toggle_favorite(person_id))
        else:
            notice = label("對方不在你的好友名單中。", "profile-status", xalign=0.5)
            notice.set_margin_top(6)
            card.append(notice)
            action("rail-add", "加入好友", lambda: self.unsupported("此版本無法加入好友，請在手機 LINE 操作。"))
        card.append(actions)
        self.open_modal("profile", "", card, css="profile-modal")

    def toggle_favorite(self, person_id, reopen=True):
        try:
            enabled = self.prefs.toggle("favorites", person_id)
        except OSError:
            self.toast("無法儲存偏好設定。請確認資料目錄的權限。", True)
            return
        if reopen:
            self.open_profile(person_id)
        else:
            self.toast("已加到我的最愛" if enabled else "已從我的最愛中移除")
        self.render_list()

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

    def forward_targets(self):
        """Recent chats first, then friends without a chat yet (LINE 分享)."""
        own = self.state.profile.get("id", "")
        seen, targets = set(), []
        for chat in sorted(self.state.chats.values(), key=lambda item: -int(item.get("updated_at") or 0)):
            # Groups are listed even without recent talk: they are all
            # current memberships (the core drops groups the user left).
            if chat["id"] in seen or (chat["id"] != KEEP_ID and not chat.get("has_messages", True) and chat.get("kind") == "direct"):
                continue
            seen.add(chat["id"])
            targets.append({"id": chat["id"], "name": chat.get("name", "對話"), "chat": chat})
        for contact_id, contact in sorted(self.state.contacts.items(), key=lambda item: item[1].get("name", "")):
            if contact_id in seen or contact_id == own:
                continue
            seen.add(contact_id)
            targets.append({"id": contact_id, "name": contact.get("name", "好友"), "contact": True})
        return targets

    def open_forward(self, message):
        kind = message.get("kind", "text")
        if kind not in ("text", "image", "sticker"):
            self.toast("這類訊息目前無法分享。")
            return
        selected = []
        rows = []
        body = box(V, 0, "forward")
        search = Gtk.SearchEntry(placeholder_text="搜尋好友或聊天")
        search.add_css_class("sidebar-search")
        body.append(search)
        chosen = label("", "forward-chosen", ellipsize=True)
        chosen.set_visible(False)
        body.append(chosen)
        listing = box(V, 0)
        scroll = scroller(listing)
        scroll.set_size_request(-1, 340)
        body.append(scroll)
        actions = self.modal_actions(lambda: self.forward(message, [target for target in self.forward_targets() if target["id"] in selected]), "分享")
        share = actions.get_last_child().get_last_child()
        share.set_sensitive(False)

        def refresh():
            share.set_sensitive(bool(selected))
            share.get_child().set_text(f"分享（{len(selected)}）" if selected else "分享")
            names = [target["name"] for target in self.forward_targets() if target["id"] in selected]
            chosen.set_text("、".join(names))
            chosen.set_visible(bool(names))

        def toggle(target_id, check):
            if target_id in selected:
                selected.remove(target_id)
            elif len(selected) < 20:
                selected.append(target_id)
            check.set_active(target_id in selected)
            refresh()

        own = self.state.profile.get("id", "")
        for target in self.forward_targets():
            if target["id"] == KEEP_ID and kind != "text":
                continue
            row = box(H, 12)
            check = Gtk.CheckButton()
            check.set_can_target(False)
            check.set_valign(CENTER)
            row.append(check)
            picture = chat_avatar(target["chat"], own, 40, 15) if "chat" in target else person_avatar(target["id"], own, 40)
            row.append(picture)
            name = label(target["name"], ellipsize=True)
            name.set_hexpand(True)
            name.set_valign(CENTER)
            row.append(name)
            widget = button(row, None, "chat-row contact-choice")
            widget.connect("clicked", lambda _w, value=target["id"], box_=check: toggle(value, box_))
            widget.forward_id = target["id"]
            widget.forward_name = target["name"].lower()
            listing.append(widget)
            rows.append(widget)
        self.forward_rows = rows
        self.forward_share = share

        def filtered(entry):
            query = entry.get_text().strip().lower()
            for widget in rows:
                widget.set_visible(not query or query in widget.forward_name)
        search.connect("search-changed", filtered)
        self.open_modal("forward", "分享", body, actions)

    def forward(self, message, targets):
        if not targets:
            return
        if not all(target["id"] == KEEP_ID or self.can_send_text_to(target["id"]) for target in targets):
            self.toast("目前無法傳送訊息，請等待連線完成。", True)
            return
        kind = message.get("kind", "text")
        self.close_modal()
        for target in targets:
            chat_id = self.ensure_direct(target["id"]) if target.get("contact") else target["id"]
            if not chat_id:
                continue
            if kind == "text":
                self.deliver_text(chat_id, message.get("text", ""))
            elif kind == "sticker":
                self.core.request("messages.sticker", {"chat_id": chat_id, "package_id": message.get("package_id", ""), "sticker_id": message.get("sticker_id", "")},
                                  lambda result, cid=chat_id: self.toast(self.error(result), True) if not result.get("ok") else self.after_message(cid))
            elif kind == "image":
                self.forward_image(message, chat_id)
        self.toast(f"已分享給 {len(targets)} 個聊天" if len(targets) > 1 else f"已分享給「{targets[0]['name']}」")

    def forward_image(self, message, chat_id):
        def send(data):
            if not data:
                self.toast("照片尚未載入完成。", True)
                return
            path = self.temp_image_path("forward")
            path.write_bytes(data)
            self.send_images(chat_id, [str(path)])
        if str(message.get("url") or "") == "demo:collage":
            # Offline demo photo: share the illustrated scene itself.
            send(bytes(art.texture("scene", "mountain", 640, 480, "").save_to_png_bytes().get_data()))
            return
        data = self.image_data(message)
        if data:
            send(data)
            return
        holder = Gtk.Picture()
        self.load_image(message, holder, preview=False, done=lambda texture: send(self.image_data(message) or bytes(texture.save_to_png_bytes().get_data())))

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
        """LINE's sticker panel: a popover above the composer, one click sends."""
        chat = self.current_chat()
        if not chat:
            return
        if chat["id"] == KEEP_ID:
            self.show_sticker_panel([], KEEP_ID, self.generation, emoji_only=True)
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
            self.show_sticker_panel(response.get("result", {}).get("packs", []), chat_id, generation)
        self.core.request("stickers.list", callback=loaded)

    def sticker_tile_picture(self, sticker, size):
        url = str(sticker.get("url", ""))
        if url.startswith("demo:bear-"):
            return Art("bear", url.rsplit("-", 1)[-1], size, size)
        return self.sticker_picture(url, size, size)

    def remember_sticker(self, package_id, sticker):
        entry = {"package_id": str(package_id), "id": str(sticker.get("id", "")), "url": str(sticker.get("url", ""))}
        recent = [item for item in self.recent_stickers if (item["package_id"], item["id"]) != (entry["package_id"], entry["id"])]
        self.recent_stickers = [entry] + recent[:23]
        if self.prefs.persist:
            self.prefs.save_settings(recent_stickers=self.recent_stickers)

    def show_sticker_panel(self, packs, chat_id, generation, emoji_only=False):
        """Mirrors LINE desktop: tabs on top, a wrapping grid of pack icons,
        a drag handle, then the items of the chosen pack. Stickers send at
        once; the 表情貼 tab (basic emoji plus owned LINE emoji) types into the
        composer and keeps the panel open."""
        popover = self.sticker_popover
        packs = [pack for pack in packs if pack.get("stickers")]
        sets = {"sticker": [pack for pack in packs if pack.get("kind") != "emoji"],
                "emoji": [{"id": "basic", "name": "基本表情", "basic": True}] + [pack for pack in packs if pack.get("kind") == "emoji"]}
        sticker_start = "recent" if self.recent_stickers else (0 if sets["sticker"] else None)
        state = {"kind": "emoji" if emoji_only or sticker_start is None else "sticker",
                 "tab": {"sticker": sticker_start, "emoji": 0}, "query": "", "drag": 0}
        self.sticker_choices = []
        last = {"key": None, "time": 0.0}
        panel = box(V, 0, "sticker-panel")
        panel.set_size_request(540, 470)

        header = Gtk.CenterBox()
        header.add_css_class("sticker-header")
        tabs = box(H, 18, "sticker-tabs")
        header.set_center_widget(tabs)
        tools = box(H, 2, halign=END)
        tools.append(icon_button("search", lambda: toggle_search(), "搜尋", 18, "sticker-tool"))
        tools.append(icon_button("x", popover.popdown, "關閉", 18, "sticker-tool"))
        header.set_end_widget(tools)
        panel.append(header)
        search = Gtk.SearchEntry(placeholder_text="搜尋")
        search.add_css_class("sticker-search")
        search.set_visible(False)
        search.connect("search-changed", lambda entry: (state.update(query=entry.get_text().casefold().strip()), render_packs()))
        search.connect("stop-search", lambda *_: toggle_search(False))
        panel.append(search)

        packs_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=2, row_spacing=2)
        packs_box.add_css_class("sticker-packs")
        packs_box.set_valign(START)
        packs_box.set_min_children_per_line(9)
        packs_box.set_max_children_per_line(9)
        packs_scroll = Gtk.ScrolledWindow()
        packs_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        packs_scroll.add_css_class("sticker-packs-area")
        packs_scroll.set_child(packs_box)
        panel.append(packs_scroll)
        # The handle under the pack icons can be dragged, as in LINE. (A
        # Gtk.Paned warns whenever focus leaves it, so this is a plain drag.)
        handle = box(H, 0, "sticker-handle")
        handle.set_cursor_from_name("row-resize")
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: state.update(drag=packs_scroll.get_height()))
        drag.connect("drag-update", lambda gesture, dx, dy: packs_scroll.set_size_request(-1, max(56, min(300, int(state["drag"] + dy)))))
        handle.add_controller(drag)
        panel.append(handle)
        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_scroll.set_vexpand(True)
        panel.append(grid_scroll)

        def fit_packs():
            # Up to two rows of pack icons at first, like LINE; drag for more.
            icons = len(sets[state["kind"]]) + (1 if state["kind"] == "sticker" and self.recent_stickers else 0)
            packs_scroll.set_size_request(-1, min(2, max(1, -(-icons // 9))) * 52 + 14)

        def send(package_id, sticker):
            key = (package_id, sticker.get("id"))
            now = time.monotonic()
            # A double click is two "clicked" signals; it must not send twice.
            if last["key"] == key and now - last["time"] < 0.6:
                return
            last.update(key=key, time=now)
            if generation != self.generation or self.state.session != "ready" or not self.core:
                return
            self.core.request("messages.sticker", {"chat_id": chat_id, "package_id": package_id, "sticker_id": sticker["id"]},
                              lambda result: self.toast(self.error(result), True) if not result.get("ok") else self.after_message(chat_id))
            self.remember_sticker(package_id, sticker)

        def tile(package_id, sticker):
            widget = button(self.sticker_tile_picture(sticker, 64), lambda: send(package_id, sticker), "sticker-tile", "傳送貼圖")
            self.sticker_choices.append(widget)
            return widget

        def toggle_search(show=None):
            show = not search.get_visible() if show is None else show
            search.set_visible(show)
            if show:
                search.grab_focus()
            else:
                search.set_text("")

        def render_tabs():
            clear(tabs)
            for kind, title in (("sticker", "貼圖"), ("emoji", "表情貼")):
                if kind == "sticker" and emoji_only:
                    continue
                tabs.append(button(None, lambda value=kind: choose_kind(value), "sticker-kind" + (" active" if state["kind"] == kind else ""), text=title))

        def add_pack(widget):
            widget.set_halign(CENTER)
            packs_box.append(widget)

        def render_packs():
            clear(packs_box)
            kind, query = state["kind"], state["query"]
            current = state["tab"][kind]
            if kind == "sticker" and self.recent_stickers and not query:
                add_pack(button(icon("clock", 22), lambda: choose_tab("recent"), "sticker-pack" + (" active" if current == "recent" else ""), "最近使用"))
            for index, pack in enumerate(sets[kind]):
                if query and query not in str(pack.get("name", "")).casefold():
                    continue
                css = "sticker-pack" + (" active" if current == index else "")
                if pack.get("basic"):
                    add_pack(button(None, lambda value=index: choose_tab(value), css + " basic-emoji-pack", pack["name"], text="😊"))
                else:
                    add_pack(button(self.sticker_tile_picture(pack["stickers"][0], 36), lambda value=index: choose_tab(value), css, pack.get("name", "貼圖")))

        def render_grid():
            self.sticker_choices = []
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=4, row_spacing=4)
            grid.add_css_class("sticker-grid")
            grid.set_valign(START)
            current = state["tab"][state["kind"]]
            if state["kind"] == "emoji":
                pack = sets["emoji"][current]
                if pack.get("basic"):
                    grid.set_min_children_per_line(10)
                    grid.set_max_children_per_line(10)
                    for glyph in BASIC_EMOJI:
                        grid.append(button(None, lambda value=glyph: self.insert_emoji(value), "emoji", f"插入 {glyph}", text=glyph))
                else:
                    grid.set_min_children_per_line(8)
                    grid.set_max_children_per_line(8)
                    for sticker in pack["stickers"]:
                        widget = button(self.sticker_tile_picture(sticker, 40), lambda value=sticker: self.insert_sticon(value), "sticker-tile emoji-tile", sticker.get("text") or "表情貼")
                        self.sticker_choices.append(widget)
                        grid.append(widget)
                grid_scroll.set_child(grid)
                return
            grid.set_min_children_per_line(6)
            grid.set_max_children_per_line(6)
            if current == "recent":
                for entry in self.recent_stickers:
                    grid.append(tile(entry["package_id"], entry))
            elif current is not None:
                pack = sets["sticker"][current]
                for sticker in pack["stickers"]:
                    grid.append(tile(sticker.get("package_id") or pack["id"], sticker))
            grid_scroll.set_child(grid)

        def render():
            render_tabs()
            render_packs()
            render_grid()

        def choose_kind(value):
            if state["kind"] != value:
                state["kind"] = value
                fit_packs()
            render()

        def choose_tab(value):
            state["tab"][state["kind"]] = value
            render_packs()
            render_grid()
            grid_scroll.get_vadjustment().set_value(0)
        fit_packs()
        render()
        popover.set_child(panel)
        popover.popup()

    def insert_emoji(self, glyph):
        buffer = self.compose.get_buffer()
        buffer.insert_at_cursor(glyph)
        self.compose.grab_focus()

    def insert_sticon(self, sticker):
        """Puts an owned LINE emoji into the composer at the cursor, as an
        inline picture; send_text turns it back into a sticon reference."""
        url = str(sticker.get("url", ""))
        ref = {"package_id": str(sticker.get("package_id", "")), "sticker_id": str(sticker.get("id", "")),
               "text": str(sticker.get("text") or "(emoji)"), "url": url}
        chat_id = self.state.selected
        if not url or not ref["package_id"] or not ref["sticker_id"]:
            return

        def loaded(data):
            if self.state.selected != chat_id:
                return
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                return
            buffer = self.compose.get_buffer()
            buffer.insert_paintable(buffer.get_iter_at_mark(buffer.get_insert()), InlineEmoji(texture, 22, ref))
            self.compose.grab_focus()
        self.pictures.load(url, loaded)

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
                paths = [path for path in paths if path]
                if chat_id == self.state.selected:
                    self.add_images([p for p in paths if self.is_image(p)])
                    paths = [p for p in paths if not self.is_image(p)]
                if paths:
                    self.queue_files(paths, chat_id)
            chooser.destroy()
        dialog.connect("response", selected)
        dialog.show()

    def dropped(self, target, value, x, y):
        self.drag_overlay.set_visible(False)
        if not self.can_attach():
            return False
        paths = [item.get_path() for item in value.get_files() if item.get_path()]
        self.add_images([p for p in paths if self.is_image(p)])
        others = [p for p in paths if not self.is_image(p)]
        if others:
            self.queue_files(others, self.state.selected)
        return True

    # ------------------------------------------------------ composer tray
    IMAGE_TYPES = ("png", "jpg", "jpeg", "gif")

    def is_image(self, path):
        return str(path).lower().rsplit(".", 1)[-1] in self.IMAGE_TYPES

    def add_images(self, paths):
        """Pictures wait in the composer tray as thumbnails until Send, like
        LINE PC; each becomes its own photo message."""
        added = 0
        for path in paths:
            try:
                size = Path(path).stat().st_size
            except OSError:
                continue
            if size > 20 * 1024 * 1024:
                self.toast("照片超過 20 MB，請改用檔案傳送。", True)
                continue
            if len(self.pending_images) >= MAX_QUEUE:
                self.toast("一次最多傳送 20 張照片。")
                break
            self.pending_images.append(str(path))
            added += 1
        if added:
            self.render_tray()
            self.update_send()
            self.compose.grab_focus()

    def render_tray(self):
        clear(self.tray)
        for index, path in enumerate(self.pending_images):
            cell = Gtk.Overlay()
            picture = Gtk.Picture.new_for_filename(path)
            picture.set_size_request(64, 64)
            picture.set_can_shrink(True)
            try:
                picture.set_content_fit(Gtk.ContentFit.COVER)
            except AttributeError:
                pass
            picture.add_css_class("tray-thumb")
            picture.set_overflow(Gtk.Overflow.HIDDEN)
            cell.set_child(picture)
            remove = icon_button("x", lambda value=index: self.remove_image(value), "移除", 11, "tray-remove")
            remove.set_halign(END)
            remove.set_valign(START)
            cell.add_overlay(remove)
            self.tray.append(cell)
        self.tray.set_visible(bool(self.pending_images))

    def remove_image(self, index):
        if 0 <= index < len(self.pending_images):
            self.pending_images.pop(index)
        self.render_tray()
        self.update_send()

    def clear_tray(self):
        self.pending_images = []
        self.render_tray()

    def send_images(self, chat_id, paths, reply_to=""):
        generation = self.generation
        remaining = list(paths)

        def next_image(response=None):
            if generation != self.generation:
                return
            if response is not None:
                if not response.get("ok"):
                    self.toast(self.error(response), True)
                self.after_message(chat_id)
            if not remaining:
                self.set_progress(None)
                return
            path = remaining.pop(0)
            done = len(paths) - len(remaining)
            self.set_progress(f"正在傳送照片 {done}/{len(paths)}…")
            self.core.request("images.send", {"chat_id": chat_id, "path": path}, next_image)
        next_image()

    def set_progress(self, text):
        if text:
            self.composer_progress.set_text(text)
        self.composer_progress.set_visible(bool(text))

    def paste_clipboard(self):
        """Ctrl+V with an image on the clipboard adds it to the tray."""
        clipboard = self.window.get_clipboard()
        formats = clipboard.get_formats()
        if formats.contain_gtype(Gdk.FileList):
            def files_ready(source, result):
                try:
                    value = source.read_value_finish(result)
                except GLib.Error:
                    return
                paths = [item.get_path() for item in value.get_files() if item.get_path()]
                images = [p for p in paths if self.is_image(p)]
                self.add_images(images)
                others = [p for p in paths if not self.is_image(p)]
                if others and self.can_attach():
                    self.queue_files(others, self.state.selected)
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, files_ready)
            return True
        if not formats.contain_gtype(Gdk.Texture):
            return False

        def texture_ready(source, result):
            try:
                texture = source.read_texture_finish(result)
            except GLib.Error:
                return
            if texture is None:
                return
            path = self.temp_image_path("paste")
            texture.save_to_png(str(path))
            self.add_images([path])
        clipboard.read_texture_async(None, texture_ready)
        return True

    def temp_image_path(self, prefix):
        directory = Path(GLib.get_user_cache_dir()) / "matrix-line" / "outgoing"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory / f"{prefix}-{uuid.uuid4().hex[:12]}.png"

    def take_screenshot(self):
        """xdg-desktop-portal Screenshot (interactive) — works on GNOME/KDE
        under Wayland and X11; the picture lands in the composer tray."""
        if not self.can_attach():
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.toast("找不到桌面服務，無法擷取畫面。", True)
            return
        token = "line" + uuid.uuid4().hex[:8]
        sender = bus.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        def response(connection, sender_name, path, interface, signal, parameters):
            bus.signal_unsubscribe(self.screenshot_signal)
            code, results = parameters.unpack()
            if code != 0:
                return
            uri = results.get("uri", "")
            source = Gio.File.new_for_uri(uri).get_path() if uri else None
            if source:
                # Copy out of the portal's location, then remove its temp file.
                target = self.temp_image_path("screenshot")
                try:
                    Gio.File.new_for_path(source).move(Gio.File.new_for_path(str(target)), Gio.FileCopyFlags.OVERWRITE, None, None, None)
                except GLib.Error:
                    target = source
                self.window.present()
                self.add_images([str(target)])
        self.screenshot_signal = bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response", handle, None, Gio.DBusSignalFlags.NO_MATCH_RULE, response)
        options = {"handle_token": GLib.Variant("s", token), "interactive": GLib.Variant("b", True), "modal": GLib.Variant("b", True)}

        def called(connection, result):
            try:
                connection.call_finish(result)
            except GLib.Error:
                bus.signal_unsubscribe(self.screenshot_signal)
                self.toast("此桌面環境不支援擷取畫面（需要 xdg-desktop-portal）。", True)
        bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Screenshot", "Screenshot",
                 GLib.Variant("(sa{sv})", ("", options)), None, Gio.DBusCallFlags.NONE, -1, None, called)

    # ---------------------------------------------------------------- replies
    def start_reply(self, message):
        self.replying = message
        name = "你" if message.get("mine") else (message.get("sender_name") or self.state.name_of(message.get("sender_id")))
        self.reply_text.set_text(f"{name}：{self.summary(message)}")
        self.reply_bar.set_visible(True)
        self.compose.grab_focus()

    def cancel_reply(self):
        self.replying = None
        self.reply_bar.set_visible(False)

    def summary(self, message):
        if message.get("unsent"):
            return "已收回訊息"
        kind = message.get("kind") or "text"
        if kind == "text":
            return str(message.get("text") or "").replace("\ufffc", "(表情貼)").replace("\n", " ")
        return media_label(message)

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
        self.photo_button.set_sensitive(bool(self.state.capabilities.get("images")))
        self.screenshot_button.set_sensitive(bool(self.state.capabilities.get("images")))
        self.sticker_button.set_sensitive(bool(self.state.capabilities.get("stickers")))
        self.update_send()

    def refresh(self):
        if self.core and self.state.session == "ready":
            self.core.request("chats.refresh", callback=lambda reply: self.toast("已重新整理") if reply.get("ok") else self.toast(self.error(reply), True))

    def reading(self):
        """A chat counts as read only while the user can actually see it."""
        return bool(self.window and self.window.get_visible() and self.window.is_active()
                    and self.state.selected and self.main_stack.get_visible_child_name() == "chat")

    def focus_changed(self, *_):
        if self.reading():
            chat_id = self.state.selected
            if self.state.chats.get(chat_id, {}).get("unread"):
                self.state.chats[chat_id]["unread"] = 0
                self.render_list()
                self.render_rail()
            self.withdraw_notification("chat-" + str(chat_id)[:64])
            self.mark_read()

    def mark_read(self):
        if not self.reading():
            return
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
        self.sticker_popover.popdown()
        self.state.clear()
        self.persistent = None
        self.compose.get_buffer().set_text("")
        self.password_entry.set_text("")
        self.email_entry.set_text("")
        self.search.set_text("")
        self.reset_qr()
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
        # Like LINE, the list has no tagline: the bar shows only problems.
        self.footer.set_visible(bool(level))

    def load_local(self):
        demo = self.state.mode == "demo"
        account = self.account_key()
        if self.prefs.account != account:
            self.prefs.load(account, demo)
        if self.keep.account != account:
            self.keep.load(account, demo)
            self.state.messages.pop(KEEP_ID, None)
        if KEEP_ID not in self.state.messages:
            self.state.messages[KEEP_ID] = self.keep.messages()
        self.state.chats[KEEP_ID] = self.keep.chat()

    def on_event(self, event, data):
        if event == "session":
            state = data.get("state", "error")
            if state != "connecting" and self.restoring:
                self.show_restoring(False)
            if state == "logged_out":
                self.clear_session()
                message = data.get("message") or ""
                # Routine messages need no banner: the page itself says what to do.
                if message and message not in LOGIN_ROUTINE and not self.auth_status.get_visible():
                    self.login_message(message)
                self.login_ready_for_input()
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
                self.login_message("")
                self.qr_failed = False
                self.reset_qr()
                if self.state.mode != "demo" and self.persistent is False and self.auto_login.get_active():
                    self.set_footer("已連線 · 登入未保存至鑰匙圈", "warning")
                else:
                    self.set_footer("")
            elif state == "connecting":
                self.set_footer("正在恢復登入…" if self.restoring else "正在重新連線…", "warning")
            elif state == "error":
                self.set_footer("連線已中斷", "error")
                self.toast(data.get("message", "連線發生錯誤"), True)
                self.auth_failed(data.get("message", "請重新登入。"))
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
            if not self.state.selected or self.state.selected not in self.state.chats:
                # LINE opens on a blank pane: opening a chat is always the
                # user's choice, so no read receipt is sent at login.
                self.state.selected = None
                if self.main_stack.get_visible_child_name() == "chat":
                    self.main_stack.set_visible_child_name("empty")
            else:
                self.render_header()
                self.render_details()
        elif event == "message":
            if self.state.session == "logged_out":
                return
            message = data.get("message", {})
            added = self.state.add_message(message)
            chat_id = str(message.get("chat_id", ""))
            if added:
                self.notify(message)
            if chat_id == self.state.selected:
                if added and not message.get("mine") and not self.reading():
                    chat = self.state.chats.get(chat_id)
                    if chat is not None:
                        chat["unread"] = chat.get("unread", 0) + 1
                self.after_message(chat_id)
                self.mark_read()
            else:
                self.render_list()
                self.render_rail()
                self.render_sidebar()
        elif event == "read":
            # Read receipt (op 55): someone read up to `time` in this chat.
            chat = self.state.chats.get(str(data.get("chat_id", "")))
            reader = str(data.get("reader", ""))
            if chat is None or not reader:
                return
            readers = chat.setdefault("read_by", {}) or {}
            chat["read_by"] = readers
            if int(data.get("time", 0) or 0) > int(readers.get(reader, 0) or 0):
                readers[reader] = int(data.get("time", 0) or 0)
                if chat["id"] == self.state.selected and self.main_stack.get_visible_child_name() == "chat":
                    self.render_messages()
        elif event == "unsent":
            chat_id = str(data.get("chat_id", ""))
            message_id = str(data.get("message_id", ""))
            changed = False
            for message in self.state.messages.get(chat_id, []):
                if str(message.get("id")) == message_id and not message.get("unsent"):
                    message.update(unsent=True, kind="unsent", text="", url="")
                    message.pop("emoji", None)
                    changed = True
            if changed and chat_id == self.state.selected and self.main_stack.get_visible_child_name() == "chat":
                self.render_messages()
        elif event == "notice":
            self.toast(data.get("message", ""), data.get("level") == "error")
        elif event == "auth":
            stage = data.get("stage")
            if stage == "qr":
                try:
                    png = base64.b64decode(data.get("qr_png", ""), validate=True)
                    if not png:
                        raise ValueError("missing QR image")
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
                    self.qr_placeholder.set_spinning(False)
                    self.qr_placeholder.set_visible(False)
                    self.qr_button.set_visible(False)
                    self.qr_picture.set_from_paintable(texture)
                except (ValueError, GLib.Error):
                    self.auth_failed("無法顯示行動條碼。請改用電子郵件登入，或重新產生。")
            elif stage == "pin":
                self.pin_label.set_text(str(data.get("pin", "")))
                if data.get("message"):
                    self.pin_hint.set_text(data["message"])
                self.restore_spinner.set_spinning(False)
                self.login_left.set_visible_child_name("pin")
            elif stage == "error":
                self.auth_failed(data.get("message", "登入失敗，請重新嘗試。"))

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
        notification.set_default_action_and_target("app.open-chat", GLib.Variant.new_string(chat_id))
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
            self.check("login button waits for e-mail and password", not self.email_button.get_sensitive())
            self.email_entry.set_text("demo@example.com")
            self.password_entry.set_text("x")
            self.check("login button enables when both are filled", self.email_button.get_sensitive())
            self.email_entry.set_text("")
            self.password_entry.set_text("")
            self.check("QR pane and 自動登入 are on the login page", self.qr_frame.get_parent() is not None and self.auto_login.get_label() == "自動登入")
            self.on_event("auth", {"stage": "pin", "pin": "1234", "message": "請在手機輸入驗證碼"})
            self.check("PIN view shows verification code", self.login_left.get_visible_child_name() == "pin" and self.pin_label.get_text() == "1234")
            self.auth_busy(False)
            self.smoke_phase = 1
        elif step == 1 and self.state.session == "ready" and len(self.state.chats) > 1:
            self.check("native workspace ready", self.pages.get_visible_child_name() == "workspace")
            self.check("demo is clearly identified", self.state.mode == "demo" and "離線展示" in self.mode_label.get_text())
            sidebar = self.labels_in(self.chat_list)
            self.check("chat list has no section taglines", "置頂聊天" not in sidebar and "週末散步計畫 (5)" in sidebar)
            self.check("three-column layout: info panel closed", not self.details.get_visible())
            self.check("no chat opens by itself at login", self.main_stack.get_visible_child_name() == "empty")
            self.select_chat("demo-weekend")
            self.check("reference chat opens on click", self.state.selected == "demo-weekend" and self.chat_title.get_text() == "週末散步計畫 (5)")
            self.check("sealed chat has no unencrypted label", not self.subtitle_row.get_visible())
            history = self.state.messages.setdefault("demo-weekend", [])
            history.append({"id": "smoke-plain", "chat_id": "demo-weekend", "sender_id": "demo-hao", "sender_name": "林子豪", "text": "plain", "kind": "text", "time": 0, "status": "received", "unencrypted": True})
            self.after_message("demo-weekend")
            self.check("unencrypted chat is labelled in the header", self.subtitle_row.get_visible() and "未加密" in self.chat_subtitle.get_text())
            self.state.messages["demo-weekend"] = [m for m in self.state.messages["demo-weekend"] if not m["id"].startswith("smoke-plain")]
            self.after_message("demo-weekend")
            self.toggle_details()
            self.check("artwork renders through the SVG loader", art.texture("bear", 0, 120, 108, None).get_width() == 120)
            self.check("details panel shows tabs and members", {"資訊", "筆記本", "相簿", "檔案", "5 位成員"} <= set(self.labels_in(self.detail_contents)))
            self.search.set_text("怡庭")
            self.check("search filters chat rows by title or member", "陳怡庭" in self.labels_in(self.chat_list) and "我們這一家" not in self.labels_in(self.chat_list))
            self.search.set_text("")
            self.set_filter("group")
            self.check("group filter keeps only groups", "陳怡庭" not in self.labels_in(self.chat_list) and "我們這一家 (3)" in self.labels_in(self.chat_list))
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
                self.check("conversation search counts matches", self.search_count.get_text() == "1 則結果")
                self.close_search()
                self.select_chat(KEEP_ID)
                self.compose.get_buffer().set_text("Keep 本機測試")
                self.send_button.emit("clicked")
                self.check("Keep memo stays local", any(m.get("text") == "Keep 本機測試" for m in self.state.messages[KEEP_ID]) and self.keep.items[-1]["text"] == "Keep 本機測試")
                self.navigate("friends")
                friends = self.labels_in(self.chat_list)
                self.check("friends list is grouped like LINE", self.side_title.get_text() == "好友" and {"我的最愛 2", "群組 3", "好友 5"} <= set(friends))
                self.open_profile("demo-kai")
                self.check("profile modal opens", self.modal_kind == "profile")
                self.open_direct("demo-kai")
                self.check("message button opens a direct chat", self.state.selected == "demo-kai" and self.modal is None)
                self.open_settings()
                self.check("settings modal shows options", self.modal_kind == "settings")
                self.close_modal()
                self.select_chat(self.smoke_chat)
                self.check("return to conversation", self.main_stack.get_visible_child_name() == "chat")
                chat = self.current_chat()
                own = [item for item in self.state.messages.get(chat["id"], []) if item.get("mine")]
                if own:
                    before = self.read_label(own[-1], chat)
                    self.on_event("read", {"chat_id": chat["id"], "reader": "smoke-reader", "time": int(own[-1].get("time", 0)) + 1})
                    self.check("read receipt event updates 已讀", self.read_label(own[-1], chat) != before and self.read_label(own[-1], chat).startswith("已讀"))
                others = [item for item in self.state.messages.get(chat["id"], []) if not item.get("mine") and item.get("kind") == "text"]
                if others:
                    self.on_event("unsent", {"chat_id": chat["id"], "message_id": others[0]["id"]})
                    self.check("unsent event turns the bubble into a notice", others[0].get("unsent") and others[0].get("kind") == "unsent")
                texts = [item for item in self.state.messages.get(chat["id"], []) if item.get("kind") == "text" and not item.get("unsent")]
                if texts:
                    self.open_forward(texts[0])
                    for row in self.forward_rows[:2]:
                        row.emit("clicked")
                    self.check("share dialog multi-selects chats", self.modal_kind == "forward" and self.forward_share.get_sensitive()
                               and self.forward_share.get_child().get_text() == "分享（2）")
                    self.close_modal()
                selected = self.state.selected
                self.window_key(None, Gdk.KEY_Tab, 0, Gdk.ModifierType.CONTROL_MASK)
                moved = self.state.selected
                self.window_key(None, Gdk.KEY_Tab, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
                self.check("Ctrl+Tab cycles chats", moved != selected and self.state.selected == selected)
                self.window_key(None, Gdk.KEY_1, 0, Gdk.ModifierType.CONTROL_MASK)
                friends = self.section == "friends"
                self.window_key(None, Gdk.KEY_2, 0, Gdk.ModifierType.CONTROL_MASK)
                self.check("Ctrl+1 / Ctrl+2 switch 好友 and 聊天", friends and self.section == "chats")
                previous = self.prefs.settings["theme"]
                self.prefs.settings["theme"] = "dark"
                self.apply_theme()
                dark = self.window.has_css_class("dark")
                self.prefs.settings["theme"] = "light"
                self.apply_theme()
                self.check("螢幕顯示模式 switches dark and light", dark and not self.window.has_css_class("dark"))
                self.prefs.settings["theme"] = previous
                self.apply_theme()
                self.sticker_button.emit("clicked")
                self.smoke_phase = 3
        elif step == 3 and self.sticker_popover.get_visible() and getattr(self, "sticker_choices", []):
            self.check("eight reference stickers listed", len(self.sticker_choices) == 8)
            self.sticker_choices[0].emit("clicked")
            self.sticker_choices[0].emit("clicked")
            self.check("sticker panel stays open after sending", self.sticker_popover.get_visible())
            self.sticker_popover.popdown()
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
            self.check("stale sticker catalog cannot open after account changes", not self.sticker_popover.get_visible())
            self.open_stickers()
            callback = recorder.calls.pop()[2]
            callback({"ok": True, "result": {"packs": [{"id": "demo", "name": "Demo", "stickers": [{"id": "bear-1", "url": "demo:bear-1"}]}]}})
            self.generation += 1
            self.sticker_choices[0].emit("clicked")
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
