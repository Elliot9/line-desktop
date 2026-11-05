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
		b.sessionEvent("ready", "已連線至 LINE。")
	case "operation":
		var op line.Operation
		if json.Unmarshal([]byte(data.data), &op) != nil {
			return
		}
		if op.Message != nil && (op.Type == 25 || op.Type == 26) {
			b.acceptMessage(b.convertMessage(op.Message), true)
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
