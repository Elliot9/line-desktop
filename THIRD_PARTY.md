# 第三方來源

本專案重用 [beeper/line](https://github.com/beeper/line) 的 Go 通訊與加密套件。
固定版本為 `a9cefaf244d17f7bb6fb9685cdfd3ccf993c3293`，透過 `core/go.mod` 與 `core/go.sum` 取得。
原專案採 MIT 授權，完整聲明保留在 [UPSTREAM-LICENSE.txt](docs/UPSTREAM-LICENSE.txt)。

登入、群組金鑰及附件處理也參考該版本的 `pkg/connector`，並移除 Matrix 服務依賴。
GTK 與 PyGObject 使用發行版提供的套件。Go 模組的其他相依來源列於 `core/go.mod`。

`index.html` 是原先的互動設計參考。`desktop` 使用 GTK 原生元件重新實作，沒有嵌入網頁；`desktop/art.py` 的圖示、人物、風景與小熊貼圖是從 `index.html` 移植的原創 SVG。
