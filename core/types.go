package main

import (
	"encoding/json"
	"errors"
	"strings"
)

const maxEnvelope = 2 << 20
const maxText = 10000

type request struct {
	ID     string          `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}
type fault struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (f *fault) Error() string        { return f.Message }
func fail(code, message string) error { return &fault{code, message} }
func userError(err error) *fault {
	var f *fault
	if errors.As(err, &f) {
		return f
	}
	return &fault{"network", "LINE 連線失敗，請檢查網路後重試。若持續失敗，請重新登入。"}
}

type person struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status"`
	Avatar string `json:"avatar,omitempty"`
}
type chat struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Kind        string `json:"kind"`
	LastMessage string `json:"last_message"`
	Unread      int    `json:"unread"`
	UpdatedAt   int64  `json:"updated_at"`
	Avatar      string `json:"avatar,omitempty"`
	MemberCount int    `json:"member_count"`
}
type message struct {
	ID         string `json:"id"`
	ChatID     string `json:"chat_id"`
	SenderID   string `json:"sender_id"`
	SenderName string `json:"sender_name"`
	Text       string `json:"text"`
	Kind       string `json:"kind"`
	Mine       bool   `json:"mine"`
	Time       int64  `json:"time"`
	Status     string `json:"status"`
	Filename   string `json:"filename,omitempty"`
	Size       int64  `json:"size,omitempty"`
	URL        string `json:"url,omitempty"`
	StickerID  string `json:"sticker_id,omitempty"`
	PackageID  string `json:"package_id,omitempty"`
}
type snapshot struct {
	Profile  *person  `json:"profile"`
	Contacts []person `json:"contacts"`
	Chats    []chat   `json:"chats"`
}

func capabilities() map[string]bool {
	return map[string]bool{"text": true, "contacts": true, "groups": true, "stickers": true, "files": true, "images": false, "calls": false, "notes": false, "albums": false}
}
func params(raw json.RawMessage, out any) error {
	if len(raw) == 0 {
		raw = json.RawMessage(`{}`)
	}
	if len(raw) == 0 || raw[0] != '{' {
		return fail("invalid_params", "參數必須是 JSON 物件。")
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(out); err != nil {
		return fail("invalid_params", "參數格式不正確。")
	}
	return nil
}
func validMID(mid string) bool {
	if len(mid) != 33 || !strings.ContainsRune("ucr", rune(mid[0])) {
		return false
	}
	for _, c := range mid[1:] {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
