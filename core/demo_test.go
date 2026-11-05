package main

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"testing"

	"github.com/beeper/line/pkg/line"
)

func TestDemoStickerIDs(t *testing.T) {
	if text, ok := demoSticker("bear-0"); !ok || text != "出發！" {
		t.Fatal("first demo sticker missing")
	}
	for _, id := range []string{"bear-8", "bear--1", "bear-x", "hello", ""} {
		if _, ok := demoSticker(id); ok {
			t.Fatalf("accepted invalid demo sticker %q", id)
		}
	}
}

func TestGroupMembersAreValidSortedAndCapped(t *testing.T) {
	mids := line.FlexibleMidMap{"not-a-mid": true}
	for i := 0; i < maxListedMembers+5; i++ {
		mids[fmt.Sprintf("u%032x", maxListedMembers+5-i)] = true
	}
	members := groupMembers(mids)
	if len(members) != maxListedMembers {
		t.Fatalf("expected %d members, got %d", maxListedMembers, len(members))
	}
	for i, mid := range members {
		if !validMID(mid) || i > 0 && members[i-1] >= mid {
			t.Fatal("members must be valid and sorted")
		}
	}
}

func TestDemoSeedMatchesReferenceDesign(t *testing.T) {
	b := &backend{out: json.NewEncoder(io.Discard), demo: true, messages: map[string][]message{}, seen: map[string]struct{}{}}
	b.seedDemo()
	if b.data.Profile.Name != "林以安" || len(b.data.Chats) != 6 {
		t.Fatal("demo profile or chats differ from index.html")
	}
	weekend := b.data.Chats[0]
	if weekend.ID != "demo-weekend" || weekend.MemberCount != 4 || weekend.LastMessage != "林子豪：[貼圖]" {
		t.Fatalf("unexpected weekend chat %+v", weekend)
	}
	kinds := map[string]bool{}
	for _, m := range b.messages["demo-weekend"] {
		kinds[m.Kind] = true
	}
	for _, kind := range []string{"text", "image", "file", "sticker"} {
		if !kinds[kind] {
			t.Fatalf("weekend chat lacks %s message", kind)
		}
	}
}

func TestDebugRedactsTokensAndMIDs(t *testing.T) {
	mid := "u0123456789abcdef0123456789abcdef"
	token := "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlX2hlcmU"
	text := redact("login failed for " + mid + " with token " + token + ": HTTP 401")
	if strings.Contains(text, mid) || strings.Contains(text, "eyJ") || !strings.Contains(text, "HTTP 401") {
		t.Fatalf("redaction leaked or removed useful text: %q", text)
	}
	if kept := redact("Post https://line-chrome-gw.line-apps.com/api/talk/thrift/Talk: EOF"); !strings.Contains(kept, "line-chrome-gw.line-apps.com/api/talk/thrift/Talk") {
		t.Fatalf("host and path should stay readable: %q", kept)
	}
	if long := redact(strings.Repeat("字", 500)); len([]rune(long)) > 401 {
		t.Fatal("debug lines must be truncated")
	}
}
