package main

import (
	"context"
	"encoding/json"
	"io"
	"strconv"
	"testing"
	"time"

	"github.com/beeper/line/pkg/line"
)

// A page from getPreviousMessagesV2WithRequest can hold the message it was
// asked for; only the chat's earlier messages are kept, oldest first.
func TestOlderOnlyKeepsTheChatsEarlierMessages(t *testing.T) {
	me, peer, other := peerMID(9), peerMID(1), peerMID(2)
	b := &backend{session: &savedSession{MID: me}}
	raw := []*line.Message{
		{ID: "30", From: peer, To: me, CreatedTime: "3000", Text: "the message asked for"},
		{ID: "12", From: peer, To: me, CreatedTime: "1200", Text: "b"},
		nil,
		{ID: "11", From: me, To: peer, CreatedTime: "1100", Text: "a"},
		{ID: "13", From: other, To: me, CreatedTime: "1300", Text: "another chat"},
		{ID: "40", From: peer, To: me, CreatedTime: "4000", Text: "newer"},
	}
	got := olderOnly(raw, peer, "30", 3000, b.convertMessage)
	if len(got) != 2 || got[0].ID != "11" || got[1].ID != "12" || got[0].ChatID != peer {
		t.Fatalf("unexpected page: %+v", got)
	}
}

// Older pages go in front of what the core holds, so the end stays the newest
// message (marking a chat read uses it), and the cap keeps the newest ones.
func TestRememberOlderPutsPagesInFront(t *testing.T) {
	chatID := peerMID(1)
	b := &backend{messages: map[string][]message{chatID: {{ID: "10", ChatID: chatID, Time: 10}, {ID: "11", ChatID: chatID, Time: 11}}}}
	fresh := b.rememberOlder(chatID, []message{{ID: "5", ChatID: chatID, Time: 5}, {ID: "6", ChatID: chatID, Time: 6}, {ID: "10", ChatID: chatID, Time: 10}})
	list := b.messages[chatID]
	if len(fresh) != 2 || len(list) != 4 || list[0].ID != "5" || list[2].ID != "10" || list[3].ID != "11" {
		t.Fatalf("unexpected order: fresh %+v, list %+v", fresh, list)
	}
	if _, ok := b.seen["5"]; !ok || !b.hasMessage(chatID, "6") {
		t.Fatal("an older message cannot be found again (its photo would not open)")
	}

	full := make([]message, historyLimit-1)
	for i := range full {
		full[i] = message{ID: strconv.Itoa(1000 + i), ChatID: chatID, Time: int64(1000 + i)}
	}
	b.messages[chatID] = full
	kept := b.rememberOlder(chatID, []message{{ID: "1", ChatID: chatID, Time: 1}, {ID: "2", ChatID: chatID, Time: 2}, {ID: "3", ChatID: chatID, Time: 3}})
	if len(kept) != 1 || kept[0].ID != "3" || len(b.messages[chatID]) != historyLimit {
		t.Fatalf("over the cap: kept %+v, %d held", kept, len(b.messages[chatID]))
	}
}

// messages.before checks its parameters; a demo chat has nothing older.
func TestMessagesBeforeRequest(t *testing.T) {
	in, writer := io.Pipe()
	reader, out := io.Pipe()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- serve(ctx, in, out, true, nil); out.Close() }()
	defer reader.Close()
	defer writer.Close()
	decoder := json.NewDecoder(reader)
	call := func(id, method string, params map[string]any) map[string]any {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"id": id, "method": method, "params": params})
		if _, err := writer.Write(append(body, '\n')); err != nil {
			t.Fatal(err)
		}
		for {
			var envelope map[string]any
			if err := decoder.Decode(&envelope); err != nil {
				t.Fatal(err)
			}
			if envelope["id"] == id {
				return envelope
			}
		}
	}
	call("1", "initialize", map[string]any{})
	for n, params := range []map[string]any{
		{"chat_id": "demo-yiting", "before_id": "", "before_time": 1000},
		{"chat_id": "demo-yiting", "before_id": "x", "before_time": 0},
		{"chat_id": "demo-yiting", "before_id": "x", "before_time": 1000, "limit": 500},
		{"chat_id": "nobody", "before_id": "x", "before_time": 1000},
	} {
		if reply := call("bad-"+strconv.Itoa(n), "messages.before", params); reply["ok"] != false {
			t.Fatalf("accepted %v", params)
		}
	}
	reply := call("2", "messages.before", map[string]any{"chat_id": "demo-yiting", "before_id": "demo-1", "before_time": 1000})
	result, _ := reply["result"].(map[string]any)
	if reply["ok"] != true || result["more"] != false || len(result["messages"].([]any)) != 0 {
		t.Fatalf("unexpected demo answer: %v", reply)
	}
	call("3", "shutdown", map[string]any{})
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal("shutdown timed out")
	}
}

// A video message: its length, size and shape come from the metadata LINE
// sends (DURATION and FILE_SIZE, as seen on a real account).
func TestVideoMessage(t *testing.T) {
	me, peer := peerMID(9), peerMID(1)
	b := &backend{session: &savedSession{MID: me}}
	raw := &line.Message{ID: "v1", From: peer, To: me, ContentType: 2, CreatedTime: "1000",
		ContentMetadata: map[string]string{"DURATION": "46332", "FILE_SIZE": "12188262", "OID": "x", "SID": "emv", "MEDIA_THUMB_INFO": `{"width":720,"height":1280}`}}
	m := b.convertMessage(raw)
	if m.Kind != "video" || m.Text != "[影片]" || m.Duration != 46332 || m.Size != 12188262 || m.Width != 720 || m.Height != 1280 {
		t.Fatalf("unexpected video message: %+v", m)
	}
}
