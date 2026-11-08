package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"path/filepath"
	"strconv"
	"time"

	"github.com/beeper/line/pkg/line"

	"matrix-line/core/internal/attachments"
)

func (b *backend) handleAttachment(req request) (any, error) {
	switch req.Method {
	case "stickers.list":
		var reload struct {
			Reload bool `json:"reload"`
		}
		if err := params(req.Params, &reload); err != nil {
			return nil, err
		}
		if b.demo {
			stickers := make([]attachments.Sticker, len(demoMoods))
			for i := range demoMoods {
				id := "bear-" + strconv.Itoa(i)
				stickers[i] = attachments.Sticker{ID: id, PackageID: "demo", URL: "demo:" + id}
			}
			return map[string]any{"packs": []attachments.Pack{{ID: "demo", Name: "散步小熊", Stickers: stickers}}}, nil
		}
		if b.catalog != nil && !reload.Reload {
			return map[string]any{"packs": b.catalog.Packs()}, nil
		}
		catalog, err := attachments.LoadCatalog(b.ctx, b.api(), b.region, time.Now())
		if err != nil {
			return nil, err
		}
		b.catalog = catalog
		return map[string]any{"packs": catalog.Packs()}, nil
	case "messages.sticker":
		var p struct {
			ChatID    string `json:"chat_id"`
			PackageID string `json:"package_id"`
			StickerID string `json:"sticker_id"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err
		}
		if !b.validChat(p.ChatID) {
			return nil, fail("invalid_params", "請選擇有效的聊天。")
		}
		var msg message
		if b.demo {
			text, ok := demoSticker(p.StickerID)
			if !ok || p.PackageID != "demo" {
				return nil, fail("invalid_params", "貼圖不存在。")
			}
			msg = b.demoSend(p.ChatID, text)
			msg.Kind = "sticker"
			msg.URL = "demo:" + p.StickerID
			msg.StickerID = p.StickerID
			msg.PackageID = p.PackageID
		} else {
			raw, err := b.catalog.StickerMessage(p.ChatID, p.PackageID, p.StickerID)
			if err != nil {
				return nil, fail("sticker_unavailable", "請重新載入並選擇已擁有的貼圖。")
			}
			raw.From = b.session.MID
			raw.ToType = toType(p.ChatID)
			raw.HasContent = true
			raw.CreatedTime = json.Number(strconv.FormatInt(time.Now().UnixMilli(), 10))
			b.sequence++
			sent, err := b.api().SendMessage(b.sequence, raw)
			if err != nil {
				return nil, err
			}
			if sent == nil || sent.ID == "" {
				return nil, fail("send_uncertain", "無法確認貼圖是否已傳送，請先查看聊天記錄。")
			}
			raw.ID = sent.ID
			msg = b.convertMessage(raw)
		}
		b.acceptMessage(msg, true)
		return map[string]any{"message": msg}, nil
	case "files.send":
		var p struct {
			ChatID string `json:"chat_id"`
			Path   string `json:"path"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err
		}
		if !b.validChat(p.ChatID) {
			return nil, fail("invalid_params", "請選擇有效的聊天。")
		}
		var msg message
		if b.demo {
			data, err := attachments.ReadFile(p.Path)
			if err != nil {
				return nil, fail("file_error", err.Error())
			}
			msg = b.demoSend(p.ChatID, "[示範檔案] "+filepath.Base(p.Path))
			msg.Kind = "file"
			msg.Filename = filepath.Base(p.Path)
			msg.Size = int64(len(data))
		} else {
			prepared, err := attachments.PrepareFile(b.ctx, b.api(), p.ChatID, p.Path)
			if err != nil {
				if errors.Is(err, attachments.ErrFileSize) || errors.Is(err, attachments.ErrMediaFlow) {
					return nil, fail("file_error", err.Error())
				}
				return nil, err
			}
			raw, err := b.sendPayload(p.ChatID, prepared.ContentType, prepared.Metadata, prepared.Payload)
			if err != nil {
				return nil, err
			}
			msg = b.convertMessage(raw)
			msg.Filename = prepared.FileName
			msg.Size = prepared.Size
			msg.Text = "[檔案] " + prepared.FileName
		}
		b.acceptMessage(msg, true)
		return map[string]any{"message": msg}, nil
	case "images.send":
		// Sends a picture as a LINE photo message (contentType 1), not a file.
		var p struct {
			ChatID string `json:"chat_id"`
			Path   string `json:"path"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err
		}
		if !b.validChat(p.ChatID) || p.Path == "" {
			return nil, fail("invalid_params", "請選擇有效的聊天與圖片。")
		}
		var msg message
		if b.demo {
			data, err := attachments.ReadFile(p.Path)
			if err != nil {
				return nil, fail("file_error", err.Error())
			}
			cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
			if err != nil {
				return nil, fail("file_error", attachments.ErrNotImage.Error())
			}
			msg = b.demoSend(p.ChatID, "[照片]")
			msg.Kind = "image"
			msg.URL = "file://" + p.Path
			msg.Width, msg.Height = cfg.Width, cfg.Height
		} else {
			prepared, err := attachments.PrepareImage(b.ctx, b.api(), p.ChatID, p.Path, nil)
			if err != nil {
				if errors.Is(err, attachments.ErrFileSize) || errors.Is(err, attachments.ErrNotImage) {
					return nil, fail("file_error", err.Error())
				}
				return nil, err
			}
			var raw *line.Message
			if prepared.Plain {
				raw, err = b.sendPlain(p.ChatID, prepared.ContentType, prepared.Metadata)
				if err == nil {
					if upErr := attachments.PlainUpload(b.api(), raw.ID, prepared.PlainData); upErr != nil {
						b.notice("照片訊息已送出，但圖片上傳失敗；對方可能看不到照片。")
					}
				}
			} else {
				raw, err = b.sendPayload(p.ChatID, prepared.ContentType, prepared.Metadata, prepared.Payload)
			}
			if err != nil {
				return nil, err
			}
			msg = b.convertMessage(raw)
			msg.Width, msg.Height = prepared.Width, prepared.Height
			if prepared.PlainData != nil {
				b.cacheImage(raw.ID, prepared.PlainData)
			} else if data, readErr := attachments.ReadFile(p.Path); readErr == nil {
				b.cacheImage(raw.ID, data)
			}
		}
		b.acceptMessage(msg, true)
		return map[string]any{"message": msg}, nil
	case "images.fetch":
		// Downloads (and decrypts) a photo into memory and returns it as
		// base64 so the UI never sees LINE credentials or key material.
		var p struct {
			ChatID    string `json:"chat_id"`
			MessageID string `json:"message_id"`
			Preview   bool   `json:"preview"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err
		}
		if !b.hasMessage(p.ChatID, p.MessageID) {
			return nil, fail("invalid_params", "照片不屬於此聊天。")
		}
		if data, ok := b.imageCache[p.MessageID]; ok {
			return map[string]any{"data": base64.StdEncoding.EncodeToString(data)}, nil
		}
		if b.demo {
			return nil, fail("unsupported", "示範模式沒有 LINE 照片。")
		}
		raw := b.rawMessages[p.MessageID]
		data, err := attachments.DownloadImage(b.ctx, b.api(), raw, b.decryptedPayloads[p.MessageID], p.Preview)
		if err != nil && p.Preview {
			data, err = attachments.DownloadImage(b.ctx, b.api(), raw, b.decryptedPayloads[p.MessageID], false)
		}
		if err != nil {
			if errors.Is(err, attachments.ErrAuthentication) {
				return nil, fail("file_authentication", "照片驗證失敗，已停止顯示。")
			}
			if isAuthError(err) {
				return nil, err
			}
			return nil, fail("image_unavailable", "無法下載照片，可能已過期。")
		}
		if !p.Preview {
			b.cacheImage(p.MessageID, data)
		}
		return map[string]any{"data": base64.StdEncoding.EncodeToString(data)}, nil
	case "files.download":
		var p struct {
			ChatID    string `json:"chat_id"`
			MessageID string `json:"message_id"`
			Path      string `json:"path"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err
		}
		if !b.hasMessage(p.ChatID, p.MessageID) {
			return nil, fail("invalid_params", "附件不屬於此聊天。")
		}
		if b.demo {
			return nil, fail("unsupported", "示範模式不會下載 LINE 的附件。")
		}
		raw := b.rawMessages[p.MessageID]
		err := attachments.DownloadFile(b.ctx, b.api(), raw, b.decryptedPayloads[p.MessageID], p.Path)
		if err != nil {
			if errors.Is(err, attachments.ErrAuthentication) {
				return nil, fail("file_authentication", "附件驗證失敗，已停止下載。")
			}
			return nil, err
		}
		return map[string]string{"path": p.Path}, nil
	}
	return nil, fmt.Errorf("unknown attachment method")
}

// cacheImage keeps recently viewed or sent photos in memory (≈64 MiB cap) so
// reopening a chat does not download them again.
func (b *backend) cacheImage(id string, data []byte) {
	if b.imageCache == nil {
		b.imageCache = map[string][]byte{}
	}
	total := len(data)
	for _, v := range b.imageCache {
		total += len(v)
	}
	if total > 64<<20 {
		b.imageCache = map[string][]byte{}
	}
	b.imageCache[id] = data
}

func toType(chatID string) int {
	switch midKind(chatID) {
	case 'c':
		return 2
	case 'r':
		return 1
	}
	return 0
}
