package main

import (
	"strings"
	"testing"
)

func TestInlineEmojiExplicitReplace(t *testing.T) {
	// "hi😀X": 😀 is two UTF-16 units; the sticon covers "X" at unit 4.
	body := []byte(`{"text":"hi😀X","REPLACE":{"sticon":{"resources":[{"S":4,"E":5,"productId":"5ac1bfd5040ab15980c9b435","sticonId":"001"}]}}}`)
	text, runs := inlineEmoji("hi😀X", parseReplace(body, ""))
	if text != "hi😀\uFFFC" || len(runs) != 1 || runs[0].Start != 3 || runs[0].End != 4 {
		t.Fatalf("got %q %+v", text, runs)
	}
	if runs[0].URL != "https://stickershop.line-scdn.net/sticonshop/v1/sticon/5ac1bfd5040ab15980c9b435/android/001.png" {
		t.Fatalf("url %s", runs[0].URL)
	}
}

func TestInlineEmojiDerivedEMTVER3(t *testing.T) {
	text, runs := inlineEmoji("a\U00100001b", nil)
	if text != "a\uFFFCb" || len(runs) != 1 || !strings.HasSuffix(runs[0].URL, "/android/100101_k.png") {
		t.Fatalf("got %q %+v", text, runs)
	}
}

func TestInlineEmojiPlainText(t *testing.T) {
	text, runs := inlineEmoji("早安 ☀️", nil)
	if text != "早安 ☀️" || runs != nil {
		t.Fatalf("plain text changed: %q %+v", text, runs)
	}
}
