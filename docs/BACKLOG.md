# 待辦

還沒排進版本的功能，以及動手前的調查記錄。

## 語音通話（一對一）

**狀態**：已調查，尚未實作。下一步是確認目前的登入身分能不能取得通話路由（見「第一步」）。

### 目前的情況

- 核心透過 [beeper/line](https://github.com/beeper/line) 以 LINE Chrome 擴充功能的身分連線（`CHROMEOS`，`line-chrome-gw.line-apps.com`）。這個函式庫沒有任何通話 API，官方 Chrome 版本身也不能通話。
- 聊天裡的通話紀錄（content type 6）目前顯示成「[通話通知]」。來電的 operation（type 50）還沒有處理。
- 個人檔案卡上的「語音通話」按鈕會說明此版本不提供。

### 協定

以下整理自 [linejs](https://github.com/evex-dev/linejs)（MIT 授權）。它從 LINE Android 26.6.2 的 `libandromeda.so` 反向工程而來，是目前唯一公開、能實際撥通的開源實作。通話分成三層：

1. **取得通話路由（HTTPS + Thrift）**
   - 端點：`POST https://legy.line-apps.com/V4`（CallService），Thrift TCompactProtocol。header 帶 `x-line-application`、`x-line-access`。
   - 一對一：`acquireCallRoute(2: string to, 3: i32 callType, 4: map<string,string> fromEnvInfo)`。
     - `callType`：1 是語音，2 是視訊。
     - `fromEnvInfo` 帶 `devname`（裝置名稱）。
   - 回傳 `CallRoute` 的重要欄位：
     - 1 `fromToken`、2 `callFlowType`（2 = PLANET）
     - 3 `voipAddress`、4 `voipUdpPort`
     - 8 `fakeCall`
     - 12 `commParam`：JSON，內含 `mpkey`，也就是中繼伺服器的 P-256 公鑰
     - 18 `w2pGw`、20 `stnpk`
   - 群組：先 `getGroupCall(chatMid)`，再 `acquireGroupCallRoute(2: chatMid, 3: mediaType, 4: isInitialHost, 5: list<string> capabilities)`。
2. **來電通知**
   - Talk operation type 50（`NOTIFIED_RECEIVED_CALL`）。
   - `param1` 是來電者，`param2` 是 fromToken。
   - `param3` 是 JSON：`k: "CA"` 代表語音，`vs` 是通話 ID。
3. **信令（UDP，PLANET + Cassini）**
   - 送到 `voipAddress:voipUdpPort`。
   - 封包：2 bytes chunk header + 4 bytes fixed header（type、flags、11 bit 長度、17 bit 序號），後面接加密內容。
   - 加密：
     1. 本機產生一次性的 P-256 金鑰，與 `mpkey` 做 ECDH。
     2. 經過兩段 HMAC-SHA512 推導（不是標準 HKDF），得到 AES-128-CTR 金鑰和 HMAC 金鑰。
   - 內容是 protobuf：SETUP_REQ 1、SETUP_RSP 2、VERIFY 3/4、CONN_REQ 5、CONN_RSP 6、REL 7/8、INFO 17/18、ALIVE_RPT 22。
   - 撥出流程：
     1. `setup_req`：對方開始響鈴。
     2. `setup_rsp`：伺服器回覆，並給 keepalive 間隔。
     3. 定時送 keepalive，保持 UDP 通道。
     4. 對方接聽時收到 `conn_req`。
     5. 回 `conn_rsp`。
     6. 開始傳聲音。
     7. 掛斷時送 `rel_req`。
4. **聲音（RTP / SRTP）**
   - SRTP 使用 `AES_CM_128_HMAC_SHA1_80`，RTP header extension profile 是 `0x0240`。
   - 編碼是 Opus，48 kHz 單聲道、每幀 20 ms，timestamp 每次加 960，每個封包前面多加一個 `0x00` byte。
   - 一對一通話端對端加密：金鑰由雙方在 setup 和 answer 交換的 ECDH 材料推導。
   - 群組通話只有傳輸層加密，封包格式也不同：兩幀合成 40 ms。

### 風險與未知

- **身分**：Chrome 閘道會不會轉送 CallService 還沒確認。如果不會，就得另外以 PC 或 Android 身分登入 `legy`，可能讓使用者其他電腦上的 LINE 被登出。
- **穩定度**：linejs 撥出時對方會響，也聽得到我們的聲音。但對方用 iOS 或桌面版接聽時，約 10 秒後會被斷線（T103，[evex-dev/linejs#198](https://github.com/evex-dev/linejs/issues/198)），對方傳來的聲音也驗證失敗。雙向通話還不穩定。
- **範圍**：linejs 的接聽來電沒有實際測過，也不支援視訊。
- **帳號**：偽裝官方用戶端違反 LINE 的使用條款，帳號可能受到限制。

### 工作量

- 把 linejs 的 PLANET、Cassini、SRTP 移植到 Go 核心，約 8000 行 TypeScript（不含測試）。
- Linux 音訊：用 PipeWire 或 GStreamer 錄音與播放，加上 Opus 編解碼、回音消除。
- 介面：撥打、來電、通話中的畫面，以及聊天裡的通話紀錄。

### 第一步

用目前的登入，對一位好友呼叫一次 `acquireCallRoute`，看 `CHROMEOS` 身分能不能取得路由。這一步不會送出 `setup_req`，對方手機不會響。

### 參考

- [linejs 通話文件](https://linejs.evex.land/docs/call)，原始碼在 `packages/linejs/client/features/call/`
- [evex-dev/linejs#198](https://github.com/evex-dev/linejs/issues/198)：1:1 通話約 10 秒後斷線
- [LINE Encryption Report](https://www.lycorp.co.jp/en/privacy-security/security/transparency/encryption-report/2024/)：一對一通話使用 Letter Sealing 端對端加密，群組通話只有傳輸層加密
