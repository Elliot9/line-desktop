package main

import (
	"context"
	"encoding/json"
	"github.com/beeper/line/pkg/line"
	"time"
)

type streamData struct{ event, data string }

func (b *backend) stopStream() {
	b.streamGeneration++
	if b.streamCancel != nil {
		b.streamCancel()
		b.streamCancel = nil
	}
	b.retryAt = time.Time{}
}

func (b *backend) startStream() {
	if b.session == nil || b.demo {
		return
	}
	b.stopStream()
	ctx, cancel := context.WithCancel(b.ctx)
	b.streamCancel = cancel
	generation := b.streamGeneration
	client := apiClient(ctx, b.session.AccessToken)
	revision := b.session.Revision
	b.workers.Add(1)
	go func() {
		defer b.workers.Done()
		err := client.ListenSSE(ctx, revision, func(event, data string) { b.enqueue(ctx, workerEvent{generation, "stream", streamData{event, data}}) })
		if ctx.Err() == nil {
			b.enqueue(ctx, workerEvent{generation, "stream_error", err})
		}
	}()
}

func (b *backend) handleStreamEvent(ev workerEvent) {
	if ev.generation != b.streamGeneration || b.session == nil {
		return
	}
	if ev.kind == "stream_error" {
		err, _ := ev.data.(error)
		if isAuthError(err) {
			b.handleAuthError(err)
			return
		}
		b.streamCancel = nil
		if b.retryDelay == 0 {
			b.retryDelay = time.Second
		} else {
			b.retryDelay = min(b.retryDelay*2, 30*time.Second)
		}
		b.retryAt = time.Now().Add(b.retryDelay)
		b.sessionEvent("connecting", "連線暫時中斷，正在重新連接…")
		return
	}
	data := ev.data.(streamData)
	switch data.event {
	case line.SSEEventOpen:
		b.retryDelay = 0
		if !b.pushPreserved {
			// Restored sessions skip login, so keep phone push here too.
			b.pushPreserved = true
			b.preservePhoneNotifications()
		}
		b.sessionEvent("ready", "已連線至 LINE。")
	case "operation":
		var op line.Operation
		if json.Unmarshal([]byte(data.data), &op) != nil {
			return
		}
		switch {
		case op.Message != nil && (op.Type == 25 || op.Type == 26):
			b.acceptMessage(b.convertMessage(op.Message), true)
		case op.Type == 55: // read receipt: param1 chat, param2 reader
			b.markReadBy(op.Param1, op.Param2, op.CreatedTime)
		case op.Type == 64 || op.Type == 65: // unsend: param1 chat, param2 message
			b.markUnsent(op.Param1, op.Param2)
		}
		revision, err := op.Revision.Int64()
		if err == nil && revision > b.session.Revision {
			b.session.Revision = revision
		}
	case "fullSync":
		var full struct {
			NextRevision json.Number `json:"nextRevision"`
		}
		if json.Unmarshal([]byte(data.data), &full) == nil {
			if revision, err := full.NextRevision.Int64(); err == nil && revision > b.session.Revision {
				b.session.Revision = revision
			}
		}
		if err := b.refreshSnapshot(); err != nil {
			b.handleAuthError(err)
			b.notice("聊天清單同步失敗，請稍後重新整理。")
		}
	}
}

// markReadBy records that reader has read chatID up to the receipt time. The UI
// derives "已讀" / "已讀 N" for the user's own messages from Chat.ReadBy.
func (b *backend) markReadBy(chatID, reader string, created json.Number) {
	ts, _ := created.Int64()
	if ts == 0 {
		ts = time.Now().UnixMilli()
	}
	if b.session != nil && reader == b.session.MID {
		return
	}
	// In 1:1 chats LINE reports the reader as param1 and the chat as param2
	// depending on direction; the chat is whichever ID we already know.
	if !b.knownChat(chatID) && b.knownChat(reader) {
		chatID, reader = reader, chatID
	}
	for i := range b.data.Chats {
		c := &b.data.Chats[i]
		if c.ID != chatID {
			continue
		}
		if c.ReadBy == nil {
			c.ReadBy = map[string]int64{}
		}
		if ts > c.ReadBy[reader] {
			c.ReadBy[reader] = ts
		}
		b.emit("read", map[string]any{"chat_id": chatID, "reader": reader, "time": ts})
		return
	}
}

func (b *backend) knownChat(id string) bool {
	for _, c := range b.data.Chats {
		if c.ID == id {
			return true
		}
	}
	return false
}

// markUnsent turns a message into LINE's "已收回訊息" placeholder.
func (b *backend) markUnsent(chatID, messageID string) {
	for cid, list := range b.messages {
		if chatID != "" && cid != chatID && !b.hasMessage(cid, messageID) {
			continue
		}
		for i := range list {
			if list[i].ID != messageID {
				continue
			}
			m := &list[i]
			m.Unsent = true
			m.Kind = "unsent"
			m.Text = ""
			m.URL = ""
			m.Emoji = nil
			delete(b.decryptedPayloads, messageID)
			for j := range b.data.Chats {
				c := &b.data.Chats[j]
				if c.ID == cid && c.UpdatedAt <= m.Time {
					c.LastMessage = b.preview(*c, *m)
				}
			}
			b.emit("unsent", map[string]any{"chat_id": cid, "message_id": messageID})
			b.emit("snapshot", b.data)
			return
		}
	}
	if chatID != "" {
		b.emit("unsent", map[string]any{"chat_id": chatID, "message_id": messageID})
	}
}

func (b *backend) tick() {
	if b.demo || b.session == nil {
		return
	}
	now := time.Now()
	if b.session.RefreshToken != "" && b.session.RefreshAt > 0 && now.Unix() >= b.session.RefreshAt {
		if err := refreshSaved(b.api(), b.session); err != nil {
			if isAuthError(err) {
				b.stop()
				b.clearData()
				b.sessionEvent("logged_out", "登入已過期，請重新登入。")
				return
			}
			b.session.RefreshAt = now.Add(time.Minute).Unix()
			b.notice("登入憑證暫時無法更新，一分鐘後重試。")
		} else {
			b.saveSession()
			b.startStream()
		}
	}
	if !b.retryAt.IsZero() && !now.Before(b.retryAt) {
		b.startStream()
	}
	if b.persistent && now.Sub(b.savedRevisionAt) > time.Minute {
		b.saveSession()
		b.savedRevisionAt = now
	}
}
