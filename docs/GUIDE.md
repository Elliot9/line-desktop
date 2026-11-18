# Guide

**English** · [繁體中文](GUIDE.zh-TW.md) · Back to the [README](../README.md)

- [Install](#install)
- [Updating and uninstalling](#updating-and-uninstalling)
- [Everyday use](#everyday-use)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Limitations](#limitations)
- [FAQ](#faq)

The app's interface is in Traditional Chinese; the Chinese labels below are what you will see on screen.

## Install

### Requirements

- **Ubuntu 24.04+** or **Debian 12+** (including derivatives such as Linux Mint, Pop!_OS and Zorin)
- x86_64 (a typical Intel or AMD computer) or ARM64
- Signing in automatically needs the system keyring (GNOME has one). Without it LINE still works, but asks you to sign in each time.

### Release download (recommended)

```bash
curl -LO https://github.com/Elliot9/line-desktop/releases/latest/download/line-desktop-linux-amd64.tar.gz
tar -xzf line-desktop-linux-amd64.tar.gz
cd line-desktop
./line-app --install-menu   # add LINE to your app menu
./line-app                  # start
```

On ARM, use `arm64` instead of `amd64`.

- The first start installs any missing system packages (GTK 4, PyGObject, fonts, the keyring tools) and **may ask for your `sudo` password**.
- The download includes the compiled core; you don't need Go.
- Keep the whole `line-desktop` folder. If you move it later, run `./line-app --install-menu` again.

### From source

```bash
git clone https://github.com/Elliot9/line-desktop.git
cd line-desktop
./line-app
```

The first start also downloads a SHA256-checked Go toolchain and builds the core, which takes a few minutes and about 3 GB of free memory. Later starts use the cached build.

### Other distributions

Automatic setup only supports apt. On Fedora, Arch and others, install the equivalents first, then run `./line-app`: Python 3, PyGObject, GTK 4, librsvg, libsecret (`secret-tool`), gnome-keyring, curl, and the Noto CJK and Noto Color Emoji fonts. To play videos in the app you also need GTK 4's GStreamer media backend and GStreamer's good and libav plugins (MP4, H.264, AAC). The full list is in [setup-debian.sh](../scripts/setup-debian.sh).

## Updating and uninstalling

**Updating**: when a new version is out, LINE shows your version, the latest one and what changed.

- **立即更新** (update now) downloads and verifies it, then restarts LINE; your login and settings stay. **暫不更新** (not now) keeps the current version.
- You can also check under **設定 → 版本** (Settings → Version), or turn off the check at start.
- Versions 1.0.2 and older don't have this yet: download the new release once and extract it over the old folder.
- A folder run from source updates with `git pull`.
- What changed in each version: [CHANGELOG.md](../CHANGELOG.md).

**Uninstalling**: delete the `line-desktop` folder, `~/.local/share/matrix-line` (local data) and `~/.local/share/applications/org.matrixline.Desktop.*.desktop`.

## Everyday use

**Signing in**: scan the QR code with LINE on your phone, or enter your email and password; if a PIN appears, enter it on the phone. Leave **自動登入** ticked to skip this next time.

**Running in the background**: closing the window keeps LINE running so messages and notifications still arrive. Open it again from the app menu or the dock. To quit completely, right-click LINE in the dock and choose **結束 LINE** (Quit LINE), or press `Ctrl+Q`.

**Friends page**: one click on a friend or group shows their card; a double click opens the chat.

**Offline demo**: `./line-app --demo` opens built-in sample data. It doesn't connect to LINE and needs no account.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `Enter` | Send (can be changed to `Alt+Enter` in settings) |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Friends / Chats / Add friends |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Next / previous chat |
| `Ctrl+F` | Search the current chat |
| `Ctrl+Shift+F` | Search chats |
| `Ctrl+Shift+S` | Take a screenshot and send it |
| `Ctrl+V` | Paste a picture |
| `Ctrl+W` | Close the current chat |
| `Ctrl+R` | Refresh |
| `Ctrl+Q` | Quit LINE (closing the window only hides it) |
| `←` `→` | Previous / next in the photo viewer (`Esc` closes it) |
| `Space` | Pause / play a video in the photo viewer |

## Limitations

- **Not supported yet**: voice and video calls ([research notes](BACKLOG.md)), adding friends, creating groups, OpenChat, editing messages, sending videos, playing voice messages.
- **Only the last 14 days of history**: scrolling up loads older messages, but LINE only gives desktop clients the last 14 days; anything older stays on your phone.
- **Notes and Keep memos are local** to this computer and don't sync to your phone.
- **Official accounts and very large groups have no Letter Sealing**: LINE itself doesn't encrypt them, so text to them is sent unencrypted and the chat says 🔓 此聊天未加密 (this chat is not encrypted); files can't be sent to them. Ordinary friends and groups stay end-to-end encrypted, and a failed encryption never falls back to plain text.
- **Shares a login slot with LINE's Chrome extension**: signing in here may sign the Chrome extension out, and the other way round ([known upstream issue](https://github.com/beeper/line#known-issues)). Your phone and the official PC/Mac apps are not affected.

Feature-by-feature status: [STATUS.md](STATUS.md) (in Traditional Chinese).

## FAQ

**Can LINE ban my account for this?**
It connects the same way as LINE's own Chrome extension. It is still unofficial, so weigh the risk yourself.

**Chinese shows as boxes, or emoji are missing?**
Install `fonts-noto-cjk` and `fonts-noto-color-emoji`; `./line-app --setup` does it for you.

**I have to sign in every time.**
The system keyring is probably not running or not unlocked. Outside GNOME, make sure `gnome-keyring` is installed and running, and that 自動登入 is ticked on the sign-in page.

**Videos don't play.**
Usually a video decoder is missing. `./line-app --setup` installs what's needed; the viewer also offers to open the video in another app.

**Does it work on Wayland?**
Yes, on both Wayland and X11.

**Where is my data?**
Your login is in the system keyring (not written when 自動登入 is off); your password is never stored. Notes, Keep memos and settings are in `~/.local/share/matrix-line`, readable only by you. Opened photos are decrypted into a private in-memory folder that is deleted at logout and exit.
