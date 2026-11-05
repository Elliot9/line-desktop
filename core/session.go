package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os/exec"
	"time"
)

var errNoSession = errors.New("no saved session")
var errKeyring = fail("keyring_unavailable", "無法存取系統金鑰圈。此登入只會保留到程式結束。")

type savedSession struct {
	Version      int               `json:"version"`
	AccessToken  string            `json:"access_token"`
	RefreshToken string            `json:"refresh_token"`
	Certificate  string            `json:"certificate"`
	MID          string            `json:"mid"`
	Keys         map[string]string `json:"keys"`
	RefreshAt    int64             `json:"refresh_at"`
	Revision     int64             `json:"revision"`
}
type sessionStore interface {
	Load(context.Context) (*savedSession, error)
	Save(context.Context, *savedSession) error
	Clear(context.Context) error
}
type keyringStore struct{ command string }

func newKeyringStore() sessionStore { return &keyringStore{command: "secret-tool"} }
func (s *keyringStore) run(ctx context.Context, input []byte, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, s.command, args...)
	cmd.Stdin = bytes.NewReader(input)
	var out limitedBuffer
	cmd.Stdout = &out
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		return out.Bytes(), err
	}
	return out.Bytes(), nil
}

type limitedBuffer struct{ bytes.Buffer }

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > maxEnvelope {
		return 0, errors.New("keyring record exceeds size limit")
	}
	return b.Buffer.Write(p)
}
func (s *keyringStore) Load(ctx context.Context) (*savedSession, error) {
	raw, err := s.run(ctx, nil, "lookup", "application", "matrix-line", "account", "primary")
	if err != nil {
		var e *exec.ExitError
		if errors.As(err, &e) && e.ExitCode() == 1 && len(raw) == 0 {
			return nil, errNoSession
		}
		return nil, errKeyring
	}
	if len(bytes.TrimSpace(raw)) == 0 {
		return nil, errNoSession
	}
	var record savedSession
	if json.Unmarshal(raw, &record) != nil || record.Version != 1 || record.AccessToken == "" || !validMID(record.MID) || len(record.Keys) == 0 {
		return nil, fail("invalid_session", "金鑰圈中的登入資料無效，請重新登入。")
	}
	return &record, nil
}
func (s *keyringStore) Save(ctx context.Context, record *savedSession) error {
	raw, err := json.Marshal(record)
	if err != nil {
		return err
	}
	_, err = s.run(ctx, raw, "store", "--label=Matrix LINE session", "application", "matrix-line", "account", "primary")
	if err != nil {
		return errKeyring
	}
	return nil
}
func (s *keyringStore) Clear(ctx context.Context) error {
	_, err := s.run(ctx, nil, "clear", "application", "matrix-line", "account", "primary")
	if err != nil {
		return fail("keyring_delete_failed", "無法刪除系統金鑰圈中的 LINE 登入資料。請解鎖金鑰圈後再次登出。")
	}
	return nil
}
