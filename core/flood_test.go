package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/beeper/line/pkg/line"
)

const testMID = "u00000000000000000000000000000000"

func peerMID(i int) string { return fmt.Sprintf("u%032x", i+1) }

type envelope struct {
	ID     string          `json:"id"`
	OK     *bool           `json:"ok"`
	Event  string          `json:"event"`
	Result json.RawMessage `json:"result"`
	Data   json.RawMessage `json:"data"`
	Error  *fault          `json:"error"`
	Raw    int             `json:"-"`
}

func decodeOutput(t *testing.T, out *bytes.Buffer) []envelope {
	t.Helper()
	var list []envelope
	for _, line := range bytes.Split(bytes.TrimSpace(out.Bytes()), []byte("\n")) {
		if len(line) == 0 {
			continue
		}
		var e envelope
		if err := json.Unmarshal(line, &e); err != nil {
			t.Fatalf("core wrote a line that is not JSON: %v", err)
		}
		e.Raw = len(line)
		list = append(list, e)
	}
	out.Reset()
	return list
}

func liveBackend(out *bytes.Buffer) *backend {
	ctx, cancel := context.WithCancel(context.Background())
	return &backend{ctx: ctx, cancel: cancel, out: out, events: make(chan workerEvent, 128), state: "ready", initialized: true,
		session: &savedSession{MID: testMID, AccessToken: "fixture"}, messages: map[string][]message{}, seen: map[string]struct{}{},
		data: snapshot{Profile: &person{ID: testMID, Name: "me"}, Contacts: []person{}, Chats: []chat{}}}
}

func operation(i, senders int) workerEvent {
	op := line.Operation{Type: 26, Revision: json.Number(fmt.Sprint(1000 + i)), Message: &line.Message{
		ID: fmt.Sprintf("m%d", i), From: peerMID(i % senders), To: testMID, ToType: 0, Text: fmt.Sprintf("backlog %d", i),
		CreatedTime: json.Number(fmt.Sprint(1700000000000 + int64(i))),
	}}
	raw, _ := json.Marshal(op)
	return workerEvent{0, "stream", streamData{"operation", string(raw)}}
}

// A reconnect can deliver thousands of operations at once. Each one used to
// produce a "message" event plus a snapshot of the whole account, which froze
// the UI. They now arrive as a few batches and small chat updates.
func TestStreamBurstIsBatched(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	for i := 0; i < 400; i++ { // a realistic account: the old snapshot was sent whole per message
		b.data.Contacts = append(b.data.Contacts, person{ID: peerMID(1000 + i), Name: fmt.Sprintf("friend %d", i), Friend: true})
	}
	const total, senders = 3000, 20
	go func() {
		for i := 0; i < total; i++ {
			b.events <- operation(i, senders)
		}
	}()
	got := map[string]int{}
	unread := map[string]int{}
	batches, updates, bytesOut := 0, 0, 0
	deadline := time.After(20 * time.Second)
	for len(got) < total {
		select {
		case ev := <-b.events:
			b.step(ev)
		case <-deadline:
			t.Fatalf("only %d of %d messages were delivered", len(got), total)
		}
		for _, e := range decodeOutput(t, &out) {
			bytesOut += e.Raw
			switch e.Event {
			case "messages":
				batches++
				var data struct {
					Messages []message `json:"messages"`
					Chats    []chat    `json:"chats"`
				}
				if err := json.Unmarshal(e.Data, &data); err != nil || len(data.Messages) == 0 || len(data.Messages) > maxBatch {
					t.Fatalf("bad batch of %d: %v", len(data.Messages), err)
				}
				for _, c := range data.Chats {
					unread[c.ID] = c.Unread
				}
				updates += len(data.Chats)
				for _, m := range data.Messages {
					got[m.ID]++
					// The UI must learn of a chat no later than its first message.
					if _, listed := unread[m.ChatID]; !listed {
						t.Fatalf("message %s arrived before its chat row", m.ID)
					}
				}
			default:
				t.Fatalf("a burst must not send %q events", e.Event)
			}
		}
	}
	for id, n := range got {
		if n != 1 {
			t.Fatalf("message %s delivered %d times", id, n)
		}
	}
	if batches > total/10 || updates > batches*senders {
		t.Fatalf("burst was not batched: %d message events carrying %d chat rows for %d messages", batches, updates, total)
	}
	sum := 0
	for _, n := range unread {
		sum += n
	}
	if len(unread) != senders || sum != total {
		t.Fatalf("chat updates report %d unread in %d chats, want %d in %d", sum, len(unread), total, senders)
	}
	if b.revision != 1000+total-1 {
		t.Fatalf("revision %d did not follow the stream", b.revision)
	}
	// 3000 messages with whole-account snapshots were well over 100 MB.
	if bytesOut > 2<<20 {
		t.Fatalf("burst wrote %d bytes to the UI", bytesOut)
	}
}

// A single message must still reach the UI at once, with its chat row.
func TestSingleMessageFlushesImmediately(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	started := time.Now()
	b.step(operation(1, 1))
	if waited := time.Since(started); waited > 10*batchLinger {
		t.Fatalf("one message waited %v", waited)
	}
	events := decodeOutput(t, &out)
	var data struct {
		Messages []message `json:"messages"`
		Chats    []chat    `json:"chats"`
	}
	if len(events) != 1 || events[0].Event != "messages" || json.Unmarshal(events[0].Data, &data) != nil {
		t.Fatalf("unexpected events: %+v", events)
	}
	if len(data.Messages) != 1 || len(data.Chats) != 1 || data.Chats[0].ID != data.Messages[0].ChatID || data.Chats[0].Unread != 1 {
		t.Fatalf("message did not arrive with its chat row: %s", events[0].Data)
	}
}

// A snapshot carries the chat rows, so it goes out before buffered messages
// and the UI knows a new chat when its first message arrives.
func TestSnapshotPrecedesBufferedMessages(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	b.serveEvent(operation(1, 1))
	b.emitSnapshot()
	b.flush()
	events := decodeOutput(t, &out)
	if len(events) != 2 || events[0].Event != "snapshot" || events[1].Event != "messages" {
		t.Fatalf("unexpected order: %+v", events)
	}
	if !strings.Contains(string(events[1].Data), `"chats":[]`) {
		t.Fatalf("messages after a snapshot must not repeat chat rows: %s", events[1].Data)
	}
}

// A message that is retracted before its batch is sent must reach the UI as
// the retracted placeholder, never with its text.
func TestUnsentInsideBurstStaysUnsent(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	sent := operation(1, 1)
	raw, _ := json.Marshal(line.Operation{Type: 65, Revision: "2000", Param1: peerMID(1), Param2: "m1"})
	b.events <- workerEvent{0, "stream", streamData{"operation", string(raw)}}
	b.step(sent) // the unsend is already queued behind the message
	var texts []string
	for _, e := range decodeOutput(t, &out) {
		if e.Event != "messages" {
			continue
		}
		var data struct {
			Messages []message `json:"messages"`
			Chats    []chat    `json:"chats"`
		}
		if err := json.Unmarshal(e.Data, &data); err != nil {
			t.Fatal(err)
		}
		for _, m := range data.Messages {
			texts = append(texts, m.Text)
			if !m.Unsent || m.Kind != "unsent" || m.Text != "" {
				t.Fatalf("retracted message was sent to the UI as %+v", m)
			}
		}
		for _, c := range data.Chats {
			if strings.Contains(c.LastMessage, "backlog") {
				t.Fatalf("chat preview still shows the retracted text: %q", c.LastMessage)
			}
		}
	}
	if len(texts) != 1 {
		t.Fatalf("expected the message once, got %d", len(texts))
	}
}

// A background job that fails with the token it started with, after the
// session's token was renewed, must not renew it again or end the session.
func TestOutdatedJobDoesNotTouchSession(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	release := make(chan struct{})
	b.background("3", func(context.Context, *line.Client) (any, error) {
		<-release
		return nil, errors.New("API error 401: V3_TOKEN_CLIENT_LOGGED_OUT")
	}, nil)
	b.session.AccessToken = "renewed"
	close(release)
	select {
	case ev := <-b.events:
		b.serveEvent(ev)
	case <-time.After(5 * time.Second):
		t.Fatal("job never finished")
	}
	events := decodeOutput(t, &out)
	if b.session == nil || b.session.AccessToken != "renewed" || b.state != "ready" {
		t.Fatalf("an outdated job ended or changed the session: %+v", b.session)
	}
	if len(events) != 1 || events[0].ID != "3" || events[0].Error == nil {
		t.Fatalf("the request was not answered with an error: %+v", events)
	}
	b.workers.Wait()
}

func TestOnlyUnansweredRefreshKeepsSession(t *testing.T) {
	cases := map[string]struct {
		err  error
		keep bool
	}{
		"no route":          {&net.OpError{Op: "dial", Err: errors.New("network is unreachable")}, true},
		"deadline":          {fmt.Errorf("request failed: %w", context.DeadlineExceeded), true},
		"refused by LINE":   {errors.New(`API error 400: {"code":10051,"message":"RESPONSE_ERROR"}`), false},
		"refresh token bad": {fail("login_required", "登入已過期，請重新登入。"), false},
	}
	for name, c := range cases {
		if got := transientFailure(c.err); got != c.keep {
			t.Errorf("%s: transientFailure=%v, want %v", name, got, c.keep)
		}
	}
}

// A media host's answer must never be read as a verdict on the login: an
// expired photo answering 403 used to refresh the token and restart the
// stream, or log the user out when that refresh failed.
func TestMediaFailureIsNotALoginFailure(t *testing.T) {
	hostile := line.ErrOBSObjectNotFound
	for _, remote := range []error{errors.New("API error 403: forbidden V3_TOKEN_CLIENT_LOGGED_OUT"), hostile} {
		_, err := fetchMedia(context.Background(), nil, false, func() ([]byte, error) { return nil, remote })
		var media *mediaFailure
		if !errors.As(err, &media) || !errors.Is(err, remote) {
			t.Fatalf("download error was not marked as a media failure: %v", err)
		}
	}
	data, err := fetchMedia(context.Background(), nil, false, func() ([]byte, error) { return []byte("ok"), nil })
	if err != nil || string(data) != "ok" {
		t.Fatal("successful download was not passed through")
	}
}

// An oversized envelope is answered with a small error instead of a line the
// UI would refuse.
func TestOversizedReplyBecomesError(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	if err := b.reply("5", map[string]string{"data": strings.Repeat("x", maxOutput)}, nil); err != nil {
		t.Fatal(err)
	}
	events := decodeOutput(t, &out)
	if len(events) != 1 || events[0].ID != "5" || events[0].Error == nil || events[0].Error.Code != "response_too_large" || events[0].Raw > 4096 {
		t.Fatalf("oversized reply was not replaced: %d events", len(events))
	}
}

func TestResyncDecision(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	if strings.Contains(wallNow().String(), "m=") {
		t.Fatal("wallNow must not carry a monotonic reading (it stops during suspend)")
	}
	cases := []struct {
		name   string
		resync bool
		seen   time.Time
		want   bool
	}{
		{"first connection after login", false, time.Time{}, false},
		{"revision could not be fetched at login", true, time.Time{}, true},
		{"brief network drop", false, wallNow().Add(-20 * time.Second), false},
		{"back from a weekend of suspend", false, wallNow().Add(-48 * time.Hour), true},
	}
	for _, c := range cases {
		b.resync, b.streamSeen = c.resync, c.seen
		if got := b.needsResync(); got != c.want {
			t.Errorf("%s: needsResync=%v, want %v", c.name, got, c.want)
		}
	}
}

func TestFetchKeyRemembersFailures(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	calls := 0
	failing := func() error { calls++; return errors.New("not found") }
	for i := 0; i < 50; i++ {
		if b.fetchKey("group", "c1", 7, failing) == nil {
			t.Fatal("failure was lost")
		}
	}
	if calls != 1 {
		t.Fatalf("an unavailable key was requested %d times for 50 messages", calls)
	}
	if b.fetchKey("group", "c1", 8, failing); calls != 2 {
		t.Fatal("another key must be tried on its own")
	}
	// Token problems are handled by the caller and must be retried.
	auth := func() error { calls++; return errors.New("API error 401: V3_TOKEN_CLIENT_LOGGED_OUT") }
	if !isAuthError(auth()) {
		t.Fatal("fixture is not recognised as an auth error")
	}
	calls = 0
	b.fetchKey("user", "u1", 1, auth)
	b.fetchKey("user", "u1", 1, auth)
	if calls != 2 {
		t.Fatalf("auth failure was cached (%d calls)", calls)
	}
	b.keyMisses["user\x00u2\x003"] = time.Now().Add(-2 * keyMissTTL)
	calls = 0
	if err := b.fetchKey("user", "u2", 3, func() error { calls++; return nil }); err != nil || calls != 1 {
		t.Fatal("an expired miss must be retried")
	}
	if _, kept := b.keyMisses["user\x00u2\x003"]; kept {
		t.Fatal("a successful fetch must clear the miss")
	}
}

func TestMediaStoreIsPrivateAndBounded(t *testing.T) {
	root := t.TempDir()
	t.Setenv("XDG_RUNTIME_DIR", root)
	// Left behind by a core that crashed: no lock holder.
	stale := filepath.Join(root, "matrix-line", "media-stale")
	if err := os.MkdirAll(stale, 0o700); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(stale, ".lock"), nil, 0o600)
	os.WriteFile(filepath.Join(stale, "photo"), []byte("old decrypted photo"), 0o600)

	var store mediaStore
	photo := bytes.Repeat([]byte{0xff, 0xd8, 0x42}, 1<<20) // 3 MiB: over the old pipe limit as base64
	path, err := store.put(mediaKey("100", false), photo)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(stale); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("photos of a crashed core were left on disk")
	}
	info, _ := os.Stat(path)
	dir, _ := os.Stat(filepath.Dir(path))
	if info.Mode().Perm() != 0o600 || dir.Mode().Perm() != 0o700 || !strings.HasPrefix(path, filepath.Join(root, "matrix-line")+"/") {
		t.Fatalf("photo is not private: %v in %v at %s", info.Mode(), dir.Mode(), path)
	}
	if data, _ := os.ReadFile(path); !bytes.Equal(data, photo) {
		t.Fatal("stored photo differs")
	}
	if got, ok := store.get(mediaKey("100", false)); !ok || got != path {
		t.Fatal("stored photo not found")
	}
	if _, ok := store.get(mediaKey("100", true)); ok {
		t.Fatal("preview and original must not share a file")
	}
	// A second core (demo and live can run together) must not sweep this one.
	var other mediaStore
	if _, err := other.put("x", []byte("y")); err != nil {
		t.Fatal(err)
	}
	if _, ok := store.get(mediaKey("100", false)); !ok {
		t.Fatal("a second core removed the first core's photos")
	}
	other.close()

	chunk := make([]byte, 16<<20)
	for i := 0; i < 10; i++ {
		if _, err := store.put(fmt.Sprintf("big%d", i), chunk); err != nil {
			t.Fatal(err)
		}
	}
	if store.total > mediaLimit {
		t.Fatalf("store holds %d bytes", store.total)
	}
	if _, ok := store.get(mediaKey("100", false)); ok {
		t.Fatal("least recently used photo was not evicted")
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("evicted photo still on disk")
	}
	// A file removed behind the store's back is reported as missing.
	last, _ := store.get("big9")
	os.Remove(last)
	if _, ok := store.get("big9"); ok {
		t.Fatal("missing file reported as present")
	}
	held := store.dir
	store.close()
	if _, err := os.Stat(held); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("photos survive the end of the session")
	}
}

func TestPrivateDirRejectsOpenDirectory(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "shared")
	if err := os.Mkdir(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	os.Chmod(dir, 0o777)
	if err := privateDir(dir); err != nil {
		t.Fatal(err)
	}
	if info, _ := os.Stat(dir); info.Mode().Perm() != 0o700 {
		t.Fatalf("directory left open: %v", info.Mode())
	}
	link := filepath.Join(t.TempDir(), "link")
	os.Symlink(dir, link)
	if privateDir(link) == nil {
		t.Fatal("a symlink was accepted as the media directory")
	}
}

// A photo larger than the UI's old 2 MiB line limit must come back as a path
// in a small reply.
func TestImageFetchRepliesWithPath(t *testing.T) {
	t.Setenv("XDG_RUNTIME_DIR", t.TempDir())
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	defer b.media.close()
	chat := peerMID(1)
	b.messages[chat] = []message{{ID: "500", ChatID: chat, Kind: "image"}}
	photo := bytes.Repeat([]byte("JPEG"), 2<<20) // 8 MiB
	if _, err := b.media.put(mediaKey("500", false), photo); err != nil {
		t.Fatal(err)
	}
	for _, preview := range []bool{false, true} {
		params, _ := json.Marshal(map[string]any{"chat_id": chat, "message_id": "500", "preview": preview})
		result, err, _ := b.serveRequest(request{ID: "1", Method: "images.fetch", Params: params})
		if err != nil {
			t.Fatal(err)
		}
		if err := b.reply("1", result, nil); err != nil {
			t.Fatal(err)
		}
		events := decodeOutput(t, &out)
		if len(events) != 1 || events[0].Raw > 4096 {
			t.Fatalf("photo reply is %d bytes", events[0].Raw)
		}
		path := result.(map[string]any)["path"].(string)
		if data, _ := os.ReadFile(path); !bytes.Equal(data, photo) {
			t.Fatal("reply does not point at the photo")
		}
	}
	params, _ := json.Marshal(map[string]any{"chat_id": chat, "message_id": "501"})
	if _, err, _ := b.serveRequest(request{ID: "2", Method: "images.fetch", Params: params}); err == nil {
		t.Fatal("a message outside the chat was accepted")
	}
}

func TestBackgroundWorkRepliesLater(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	next := func() workerEvent {
		t.Helper()
		select {
		case ev := <-b.events:
			return ev
		case <-time.After(5 * time.Second):
			t.Fatal("background work never finished")
			return workerEvent{}
		}
	}
	release := make(chan struct{})
	result := b.background("7", func(ctx context.Context, _ *line.Client) (any, error) {
		<-release
		return 21, nil
	}, func(value any, err error) (any, error) {
		return map[string]int{"answer": value.(int) * 2}, err
	})
	if _, later := any(result).(deferred); !later {
		t.Fatal("request was not deferred")
	}
	// The loop stays free while the job runs.
	select {
	case <-b.events:
		t.Fatal("job finished before it was released")
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	b.serveEvent(next())
	events := decodeOutput(t, &out)
	if len(events) != 1 || events[0].ID != "7" || events[0].OK == nil || !*events[0].OK || string(events[0].Result) != `{"answer":42}` {
		t.Fatalf("unexpected reply: %+v", events)
	}

	// A panic in a worker is one failed request, not the end of the core.
	b.background("8", func(context.Context, *line.Client) (any, error) { panic("malformed media") }, nil)
	b.serveEvent(next())
	events = decodeOutput(t, &out)
	if len(events) != 1 || events[0].ID != "8" || events[0].Error == nil || events[0].Error.Code != "internal" {
		t.Fatalf("panic was not reported as a failed request: %+v", events)
	}

	// A job that outlives its session is answered, but its result is dropped.
	finished := false
	b.background("9", func(ctx context.Context, _ *line.Client) (any, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}, func(any, error) (any, error) { finished = true; return nil, nil })
	b.stop()
	b.serveEvent(next())
	events = decodeOutput(t, &out)
	if finished || len(events) != 1 || events[0].ID != "9" || events[0].Error == nil || events[0].Error.Code != "session_changed" {
		t.Fatalf("stale job was applied to a new session: finished=%v %+v", finished, events)
	}
	b.workers.Wait()
}

// A panic while handling a request is answered as an error and the loop goes on.
func TestHandlerPanicDoesNotStopCore(t *testing.T) {
	var out bytes.Buffer
	b := liveBackend(&out)
	defer b.cancel()
	b.initialized = true
	b.session = nil // messages.list in live mode dereferences the session
	chat := peerMID(3)
	params, _ := json.Marshal(map[string]any{"chat_id": chat, "limit": 10})
	_, err, done := b.serveRequest(request{ID: "1", Method: "messages.list", Params: params})
	if err == nil || done || userError(err).Code != "internal" {
		t.Fatalf("panic was not turned into a failed request: %v", err)
	}
}
