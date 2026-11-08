package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/beeper/line/pkg/e2ee"
	"github.com/beeper/line/pkg/line"
	"matrix-line/core/internal/attachments"
)

const version = "matrix-line-core 0.1.0 (beeper/line a9cefaf244d17f7bb6fb9685cdfd3ccf993c3293)"

type workerEvent struct {
	generation uint64
	kind       string
	data       any
}
type backend struct {
	ctx               context.Context
	cancel            context.CancelFunc
	out               *json.Encoder
	demo              bool
	store             sessionStore
	events            chan workerEvent
	workers           sync.WaitGroup
	generation        uint64
	loginCancel       context.CancelFunc
	streamCancel      context.CancelFunc
	streamGeneration  uint64
	retryAt           time.Time
	retryDelay        time.Duration
	state             string
	initialized       bool
	persistent        bool
	stored            bool
	session           *savedSession
	previousSession   *savedSession
	crypto            *e2ee.Manager
	data              snapshot
	messages          map[string][]message
	seen              map[string]struct{}
	seenOrder         []string
	sequence          int64
	rawMessages       map[string]*line.Message
	decryptedPayloads map[string][]byte
	catalog           *attachments.Catalog
	pendingReply      string
	pushPreserved     bool
	imageCache        map[string][]byte
	region            string
	savedRevisionAt   time.Time
}

func main() {
	demo := flag.Bool("demo", false, "Use deterministic offline demo data")
	showVersion := flag.Bool("version", false, "Print version")
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		return
	}
	log.SetOutput(io.Discard)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := serve(ctx, os.Stdin, os.Stdout, *demo, newKeyringStore()); err != nil {
		fmt.Fprintln(os.Stderr, "LINE core stopped because the private pipe failed.")
		os.Exit(1)
	}
}
func serve(parent context.Context, in io.Reader, out io.Writer, demo bool, store sessionStore) error {
	ctx, cancel := context.WithCancel(parent)
	b := &backend{ctx: ctx, cancel: cancel, out: json.NewEncoder(out), demo: demo, store: store, events: make(chan workerEvent, 128), state: "logged_out", sequence: time.Now().UnixMilli(), messages: map[string][]message{}, seen: map[string]struct{}{}, data: snapshot{Contacts: []person{}, Chats: []chat{}}}
	defer func() { cancel(); b.stop(); b.workers.Wait() }()
	requests := make(chan request)
	inputErrors := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(in)
		scanner.Buffer(make([]byte, 4096), maxEnvelope)
		defer close(requests)
		for scanner.Scan() {
			var req request
			if err := json.Unmarshal(scanner.Bytes(), &req); err != nil || req.ID == "" || len(req.ID) > 128 || req.Method == "" {
				req = request{ID: req.ID, Method: "!invalid"}
			}
			select {
			case requests <- req:
			case <-ctx.Done():
				return
			}
		}
		if scanner.Err() != nil {
			inputErrors <- scanner.Err()
		}
		cancel()
	}()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			select {
			case <-inputErrors:
				b.reply("", nil, fail("input_too_large", "輸入超過 2 MiB 限制。"))
			default:
			}
			return nil
		case req, ok := <-requests:
			if !ok {
				return nil
			}
			result, err, done := b.handle(req)
			if err := b.reply(req.ID, result, err); err != nil {
				return err
			}
			if done {
				return nil
			}
		case ev := <-b.events:
			b.handleEvent(ev)
		case <-ticker.C:
			b.tick()
		}
	}
}
func (b *backend) emit(event string, data any) {
	if b.out.Encode(map[string]any{"event": event, "data": data}) != nil {
		b.cancel()
	}
}
func (b *backend) reply(id string, result any, err error) error {
	if err != nil {
		return b.out.Encode(map[string]any{"id": id, "ok": false, "error": userError(err)})
	}
	if result == nil {
		result = struct{}{}
	}
	return b.out.Encode(map[string]any{"id": id, "ok": true, "result": result})
}
func (b *backend) sessionEvent(state, text string) {
	b.state = state
	b.emit("session", map[string]any{"state": state, "message": text, "profile": b.data.Profile, "persistent": b.persistent})
}
func (b *backend) notice(text string) {
	b.emit("notice", map[string]string{"level": "warning", "message": text})
}
func (b *backend) stop() {
	if b.session != nil {
		b.previousSession = b.session
	}
	b.generation++
	if b.loginCancel != nil {
		b.loginCancel()
		b.loginCancel = nil
	}
	b.stopStream()
	if b.crypto != nil {
		b.crypto.Close()
		b.crypto = nil
	}
	b.session = nil
	b.pushPreserved = false
	b.persistent = false
}
func (b *backend) clearData() {
	b.data = snapshot{Contacts: []person{}, Chats: []chat{}}
	b.messages = map[string][]message{}
	b.seen = map[string]struct{}{}
	b.seenOrder = nil
	b.rawMessages = nil
	b.decryptedPayloads = nil
	b.catalog = nil
	b.region = ""
	b.emit("snapshot", b.data)
}
func (b *backend) enqueue(ctx context.Context, ev workerEvent) {
	select {
	case b.events <- ev:
	case <-ctx.Done():
	}
}
func (b *backend) handle(req request) (any, error, bool) {
	if req.Method == "!invalid" {
		return nil, fail("invalid_request", "請求必須包含 id、method 與有效 JSON。"), false
	}
	if req.Method == "shutdown" {
		return nil, nil, true
	}
	if req.Method == "initialize" {
		if err := params(req.Params, &struct{}{}); err != nil {
			return nil, err, false
		}
		if !b.initialized {
			b.initialized = true
			if b.demo {
				b.seedDemo()
			} else {
				b.beginRestore()
			}
		}
		mode := "live"
		if b.demo {
			mode = "demo"
		}
		return map[string]any{"mode": mode, "capabilities": capabilities()}, nil, false
	}
	if !b.initialized {
		return nil, fail("not_initialized", "請先初始化核心。"), false
	}
	switch req.Method {
	case "login.qr", "login.email":
		var p struct {
			Email    string `json:"email"`
			Password string `json:"password"`
		}
		var err error
		if req.Method == "login.qr" {
			err = params(req.Params, &struct{}{})
		} else {
			err = params(req.Params, &p)
			if err == nil && (len(p.Email) > 320 || !strings.Contains(p.Email, "@") || p.Password == "" || len(p.Password) > 1024) {
				err = fail("invalid_params", "請輸入有效的電子郵件與密碼。")
			}
		}
		if err != nil {
			return nil, err, false
		}
		b.beginLogin(req.Method, p.Email, p.Password)
		return nil, nil, false
	case "login.cancel":
		if err := params(req.Params, &struct{}{}); err != nil {
			return nil, err, false
		}
		b.stop()
		b.clearData()
		b.sessionEvent("logged_out", "已取消登入。")
		return nil, nil, false
	case "logout":
		b.stop()
		b.previousSession = nil
		b.clearData()
		var err error
		if !b.demo && b.stored {
			err = b.store.Clear(b.ctx)
			if err == nil {
				b.stored = false
			}
		}
		b.sessionEvent("logged_out", "已登出。")
		if err != nil {
			b.notice(userError(err).Message)
		}
		return nil, err, false
	}
	if b.state != "ready" || b.data.Profile == nil {
		return nil, fail("not_ready", "請先登入並等待連線完成。"), false
	}
	switch req.Method {
	case "chats.refresh":
		if err := params(req.Params, &struct{}{}); err != nil {
			return nil, err, false
		}
		if b.demo {
			b.emit("snapshot", b.data)
			return nil, nil, false
		}
		err := b.refreshSnapshot()
		b.handleAuthError(err)
		return nil, err, false
	case "messages.list":
		var p struct {
			ChatID string `json:"chat_id"`
			Limit  int    `json:"limit"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) || p.Limit < 0 || p.Limit > 100 {
			return nil, fail("invalid_params", "聊天或訊息數量無效。"), false
		}
		if p.Limit == 0 {
			p.Limit = 50
		}
		var list []message
		var err error
		if b.demo {
			list = b.messages[p.ChatID]
		} else {
			list, err = b.listMessages(p.ChatID, p.Limit)
		}
		if list == nil {
			list = []message{}
		}
		if len(list) > p.Limit {
			list = list[len(list)-p.Limit:]
		}
		b.handleAuthError(err)
		return map[string]any{"messages": list}, err, false
	case "messages.send":
		var p struct {
			ChatID  string `json:"chat_id"`
			Text    string `json:"text"`
			ReplyTo string `json:"reply_to"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) || strings.TrimSpace(p.Text) == "" || len([]rune(p.Text)) > maxText {
			return nil, fail("invalid_params", "請選擇有效聊天，並輸入 1 至 10000 字的訊息。"), false
		}
		if p.ReplyTo != "" && !b.hasMessage(p.ChatID, p.ReplyTo) {
			return nil, fail("invalid_params", "要回覆的訊息不在此聊天。"), false
		}
		var msg message
		var err error
		if b.demo {
			msg = b.demoSend(p.ChatID, p.Text)
			msg.ReplyTo = p.ReplyTo
		} else {
			msg, err = b.sendReply(p.ChatID, p.Text, p.ReplyTo)
		}
		if err == nil {
			b.acceptMessage(msg, true)
		} else {
			b.handleAuthError(err)
		}
		return map[string]any{"message": msg}, err, false
	case "messages.read":
		var p struct {
			ChatID    string `json:"chat_id"`
			MessageID string `json:"message_id"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) || !b.hasMessage(p.ChatID, p.MessageID) {
			return nil, fail("invalid_params", "訊息不屬於此聊天。"), false
		}
		var err error
		if !b.demo {
			err = b.api().SendChatChecked(p.ChatID, p.MessageID)
		}
		if err == nil {
			for i := range b.data.Chats {
				if b.data.Chats[i].ID == p.ChatID {
					b.data.Chats[i].Unread = 0
				}
			}
			b.emit("snapshot", b.data)
		}
		b.handleAuthError(err)
		return nil, err, false
	case "messages.unsend":
		var p struct {
			ChatID    string `json:"chat_id"`
			MessageID string `json:"message_id"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) || !b.hasMessage(p.ChatID, p.MessageID) {
			return nil, fail("invalid_params", "訊息不屬於此聊天。"), false
		}
		for _, m := range b.messages[p.ChatID] {
			if m.ID == p.MessageID && (!m.Mine || time.Since(time.UnixMilli(m.Time)) > 24*time.Hour) {
				return nil, fail("unsend_unavailable", "只能收回自己在 24 小時內傳送的訊息。"), false
			}
		}
		var err error
		if !b.demo {
			b.sequence++
			err = b.api().UnsendMessage(b.sequence, p.MessageID)
		}
		if err == nil {
			b.markUnsent(p.ChatID, p.MessageID)
		}
		b.handleAuthError(err)
		return nil, err, false
	case "chats.delete":
		// LINE「刪除聊天室」: removes history up to the newest message on the
		// server (sendChatRemoved) and drops the row from the list.
		var p struct {
			ChatID string `json:"chat_id"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) {
			return nil, fail("invalid_params", "聊天無效。"), false
		}
		var err error
		if !b.demo {
			lastID, lastTime := "", int64(0)
			if list := b.messages[p.ChatID]; len(list) > 0 {
				lastID, lastTime = list[len(list)-1].ID, list[len(list)-1].Time
			}
			b.sequence++
			err = b.api().SendChatRemovedContext(b.ctx, b.sequence, p.ChatID, lastID, lastTime)
		}
		if err == nil {
			delete(b.messages, p.ChatID)
			kept := b.data.Chats[:0]
			for _, c := range b.data.Chats {
				if c.ID != p.ChatID {
					kept = append(kept, c)
				}
			}
			b.data.Chats = kept
			b.emit("snapshot", b.data)
		}
		b.handleAuthError(err)
		return nil, err, false
	case "chats.read_all":
		// 「全部標為已讀」 sends a read receipt for the newest message of each
		// unread chat; it is an explicit user action.
		if err := params(req.Params, &struct{}{}); err != nil {
			return nil, err, false
		}
		var firstErr error
		for i := range b.data.Chats {
			c := &b.data.Chats[i]
			if c.Unread == 0 {
				continue
			}
			if list := b.messages[c.ID]; len(list) > 0 && !b.demo {
				if err := b.api().SendChatChecked(c.ID, list[len(list)-1].ID); err != nil && firstErr == nil {
					firstErr = err
					continue
				}
			}
			c.Unread = 0
		}
		b.emit("snapshot", b.data)
		b.handleAuthError(firstErr)
		return nil, firstErr, false
	case "stickers.list", "messages.sticker", "files.send", "files.download", "images.send", "images.fetch":
		result, err := b.handleAttachment(req)
		b.handleAuthError(err)
		return result, err, false
	default:
		return nil, fail("unknown_method", "不支援的核心命令。"), false
	}
}
func (b *backend) validChat(id string) bool {
	if b.demo {
		for _, c := range b.data.Chats {
			if c.ID == id {
				return true
			}
		}
		for _, c := range b.data.Contacts {
			if c.ID == id {
				return true
			}
		}
		return false
	}
	return validMID(id)
}
func (b *backend) hasMessage(chatID, id string) bool {
	if id == "" {
		return false
	}
	for _, m := range b.messages[chatID] {
		if m.ID == id {
			return true
		}
	}
	return false
}
func (b *backend) acceptMessage(m message, notify bool) {
	if m.ID == "" || m.ChatID == "" {
		return
	}
	if _, ok := b.seen[m.ID]; ok {
		return
	}
	b.seen[m.ID] = struct{}{}
	b.seenOrder = append(b.seenOrder, m.ID)
	if len(b.seenOrder) > 10000 {
		oldest := b.seenOrder[0]
		delete(b.seen, oldest)
		delete(b.rawMessages, oldest)
		delete(b.decryptedPayloads, oldest)
		b.seenOrder = b.seenOrder[1:]
	}
	list := append(b.messages[m.ChatID], m)
	if len(list) > 500 {
		list = list[len(list)-500:]
	}
	b.messages[m.ChatID] = list
	found := false
	for i := range b.data.Chats {
		c := &b.data.Chats[i]
		if c.ID == m.ChatID {
			found = true
			c.HasMessages = true
			if m.Time >= c.UpdatedAt {
				c.LastMessage = b.preview(*c, m)
				c.UpdatedAt = m.Time
			}
			if notify && !m.Mine {
				c.Unread++
			}
		}
	}
	if !found {
		unread := 0
		if notify && !m.Mine {
			unread = 1
		}
		row := chat{ID: m.ChatID, Name: b.personName(m.ChatID), Kind: chatKind(m.ChatID), UpdatedAt: m.Time, Unread: unread, HasMessages: true}
		if p, ok := b.data.People[m.ChatID]; ok {
			row.Avatar = p.Avatar
		}
		for _, p := range b.data.Contacts {
			if p.ID == m.ChatID {
				row.Avatar = p.Avatar
			}
		}
		row.LastMessage = b.preview(row, m)
		b.data.Chats = append(b.data.Chats, row)
	}
	if notify {
		b.emit("message", map[string]any{"message": m})
		b.emit("snapshot", b.data)
	}
}
