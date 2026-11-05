package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

// LINE_CORE_DEBUG=1 prints login and sync steps to stderr so a failing real
// login can be diagnosed. Long token-like strings containing digits (access
// tokens, MIDs, keys) are redacted; host names and API paths stay readable.
var debugEnabled = os.Getenv("LINE_CORE_DEBUG") == "1"
var secretPattern = regexp.MustCompile(`[A-Za-z0-9+/=_.\-]{24,}`)

func redact(text string) string {
	text = secretPattern.ReplaceAllStringFunc(text, func(word string) string {
		if strings.ContainsAny(word, "0123456789") {
			return "[redacted]"
		}
		return word
	})
	if len([]rune(text)) > 400 {
		text = string([]rune(text)[:400]) + "…"
	}
	return text
}

func debugf(format string, args ...any) {
	if !debugEnabled {
		return
	}
	fmt.Fprintf(os.Stderr, "%s line-core: %s\n", time.Now().Format("15:04:05"), redact(fmt.Sprintf(format, args...)))
}
