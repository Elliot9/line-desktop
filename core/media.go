package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/beeper/line/pkg/line"
)

// mediaLimit bounds the decrypted photos kept for the UI.
const mediaLimit = 128 << 20

// mediaStore hands photos to the UI as private files. Image bytes used to be
// sent as base64 inside the JSON reply; any photo over about 1.5 MB then
// exceeded the UI's envelope limit and ended the connection ("核心已停止").
//
// The directory lives in XDG_RUNTIME_DIR: memory-backed, readable only by the
// user and removed at logout, so Letter Sealing photos never reach permanent
// storage. It is deleted when the session ends or the core exits.
type mediaStore struct {
	dir   string
	lock  *os.File
	files map[string]mediaFile
	order []string // least recently used first
	total int64
}

type mediaFile struct {
	path string
	size int64
}

func mediaKey(messageID string, preview bool) string {
	if preview {
		return messageID + "/preview"
	}
	return messageID + "/original"
}

func mediaRoot() string {
	if base := os.Getenv("XDG_RUNTIME_DIR"); filepath.IsAbs(base) {
		return filepath.Join(base, "matrix-line")
	}
	// Without a runtime directory, prefer memory-backed /dev/shm to /tmp.
	base := os.TempDir()
	if info, err := os.Stat("/dev/shm"); err == nil && info.IsDir() {
		base = "/dev/shm"
	}
	return filepath.Join(base, "matrix-line-"+strconv.Itoa(os.Getuid()))
}

// privateDir creates path for this user only and refuses a directory that
// someone else owns or can read (the fallback location is under /tmp).
func privateDir(path string) error {
	if err := os.Mkdir(path, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !info.IsDir() || !ok || int(stat.Uid) != os.Getuid() {
		return errors.New("media directory is not a private directory of this user")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return os.Chmod(path, 0o700)
	}
	return nil
}

// sweepMedia removes directories left behind by a core that did not exit
// cleanly. A running core holds a lock on its own directory.
func sweepMedia(root string) {
	entries, err := os.ReadDir(root)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() || !strings.HasPrefix(entry.Name(), "media-") {
			continue
		}
		dir := filepath.Join(root, entry.Name())
		lock, err := os.OpenFile(filepath.Join(dir, ".lock"), os.O_RDWR|syscall.O_NOFOLLOW, 0)
		if err != nil {
			// No lock file: either abandoned, or another core is between
			// creating the directory and locking it. Only the former is old.
			if info, statErr := entry.Info(); statErr == nil && time.Since(info.ModTime()) > time.Minute {
				os.RemoveAll(dir)
			}
			continue
		}
		if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) == nil {
			os.RemoveAll(dir)
		}
		lock.Close()
	}
}

func (s *mediaStore) open() error {
	if s.dir != "" {
		return nil
	}
	root := mediaRoot()
	if err := privateDir(root); err != nil {
		return err
	}
	sweepMedia(root)
	dir, err := os.MkdirTemp(root, "media-")
	if err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(dir, ".lock"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err == nil {
		if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			lock.Close()
		}
	}
	if err != nil {
		os.RemoveAll(dir)
		return err
	}
	s.dir, s.lock, s.files = dir, lock, map[string]mediaFile{}
	return nil
}

// get returns the stored file for key if it is still intact.
func (s *mediaStore) get(key string) (string, bool) {
	file, ok := s.files[key]
	if !ok {
		return "", false
	}
	if info, err := os.Lstat(file.path); err != nil || !info.Mode().IsRegular() || info.Size() != file.size {
		s.drop(key)
		return "", false
	}
	s.touch(key)
	return file.path, true
}

// put stores data under key and returns the file's path, evicting the least
// recently used photos once the store holds more than mediaLimit.
func (s *mediaStore) put(key string, data []byte) (string, error) {
	if err := s.open(); err != nil {
		return "", err
	}
	s.drop(key)
	sum := sha256.Sum256([]byte(key))
	path := filepath.Join(s.dir, hex.EncodeToString(sum[:16]))
	_ = os.Remove(path)
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0o600)
	if err != nil {
		return "", err
	}
	_, err = file.Write(data)
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		os.Remove(path)
		return "", err
	}
	s.files[key] = mediaFile{path, int64(len(data))}
	s.order = append(s.order, key)
	s.total += int64(len(data))
	for s.total > mediaLimit && len(s.order) > 1 {
		s.drop(s.order[0])
	}
	return path, nil
}

func (s *mediaStore) touch(key string) {
	for i, k := range s.order {
		if k == key {
			s.order = append(append(s.order[:i:i], s.order[i+1:]...), key)
			return
		}
	}
}

func (s *mediaStore) drop(key string) {
	file, ok := s.files[key]
	if !ok {
		return
	}
	os.Remove(file.path)
	delete(s.files, key)
	s.total -= file.size
	for i, k := range s.order {
		if k == key {
			s.order = append(s.order[:i:i], s.order[i+1:]...)
			break
		}
	}
}

// close deletes every stored photo; the next put starts a new directory.
func (s *mediaStore) close() {
	if s.dir == "" {
		return
	}
	os.RemoveAll(s.dir)
	s.lock.Close()
	*s = mediaStore{}
}

// maxImageBytes bounds a downloaded photo. LINE photos are at most 20 MiB,
// but an official account's picture can point at any URL.
const maxImageBytes = 32 << 20

// mediaFailure marks an error that came from the media host. Its status and
// body are another server's (for official accounts, any URL), so it says
// nothing about the login and must never refresh the token or end the
// session: an expired photo answering 403 used to do exactly that.
type mediaFailure struct{ err error }

func (e *mediaFailure) Error() string { return e.err.Error() }
func (e *mediaFailure) Unwrap() error { return e.err }

// fetchMedia runs download with a valid OBS token. Only acquiring the token
// can report a login problem; every error of the download itself is returned
// as a mediaFailure.
func fetchMedia(ctx context.Context, client *line.Client, needsToken bool, download func() ([]byte, error)) ([]byte, error) {
	if needsToken {
		if _, err := client.AcquireEncryptedAccessToken(); err != nil {
			return nil, err
		}
	}
	data, err := download()
	if err != nil && needsToken && line.HTTPStatusCode(err) == 401 && ctx.Err() == nil {
		// The session's shared OBS token was no longer good: once more with a new one.
		client.OBSTokenCache.Invalidate()
		if _, tokenErr := client.AcquireEncryptedAccessToken(); tokenErr != nil {
			return nil, tokenErr
		}
		data, err = download()
	}
	if err != nil {
		return nil, &mediaFailure{err}
	}
	return data, nil
}

// deferred marks a request whose reply is sent later by a background worker.
type deferred struct{}

type workResult struct {
	id     string
	token  string // the access token the job ran with
	value  any
	err    error
	finish func(any, error) (any, error)
}

// backgroundJobs bounds concurrent downloads and uploads.
const backgroundJobs = 4

// sessionContext is cancelled when the session ends (logout, new login).
func (b *backend) sessionContext() context.Context {
	if b.work == nil {
		b.work, b.workCancel = context.WithCancel(b.ctx)
	}
	return b.work
}

// background runs work off the event loop and replies to request id when it
// is done. Downloads and uploads can take a minute or more; run inline they
// stopped the core from receiving messages or answering anything else.
//
// work runs on its own goroutine and must not touch backend state. finish
// runs on the event loop with work's outcome, and only if the session is
// still the one that started the job.
func (b *backend) background(id string, work func(context.Context, *line.Client) (any, error), finish func(any, error) (any, error)) deferred {
	if b.slots == nil {
		b.slots = make(chan struct{}, backgroundJobs)
	}
	ctx := b.sessionContext()
	token := b.session.AccessToken
	client := apiClient(ctx, token)
	if b.obsTokens == nil {
		b.obsTokens = &line.OBSTokenCache{}
	}
	client.OBSTokenCache = b.obsTokens
	slots, generation := b.slots, b.generation
	b.workers.Add(1)
	go func() {
		defer b.workers.Done()
		result := workResult{id: id, token: token, finish: finish}
		select {
		case slots <- struct{}{}:
			result.value, result.err = runWork(ctx, client, work)
			<-slots
		case <-ctx.Done():
			result.err = ctx.Err()
		}
		// The reply must reach the UI even after the session ended.
		b.enqueue(b.ctx, workerEvent{generation, "done", result})
	}()
	return deferred{}
}

// runWork turns a panic in a worker (malformed media, a library bug) into an
// ordinary failed request instead of taking the whole core down.
func runWork(ctx context.Context, client *line.Client, work func(context.Context, *line.Client) (any, error)) (value any, err error) {
	defer func() {
		if recover() != nil {
			value, err = nil, errInternal
		}
	}()
	return work(ctx, client)
}

var errInternal = fail("internal", "處理這個操作時發生內部錯誤，請再試一次。")

// finishWork completes a background request on the event loop.
func (b *backend) finishWork(generation uint64, r workResult) {
	result, err := r.value, r.err
	switch {
	case generation != b.generation:
		result, err = nil, fail("session_changed", "登入狀態已變更，操作已取消。")
	case r.finish != nil:
		result, err = b.runFinish(r)
	}
	// A job that waited or ran while the token was renewed failed with the
	// old token: that is no verdict on the session (it would renew the token
	// once more, or even log out, for every such job).
	outdated := r.err != nil && (b.session == nil || b.session.AccessToken != r.token)
	if generation == b.generation && !outdated {
		b.handleAuthError(err)
	}
	b.flush()
	if _, later := result.(deferred); later && err == nil {
		return
	}
	if b.reply(r.id, result, err) != nil {
		b.cancel()
	}
}

func (b *backend) runFinish(r workResult) (result any, err error) {
	defer func() {
		if recover() != nil {
			result, err = nil, errInternal
		}
	}()
	return r.finish(r.value, r.err)
}
