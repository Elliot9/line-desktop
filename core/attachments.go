package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strconv"
	"time"

	"matrix-line/core/internal/attachments"
)

func (b *backend) handleAttachment(req request) (any, error) {
	switch req.Method {
	case "stickers.list":
		if err := params(req.Params, &struct{}{}); err != nil {
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

func toType(chatID string) int {
	switch midKind(chatID) {
	case 'c':
		return 2
	case 'r':
		return 1
	}
	return 0
}
