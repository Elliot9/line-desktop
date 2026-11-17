package main

import (
	"encoding/json"
	"testing"

	"github.com/beeper/line/pkg/line"
)

// The gateway's answer to getMessageReadRange, as seen on a real account
// (times are strings).
func TestReadRangesParseAndMerge(t *testing.T) {
	var data any
	reader, other, me := peerMID(1), peerMID(2), peerMID(9)
	chatID := peerMID(3)
	body := `[{"chatId":"` + chatID + `","ranges":{
		"` + reader + `":[{"startMessageId":"1","endMessageId":"5","startTime":"1000","endTime":"5000"},
		                  {"startMessageId":"7","endMessageId":"9","startTime":"7000","endTime":"9000"}],
		"` + other + `":[{"startMessageId":"1","endMessageId":"2","startTime":"1000","endTime":2000}],
		"` + me + `":[{"endTime":"9999"}]}},
		{"ranges":{}}, "junk"]`
	if err := json.Unmarshal([]byte(body), &data); err != nil {
		t.Fatal(err)
	}
	ranges := parseReadRanges(data)
	if len(ranges) != 1 || ranges[chatID][reader] != 9000 || ranges[chatID][other] != 2000 {
		t.Fatalf("unexpected ranges: %v", ranges)
	}

	b := &backend{session: &savedSession{MID: me}}
	b.data.Chats = []chat{{ID: chatID, Kind: "direct", ReadBy: map[string]int64{reader: 9500}}}
	merged := b.mergeReadBy(chatID, ranges[chatID])
	if merged[reader] != 9500 || merged[other] != 2000 {
		t.Fatalf("a read time moved back or was lost: %v", merged)
	}
	if _, ok := merged[me]; ok {
		t.Fatal("the user's own read range counts as a reader")
	}
	if b.mergeReadBy(peerMID(4), ranges[chatID]) != nil {
		t.Fatal("an unknown chat got read times")
	}
}

// A Letter Sealing file from LINE's own apps names itself only inside the
// encrypted payload; the bubble must still show the name and extension.
func TestSealedFileNameComesFromPayload(t *testing.T) {
	me, peer := peerMID(9), peerMID(1)
	b := &backend{session: &savedSession{MID: me}}
	b.decryptedPayloads = map[string][]byte{"f1": []byte(`{"fileName":"散步路線提案.pdf","keyMaterial":"k"}`)}
	raw := &line.Message{ID: "f1", From: peer, To: me, ContentType: 14, CreatedTime: "1000",
		Chunks: []string{"a", "b", "c", "d", "e"}, ContentMetadata: map[string]string{"FILE_SIZE": "2468", "FILE_EXPIRE_TIMESTAMP": "1900000000000"}}
	m := b.convertMessage(raw)
	if m.Kind != "file" || m.Filename != "散步路線提案.pdf" || m.Size != 2468 || m.Expires != 1900000000000 {
		t.Fatalf("unexpected file message: %+v", m)
	}
	// Metadata, when present, still wins (files this app sends carry both).
	raw.ContentMetadata["FILE_NAME"] = "報告.pdf"
	if m := b.convertMessage(raw); m.Filename != "報告.pdf" {
		t.Fatalf("metadata name ignored: %+v", m)
	}
}
