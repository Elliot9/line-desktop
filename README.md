# LINE for Linux · GTK 4 預覽版

這是以 `index.html` 為視覺參考的原生 GTK 4 用戶端，目標為 Ubuntu 24.04 與 Debian 12 以上。Go 核心直接重用 [beeper/line](https://github.com/beeper/line) 的 LINE 通訊與加密套件，不需要 Matrix 伺服器，也沒有嵌入瀏覽器。

目前是可啟動、可測試的預覽版。已驗證 Linux 介面、核心邏輯與真實 QR 挑戰的產生及取消。**尚未使用真實帳號完成登入、收發及檔案互通驗證。** 各功能的邊界與驗證結果見 [功能狀態](docs/STATUS.md)。

## 啟動

在 Ubuntu／Debian 桌面終端機進入本專案目錄，執行：

```bash
./line-app
```

首次執行會安裝缺少的 GTK 套件，可能要求 `sudo` 密碼。使用原始碼時，啟動腳本也會下載經 SHA256 驗證的 Go 1.26.10 並建置核心；第一次編譯可能需要數分鐘及約 3 GB 可用記憶體。後續啟動使用快取。

開啟後選擇 QR Code 登入，以手機 LINE 掃描。若出現 PIN，依畫面提示在手機輸入。也可改用電子郵件與密碼登入。

LINE 的 Letter Sealing 必須可用。目前文字與檔案傳送不會退回未加密流程。此程式與 LINE Chrome 擴充套件共用登入名額，登入其中一個可能使另一個工作階段失效。[上游已記錄此限制](https://github.com/beeper/line#known-issues)。

## 先看介面

```bash
./line-app --demo
```

展示模式不連線到 LINE。聊天、貼圖及檔案操作只會改變本次示範資料；本機筆記會保存在這台電腦。畫面頂端會持續標示「離線展示」。

## 使用已包好的版本

選擇與電腦架構相符的壓縮檔。一般 Intel／AMD 電腦使用 `amd64`，ARM 電腦使用 `arm64`。

```bash
tar -xzf matrix-line-preview-linux-amd64.tar.gz
cd matrix-line
./line-app
```

套件內含已編譯的核心，不需要安裝 Go。GTK 與 Python 仍由系統套件提供。保留整個資料夾，避免只移動 `line-app`。

## 加入應用程式選單

```bash
./line-app --install-menu
```

啟動器會寫入目前使用者的應用程式選單。若之後搬移專案資料夾，請重新執行此指令。

## 手動準備相依套件

```bash
./line-app --setup
```

自動安裝使用 `apt-get`，套件清單在 [setup-debian.sh](scripts/setup-debian.sh)。程式使用 `/usr/bin/python3`，不需要 `pip` 或虛擬環境。

登入資料交給 Secret Service 系統鑰匙圈保存。若鑰匙圈不可用，程式只保留本次登入，並在狀態與設定中說明。密碼不會儲存。本機筆記保存在 `$XDG_DATA_HOME/matrix-line/notes`，預設為 `~/.local/share/matrix-line/notes`。

## 開發與驗證

```bash
cd core
go test -trimpath ./...
cd ..
python3 scripts/check-core.py .build/line-core-linux-arm64
scripts/gui-smoke.sh
```

GUI 自動檢查需要 `xvfb xauth dbus-x11 imagemagick`。可使用 [test.Dockerfile](scripts/test.Dockerfile) 建立 Linux 測試環境。`scripts/check-core.py` 的 `--live-qr` 選項會連線至 LINE 建立並取消 QR 挑戰，請只在沒有已儲存帳號的隔離環境使用。

在 Linux 建立包含核心的套件：

```bash
scripts/package.sh amd64
scripts/package.sh arm64
```

輸出位於 `dist/`，並附 SHA256 檢查檔。通訊協定與檔案分工見 [架構說明](docs/ARCHITECTURE.md)，授權來源見 [第三方聲明](THIRD_PARTY.md)。
