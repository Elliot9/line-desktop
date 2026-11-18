package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"maps"
	"os"
	"os/signal"
	"runtime/debug"
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
	out               io.Writer
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
	unsealedNoticed   map[string]bool
	pushPreserved     bool
	restoring         bool
	// forget is set when the user unticked 自動登入: the session then lives
	// only in memory and any earlier keyring copy is removed.
	forget bool
	region string
	// revision is the last LINE operation seen. resync makes the next stream
	// start over from LINE's current revision instead of replaying from it;
	// streamSeen is the wall-clock time of the last event the stream sent.
	revision   int64
	resync     bool
	streamSeen time.Time
	// pendingMessages and dirtyChats collect changes until flush sends them
	// to the UI as one "messages" and one "chats" event.
	pendingMessages []message
	dirtyChats      map[string]struct{}
	keyMisses       map[string]time.Time
	media           mediaStore
	work            context.Context
	workCancel      context.CancelFunc
	slots           chan struct{}
	// obsTokens lets every media download of a session share one OBS token
	// instead of asking LINE for a new one per photo.
	obsTokens *line.OBSTokenCache
	// groupKeys records the group keys handed to the crypto manager, so key
	// prefetching does not ask LINE for them again.
	groupKeys map[keyNeed]bool
	// streamOpened is when the current stream connected (zero once used to
	// allow one immediate reconnect).
	streamOpened time.Time
	// snapshotRetry is when a chat list refresh that failed is tried again.
	snapshotRetry time.Time
	snapshotDelay time.Duration
}

const (
	// A burst from the stream is collected for at most batchWindow before it
	// is sent to the UI; batchLinger is how long the loop waits for the next
	// event of a burst before it sends what it has.
	batchWindow   = 120 * time.Millisecond
	batchLinger   = 12 * time.Millisecond
	maxBatch      = 200
	maxBatchBytes = 512 << 10
)

func main() {
	demo := flag.Bool("demo", false, "Use deterministic offline demo data")
	showVersion := flag.Bool("version", false, "Print version")
	diag := flag.Bool("diagnose", false, "Print anonymised contact/chat field statistics for the saved login")
	diagRead := flag.Bool("diagnose-read", false, "Print anonymised read-receipt statistics for the saved login")
	diagHistory := flag.Bool("diagnose-history", false, "Page back through recent chats with the saved login and print how far LINE serves history")
	diagVideo := flag.Bool("diagnose-video", false, "Download and decrypt the newest video of the recent chats in memory and print what it held")
	flag.Parse()
	if *diagVideo {
		if err := diagnoseVideo(context.Background(), newKeyringStore()); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *diagHistory {
		if err := diagnoseHistory(context.Background(), newKeyringStore()); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *diagRead {
		if err := diagnoseReadRange(context.Background(), newKeyringStore()); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *diag {
		if err := diagnose(context.Background(), newKeyringStore()); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *showVersion {
		fmt.Println(version)
		return
	}
	log.SetOutput(io.Discard)
	// A UI that went away must end the core through a failed write, not
	// through SIGPIPE, so that the cleanup below (decrypted photos) runs.
	signal.Ignore(syscall.SIGPIPE)
	if !*demo {
		// Photos left by a core that was killed are removed right away.
		if root := mediaRoot(); privateDir(root) == nil {
			sweepMedia(root)
		}
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	defer cancel()
	if err := serve(ctx, os.Stdin, os.Stdout, *demo, newKeyringStore()); err != nil {
		fmt.Fprintln(os.Stderr, "LINE core stopped because the private pipe failed.")
		os.Exit(1)
	}
}
func serve(parent context.Context, in io.Reader, out io.Writer, demo bool, store sessionStore) error {
	ctx, cancel := context.WithCancel(parent)
	b := &backend{ctx: ctx, cancel: cancel, out: out, demo: demo, store: store, events: make(chan workerEvent, 128), state: "logged_out", sequence: time.Now().UnixMilli(), messages: map[string][]message{}, seen: map[string]struct{}{}, data: snapshot{Contacts: []person{}, Chats: []chat{}}, slots: make(chan struct{}, backgroundJobs)}
	// The photos go first: only this loop touches the store, and a worker
	// that is slow to stop must not keep them on disk.
	defer func() { cancel(); b.stop(); b.media.close(); b.workers.Wait() }()
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
			result, err, done := b.serveRequest(req)
			b.flush()
			if _, later := result.(deferred); later && err == nil {
				continue // a background worker replies when it is done
			}
			if err := b.reply(req.ID, result, err); err != nil {
				return err
			}
			if done {
				return nil
			}
		case ev := <-b.events:
			b.step(ev)
		case <-ticker.C:
			b.tick()
			b.flush()
		}
	}
}

// serveRequest answers one request. A panic in a handler becomes a failed
// request: the core is the only connection to LINE, and letting it die turns
// one bad message or reply into "核心已停止" for the whole app.
func (b *backend) serveRequest(req request) (result any, err error, done bool) {
	defer func() {
		if p := recover(); p != nil {
			reportPanic(p)
			result, err, done = nil, errInternal, false
		}
	}()
	return b.handle(req)
}

func (b *backend) serveEvent(ev workerEvent) {
	defer func() {
		if p := recover(); p != nil {
			reportPanic(p)
		}
	}()
	b.handleEvent(ev)
}

// reportPanic writes to stderr, which is only read when the core is run by
// hand; a stack trace holds no message text, tokens or keys.
func reportPanic(p any) {
	fmt.Fprintf(os.Stderr, "LINE core recovered from an internal error: %v\n%s\n", p, debug.Stack())
}

// step handles one worker event and what queued up behind it, then sends
// the result to the UI as one batch.
func (b *backend) step(ev workerEvent) {
	b.serveEvent(ev)
	b.drainEvents()
	b.flush()
}

// drainEvents keeps handling worker events for a moment after one arrived, so
// a burst of stream operations reaches the UI as one batch instead of one
// update (and one redraw of the chat list) per message.
func (b *backend) drainEvents() {
	if len(b.pendingMessages) == 0 && len(b.dirtyChats) == 0 {
		return
	}
	deadline := time.Now().Add(batchWindow)
	linger := time.NewTimer(batchLinger)
	defer linger.Stop()
	for len(b.pendingMessages) < maxBatch && time.Now().Before(deadline) {
		select {
		case ev := <-b.events:
			b.serveEvent(ev)
			linger.Reset(batchLinger)
		case <-linger.C:
			return
		case <-b.ctx.Done():
			return
		}
	}
}

// touchChat marks a chat row as changed; flush sends it to the UI.
func (b *backend) touchChat(id string) {
	if b.dirtyChats == nil {
		b.dirtyChats = map[string]struct{}{}
	}
	b.dirtyChats[id] = struct{}{}
}

// flushMessages sends the buffered messages. rows, the chat rows they
// changed, travel with the first batch: the UI applies them before the
// messages, so it knows a chat before its first message, and it takes unread
// counts from rows (or a snapshot) instead of counting messages itself.
func (b *backend) flushMessages(rows []chat) {
	batch := b.pendingMessages
	b.pendingMessages = nil
	if rows == nil {
		rows = []chat{}
	}
	for len(batch) > 0 {
		n, size := 0, 0
		for n < len(batch) && n < maxBatch {
			size += len(batch[n].Text) + 512
			if n > 0 && size > maxBatchBytes {
				break
			}
			n++
		}
		b.emit("messages", map[string]any{"messages": batch[:n], "chats": rows})
		rows = []chat{}
		batch = batch[n:]
	}
}

// flush sends buffered messages and the chat rows that changed. The whole
// snapshot (every contact, chat and group member) used to be sent after each
// message, which is megabytes per second during a burst.
func (b *backend) flush() {
	rows := make([]chat, 0, len(b.dirtyChats))
	if len(b.dirtyChats) > 0 {
		for _, c := range b.data.Chats {
			if _, ok := b.dirtyChats[c.ID]; ok {
				rows = append(rows, c)
			}
		}
	}
	b.dirtyChats = nil
	if len(b.pendingMessages) > 0 {
		b.flushMessages(rows)
	} else if len(rows) > 0 {
		b.emit("chats", map[string]any{"chats": rows})
	}
}

// emitSnapshot sends the whole state, then any buffered messages (their chat
// rows are part of the snapshot).
func (b *backend) emitSnapshot() {
	b.dirtyChats = nil
	b.emit("snapshot", b.data)
	b.flushMessages(nil)
}

// maxOutput bounds one line written to the UI, which refuses longer ones.
// Nothing legitimate comes close: photos travel as files, not through the pipe.
const maxOutput = 32 << 20

var errOversize = errors.New("envelope exceeds the output limit")

// send writes one envelope as a single line.
func (b *backend) send(envelope map[string]any) error {
	raw, err := json.Marshal(envelope)
	if err != nil {
		return err
	}
	if len(raw) >= maxOutput {
		return errOversize
	}
	_, err = b.out.Write(append(raw, '\n'))
	return err
}
func (b *backend) emit(event string, data any) {
	err := b.send(map[string]any{"event": event, "data": data})
	if errors.Is(err, errOversize) {
		err = b.send(map[string]any{"event": "notice", "data": map[string]string{"level": "error", "message": "資料過大，部分內容無法顯示。"}})
	}
	if err != nil {
		b.cancel()
	}
}
func (b *backend) reply(id string, result any, err error) error {
	if err != nil {
		return b.send(map[string]any{"id": id, "ok": false, "error": userError(err)})
	}
	if result == nil {
		result = struct{}{}
	}
	err = b.send(map[string]any{"id": id, "ok": true, "result": result})
	if errors.Is(err, errOversize) {
		err = b.send(map[string]any{"id": id, "ok": false, "error": &fault{"response_too_large", "回應資料過大，無法顯示。"}})
	}
	return err
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
	b.restoring = false
	if b.loginCancel != nil {
		b.loginCancel()
		b.loginCancel = nil
	}
	b.stopStream()
	if b.workCancel != nil {
		b.workCancel()
		b.work, b.workCancel = nil, nil
	}
	b.obsTokens, b.groupKeys = nil, nil
	b.snapshotRetry, b.snapshotDelay = time.Time{}, 0
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
	b.pendingMessages = nil
	b.dirtyChats = nil
	b.keyMisses = nil
	b.media.close()
	b.emitSnapshot()
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
			Remember *bool  `json:"remember"`
		}
		var err error
		if req.Method == "login.qr" {
			var q struct {
				Remember *bool `json:"remember"`
			}
			err = params(req.Params, &q)
			p.Remember = q.Remember
		} else {
			err = params(req.Params, &p)
			if err == nil && (len(p.Email) > 320 || !strings.Contains(p.Email, "@") || p.Password == "" || len(p.Password) > 1024) {
				err = fail("invalid_params", "請輸入有效的電子郵件與密碼。")
			}
		}
		if err != nil {
			return nil, err, false
		}
		// A login would throw away the saved session that is being restored
		// (or is already connected) and leave the app waiting for a scan.
		if b.restoring {
			return nil, fail("restoring", "正在恢復上次的登入，請稍候。"), false
		}
		if b.session != nil && b.state == "ready" {
			return nil, fail("already_logged_in", "已經登入 LINE。"), false
		}
		b.beginLogin(req.Method, p.Email, p.Password)
		b.forget = p.Remember != nil && !*p.Remember
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
			b.emitSnapshot()
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
			// Who has read how far, asked alongside the messages; a slow or
			// failed answer only means fewer 已讀 labels.
			ranges := make(chan map[string]map[string]int64, 1)
			ctx, token, chatID := b.ctx, b.session.AccessToken, p.ChatID
			go func() {
				found, _ := fetchReadRanges(ctx, token, []string{chatID})
				ranges <- found
			}()
			list, err = b.listMessages(p.ChatID, p.Limit)
			if err == nil {
				b.mergeReadBy(p.ChatID, (<-ranges)[p.ChatID])
			}
		}
		if list == nil {
			list = []message{}
		}
		if len(list) > p.Limit {
			list = list[len(list)-p.Limit:]
		}
		b.handleAuthError(err)
		return map[string]any{"messages": list, "read_by": maps.Clone(b.mergeReadBy(p.ChatID, nil))}, err, false
	case "messages.before":
		// Older messages, a page at a time, when the person scrolls up.
		var p struct {
			ChatID     string `json:"chat_id"`
			BeforeID   string `json:"before_id"`
			BeforeTime int64  `json:"before_time"`
			Limit      int    `json:"limit"`
		}
		if err := params(req.Params, &p); err != nil {
			return nil, err, false
		}
		if !b.validChat(p.ChatID) || p.BeforeID == "" || len(p.BeforeID) > 64 || p.BeforeTime <= 0 || p.Limit < 0 || p.Limit > 200 {
			return nil, fail("invalid_params", "聊天或訊息無效。"), false
		}
		if p.Limit == 0 {
			p.Limit = 100
		}
		list := []message{}
		var err error
		// Demo chats come whole; LINE's message IDs are numbers.
		if !b.demo {
			if !numericID(p.BeforeID) || b.session == nil {
				return nil, fail("invalid_params", "訊息無效。"), false
			}
			list, err = b.olderMessages(p.ChatID, p.BeforeID, p.BeforeTime, p.Limit)
			if list == nil {
				list = []message{}
			}
		}
		b.handleAuthError(err)
		return map[string]any{"messages": list, "more": len(list) > 0, "read_by": maps.Clone(b.mergeReadBy(p.ChatID, nil))}, err, false
	case "messages.send":
		var p struct {
			ChatID  string                 `json:"chat_id"`
			Text    string                 `json:"text"`
			ReplyTo string                 `json:"reply_to"`
			Emoji   []attachments.EmojiRef `json:"emoji"`
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
			if len(p.Emoji) > 0 {
				return nil, fail("invalid_params", "展示模式沒有 LINE 表情貼。"), false
			}
			msg = b.demoSend(p.ChatID, p.Text)
			msg.ReplyTo = p.ReplyTo
		} else if len(p.Emoji) > 0 {
			msg, err = b.sendEmojiText(p.ChatID, p.Text, p.ReplyTo, p.Emoji)
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
				if b.data.Chats[i].ID == p.ChatID && b.data.Chats[i].Unread != 0 {
					b.data.Chats[i].Unread = 0
					b.touchChat(p.ChatID)
				}
			}
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
			b.emitSnapshot()
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
			b.touchChat(c.ID)
		}
		b.handleAuthError(firstErr)
		return nil, firstErr, false
	case "stickers.list", "messages.sticker", "files.send", "files.download", "images.send", "images.fetch", "videos.fetch":
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
		if row.Kind != "direct" {
			// A group joined since the last sync: look up its real name and
			// members instead of showing the raw chat ID.
			b.fillGroup(&row)
		}
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
		b.pendingMessages = append(b.pendingMessages, m)
		b.touchChat(m.ChatID)
	}
}
