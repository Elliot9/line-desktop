# 使用說明

[English](GUIDE.md) · **繁體中文** · 回到 [README](../README.zh-TW.md)

- [安裝](#安裝)
- [更新與移除](#更新與移除)
- [日常使用](#日常使用)
- [快捷鍵](#快捷鍵)
- [限制與注意事項](#限制與注意事項)
- [常見問題](#常見問題)

## 安裝

### 系統需求

- **Ubuntu 24.04 以上**或 **Debian 12 以上**（含 Linux Mint、Pop!_OS、Zorin 等衍生版）
- x86_64（一般 Intel／AMD 電腦）或 ARM64
- 自動登入需要系統鑰匙圈（GNOME 預設就有）；沒有也能用，只是每次都要重新登入。

### 下載安裝包（推薦）

```bash
curl -LO https://github.com/Elliot9/line-desktop/releases/latest/download/line-desktop-linux-amd64.tar.gz
tar -xzf line-desktop-linux-amd64.tar.gz
cd line-desktop
./line-app --install-menu   # 加入應用程式選單，之後可從選單開啟
./line-app                  # 啟動
```

ARM 電腦把 `amd64` 換成 `arm64`。

- 第一次啟動會自動安裝缺少的系統套件（GTK 4、PyGObject、字型、鑰匙圈工具），**可能會要求輸入 `sudo` 密碼**。
- 安裝包已內含編譯好的通訊核心，不需要安裝 Go。
- 請保留整個 `line-desktop` 資料夾。之後搬移資料夾的話，重新執行一次 `./line-app --install-menu`。

### 從原始碼執行

```bash
git clone https://github.com/Elliot9/line-desktop.git
cd line-desktop
./line-app
```

第一次啟動會額外下載經 SHA256 驗證的 Go 工具鏈並編譯核心，需要幾分鐘與約 3 GB 可用記憶體。之後啟動會使用快取。

### 其他發行版

自動安裝只支援 apt。Fedora、Arch 等請先自行安裝對應套件再執行 `./line-app`：Python 3、PyGObject、GTK 4、librsvg、libsecret（`secret-tool`）、gnome-keyring、curl、Noto CJK 與 Noto Color Emoji 字型。要在程式裡播放影片，另需 GTK 4 的 GStreamer 媒體後端，以及 GStreamer 的 good 與 libav 外掛（MP4、H.264、AAC）。完整清單見 [setup-debian.sh](../scripts/setup-debian.sh)。

## 更新與移除

**更新**：有新版本時，開啟 LINE 會顯示目前版本、最新版本與這次改了什麼。

- 按「立即更新」會自動下載、驗證並重新開啟 LINE，登入狀態與設定都會保留；按「暫不更新」就照常使用目前的版本。
- 也可以在「設定 → 版本」按「檢查更新」，或關閉「開啟 LINE 時檢查有沒有新版本」。
- 1.0.2 以前的版本還沒有這個功能，請手動下載新版安裝包，解壓縮覆蓋原本的資料夾一次。
- 從原始碼執行的資料夾請用 `git pull` 更新。
- 每個版本改了什麼，見[更新紀錄](../CHANGELOG.md)。

**移除**：刪除 `line-desktop` 資料夾、`~/.local/share/matrix-line`（本機資料），以及 `~/.local/share/applications/org.matrixline.Desktop.*.desktop`。

## 日常使用

**登入**：用手機 LINE 掃描畫面上的 QR Code，或輸入電子郵件與密碼；出現 PIN 碼時在手機上輸入。勾選「自動登入」，下次開啟就會直接進入。

**在背景繼續收訊息**：關閉視窗後 LINE 會留在背景接收訊息與通知，再從應用程式選單或 dock 開啟就會回到原畫面。要完全結束，在 dock 的 LINE 圖示上按右鍵選「結束 LINE」，或按 `Ctrl+Q`。

**好友頁**：單擊好友或群組顯示資訊卡片，雙擊開啟聊天室。

**離線展示**：`./line-app --demo` 開啟內建的展示資料，不會連到 LINE，也不需要帳號。

## 快捷鍵

| 按鍵 | 動作 |
| --- | --- |
| `Enter` | 傳送訊息（可在設定改成 `Alt+Enter` 傳送） |
| `Ctrl+1`／`Ctrl+2`／`Ctrl+3` | 好友／聊天／加入好友 |
| `Ctrl+Tab`／`Ctrl+Shift+Tab` | 下一個／上一個聊天 |
| `Ctrl+F` | 在目前對話中搜尋 |
| `Ctrl+Shift+F` | 搜尋聊天 |
| `Ctrl+Shift+S` | 擷取螢幕畫面並傳送 |
| `Ctrl+V` | 貼上圖片 |
| `Ctrl+W` | 關閉目前對話 |
| `Ctrl+R` | 重新整理 |
| `Ctrl+Q` | 結束 LINE（關閉視窗只會隱藏） |
| `←` `→` | 照片檢視器中切換上一張／下一張（`Esc` 關閉） |
| `空白鍵` | 照片檢視器中暫停／繼續播放影片 |

## 限制與注意事項

- **還不支援**：語音與視訊通話（[調查記錄](BACKLOG.md)）、新增好友、建立群組、社群（OpenChat）、編輯訊息、傳送影片、播放語音訊息。
- **只有最近 14 天的聊天記錄**：往上捲動可以載入更早的訊息，但 LINE 只提供電腦版最近 14 天的訊息，更早的只在手機上。
- **記事與 Keep 筆記只存在本機**，不會同步到手機。
- **官方帳號與超大群組沒有 Letter Sealing**：LINE 本身就不替它們加密，傳給它們的文字會以未加密方式送出，聊天頂端會顯示「🔓 此聊天未加密」；檔案則無法傳給這類聊天。一般好友與群組仍全程加密，加密失敗時不會改用明文。
- **與 LINE Chrome 擴充功能共用登入名額**：登入這個程式可能讓 Chrome 擴充功能登出，反之亦然（[上游已知問題](https://github.com/beeper/line#known-issues)）。手機與官方 PC／Mac 版不受影響。

各功能的詳細狀態見[功能狀態](STATUS.md)。

## 常見問題

**這會被 LINE 封鎖帳號嗎？**
程式使用與 LINE 官方 Chrome 擴充功能相同的連線方式。但它仍是非官方程式，請自行評估風險。

**中文是方塊，或表情符號顯示不出來？**
請安裝 `fonts-noto-cjk` 與 `fonts-noto-color-emoji`（`./line-app --setup` 會自動安裝）。

**每次開啟都要重新登入？**
系統鑰匙圈可能沒有啟動或沒有解鎖。GNOME 以外的桌面環境請確認已安裝並啟動 `gnome-keyring`，並勾選登入頁的「自動登入」。

**影片無法播放？**
多半是缺少影片解碼器。執行 `./line-app --setup` 會安裝需要的元件；也可以在檢視器按「用其他程式開啟」。

**支援 Wayland 嗎？**
支援，Wayland 與 X11 都可以用。

**資料存在哪裡？**
登入憑證在系統鑰匙圈；取消「自動登入」時不會寫入，密碼從不儲存。記事、Keep 筆記與偏好設定在 `~/.local/share/matrix-line`，檔案權限僅限本人讀寫。開啟照片時，解密後的照片暫存在只有你能讀取的記憶體目錄，登出或結束時刪除。
