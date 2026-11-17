package main

import (
	"context"
	"encoding/json"
	"github.com/beeper/line/pkg/line"
	"time"
)

type streamData struct{ event, data string }

// resyncAfter is how long the stream may have been silent before a reconnect
// starts over from LINE's current state. LINE replays every operation after
// the revision it is given: after hours or days offline that is thousands of
// messages, all of which the chat list (unread counts, last message) already
// accounts for, and it used to bury the UI in updates and notifications.
const resyncAfter = 5 * time.Minute

// wallNow is the wall clock without Go's monotonic reading. The monotonic
// clock stops while the computer is suspended, which would hide exactly the
// gap resyncAfter looks for.
func wallNow() time.Time { return time.Now().Round(0) }

func (b *backend) stopStream() {
	b.streamGeneration++
	if b.streamCancel != nil {
		b.streamCancel()
		b.streamCancel = nil
	}
	b.retryAt = time.Time{}
}

// needsResync reports whether the next stream must start from LINE's current
// revision: none is known yet, or the stream has been silent for too long.
func (b *backend) needsResync() bool {
	return b.resync || (!b.streamSeen.IsZero() && wallNow().Sub(b.streamSeen) > resyncAfter)
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
	revision := b.revision
	resync := b.needsResync()
	b.workers.Add(1)
	go func() {
		defer b.workers.Done()
		if resync {
			latest, err := client.GetLastOpRevisionContext(ctx)
			if err != nil {
				if ctx.Err() == nil {
					b.enqueue(ctx, workerEvent{generation, "stream_error", err})
				}
				return
			}
			revision = latest
			// Queued ahead of anything the stream delivers, so the chat
			// list is refreshed before newer operations are applied.
			b.enqueue(ctx, workerEvent{generation, "stream_resync", latest})
		}
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
		if !b.streamOpened.IsZero() && time.Since(b.streamOpened) > 30*time.Second {
			// LINE ends the long poll from time to time. A stream that had
			// been up for a while is reconnected at once, without leaving
			// "ready" (which would reject every request meanwhile). Only one
			// such attempt: if it fails too, the usual backoff applies.
			b.streamOpened = time.Time{}
			b.startStream()
			return
		}
		b.streamOpened = time.Time{}
		if b.retryDelay == 0 {
			b.retryDelay = time.Second
		} else {
			b.retryDelay = min(b.retryDelay*2, 30*time.Second)
		}
		b.retryAt = time.Now().Add(b.retryDelay)
		b.sessionEvent("connecting", "連線暫時中斷，正在重新連接…")
		return
	}
	if ev.kind == "stream_resync" {
		latest, _ := ev.data.(int64)
		b.revision, b.resync, b.streamSeen = latest, false, wallNow()
		// The operations of the gap are skipped, and with them every message
		// that was retracted or read meanwhile. Nothing cached can be trusted
		// any more: start over. The chat list comes from the snapshot and
		// conversations are loaded again when they are opened; the UI is told
		// first so it drops its own copies.
		b.messages = map[string][]message{}
		b.seen = map[string]struct{}{}
		b.seenOrder = nil
		b.rawMessages, b.decryptedPayloads = nil, nil
		b.pendingMessages = nil
		b.media.close()
		for i := range b.data.Chats {
			b.data.Chats[i].UpdatedAt, b.data.Chats[i].LastMessage = 0, ""
		}
		b.emit("resync", struct{}{})
		b.syncSnapshot()
		return
	}
	data, ok := ev.data.(streamData)
	if !ok {
		return
	}
	b.streamSeen = wallNow()
	switch data.event {
	case line.SSEEventOpen:
		b.retryDelay = 0
		b.streamOpened = time.Now()
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
		if err == nil && revision > b.revision {
			b.revision = revision
		}
	case "fullSync":
		var full struct {
			NextRevision json.Number `json:"nextRevision"`
		}
		if json.Unmarshal([]byte(data.data), &full) == nil {
			if revision, err := full.NextRevision.Int64(); err == nil && revision > b.revision {
				b.revision = revision
			}
		}
		b.syncSnapshot()
	}
}

// syncSnapshot refreshes the chat list and, when that fails for a reason
// other than the login, tries again later (tick). After a resync the snapshot
// is the only carrier of what happened while offline, so giving up after one
// failed request would leave those messages invisible.
func (b *backend) syncSnapshot() {
	err := b.refreshSnapshot()
	if err == nil {
		return
	}
	b.handleAuthError(err)
	if b.session == nil {
		return
	}
	if b.snapshotDelay == 0 {
		b.snapshotDelay = 5 * time.Second
		b.notice("聊天清單暫時無法同步，稍後自動重試。")
	} else {
		b.snapshotDelay = min(b.snapshotDelay*2, 2*time.Minute)
	}
	b.snapshotRetry = time.Now().Add(b.snapshotDelay)
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
			b.media.drop(mediaKey(messageID, true))
			b.media.drop(mediaKey(messageID, false))
			for j := range b.data.Chats {
				c := &b.data.Chats[j]
				if c.ID == cid && c.UpdatedAt <= m.Time {
					c.LastMessage = b.preview(*c, *m)
				}
			}
			// A message still waiting in the current batch must go out as
			// the placeholder too, or the UI would show the retracted text.
			for k := range b.pendingMessages {
				if b.pendingMessages[k].ID == messageID {
					b.pendingMessages[k] = *m
				}
			}
			b.emit("unsent", map[string]any{"chat_id": cid, "message_id": messageID})
			b.touchChat(cid)
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
			b.obsTokens = nil // the media token dies with the access token
			b.saveSession()
			b.startStream()
		}
	}
	if !b.retryAt.IsZero() && !now.Before(b.retryAt) {
		b.startStream()
	}
	if !b.snapshotRetry.IsZero() && !now.Before(b.snapshotRetry) {
		b.snapshotRetry = time.Time{}
		b.syncSnapshot()
	}
}
