package main

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/beeper/line/pkg/e2ee"
	"github.com/beeper/line/pkg/line"
	"github.com/skip2/go-qrcode"
)

type loginOutcome struct {
	session *savedSession
	profile *line.Profile
	stored  bool
	err     error
}
type contextTransport struct {
	ctx  context.Context
	base http.RoundTripper
}

func (t contextTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	ctx, cancel := context.WithCancel(req.Context())
	stop := context.AfterFunc(t.ctx, cancel)
	resp, err := t.base.RoundTrip(req.WithContext(ctx))
	if err != nil {
		stop()
		cancel()
		return nil, err
	}
	resp.Body = &cancelBody{ioReadCloser: resp.Body, stop: stop, cancel: cancel}
	return resp, nil
}

type cancelBody struct {
	ioReadCloser
	stop   func() bool
	cancel context.CancelFunc
}
type ioReadCloser interface {
	Read([]byte) (int, error)
	Close() error
}

func (b *cancelBody) Close() error { err := b.ioReadCloser.Close(); b.stop(); b.cancel(); return err }
func apiClient(ctx context.Context, token string) *line.Client {
	c := line.NewClient(token)
	c.HTTPClient = &http.Client{Timeout: 35 * time.Second, Transport: contextTransport{ctx, http.DefaultTransport}}
	c.OBSClient = &http.Client{Timeout: 90 * time.Second, Transport: contextTransport{ctx, http.DefaultTransport}}
	return c
}
func (b *backend) api() *line.Client { return apiClient(b.ctx, b.session.AccessToken) }
func (b *backend) beginRestore() {
	b.stop()
	b.sessionEvent("connecting", "正在讀取系統金鑰圈…")
	ctx, cancel := context.WithCancel(b.ctx)
	b.loginCancel = cancel
	generation := b.generation
	b.workers.Add(1)
	go func() {
		defer b.workers.Done()
		saved, err := b.store.Load(ctx)
		if err != nil {
			b.enqueue(ctx, workerEvent{generation, "restored", loginOutcome{err: err}})
			return
		}
		profile, err := restoreSession(ctx, saved)
		b.enqueue(ctx, workerEvent{generation, "restored", loginOutcome{session: saved, profile: profile, stored: true, err: err}})
	}()
}
func restoreSession(ctx context.Context, s *savedSession) (*line.Profile, error) {
	mgr, err := e2ee.NewManager()
	if err != nil {
		return nil, err
	}
	defer mgr.Close()
	if err = mgr.LoadMyKeyFromExportedMap(s.Keys); err != nil {
		return nil, fail("encryption_keys", "加密金鑰無法還原，請重新掃描 QR code 登入。")
	}
	c := apiClient(ctx, s.AccessToken)
	if s.RefreshToken != "" && s.RefreshAt > 0 && s.RefreshAt <= time.Now().Unix() {
		if err = refreshSaved(c, s); err != nil {
			return nil, err
		}
		c = apiClient(ctx, s.AccessToken)
	}
	p, err := c.GetProfileContext(ctx)
	if err != nil && (line.IsRefreshRequired(err) || line.HTTPStatusCode(err) == 401) && s.RefreshToken != "" {
		if err = refreshSaved(c, s); err != nil {
			return nil, err
		}
		p, err = apiClient(ctx, s.AccessToken).GetProfileContext(ctx)
	}
	if err != nil {
		return nil, err
	}
	if p == nil || p.Mid != s.MID {
		return nil, fail("account_mismatch", "登入帳號與金鑰不相符，請重新登入。")
	}
	return p, nil
}
func refreshSaved(c *line.Client, s *savedSession) error {
	if s.RefreshToken == "" {
		return fail("login_required", "登入已過期，請重新登入。")
	}
	result, err := c.RefreshAccessToken(s.RefreshToken)
	if err != nil {
		return err
	}
	if result == nil || result.AccessToken == "" {
		return fail("login_required", "登入已過期，請重新登入。")
	}
	s.AccessToken = result.AccessToken
	if result.RefreshToken != "" {
		s.RefreshToken = result.RefreshToken
	}
	s.RefreshAt = refreshTime(result)
	return nil
}
func refreshTime(token *line.TokenV3IssueResult) int64 {
	seconds, err := strconv.ParseInt(token.DurationUntilRefreshSec, 10, 64)
	if err != nil || seconds <= 0 {
		seconds = 3600
	}
	if seconds > 7*24*3600 {
		seconds = 7 * 24 * 3600
	}
	return time.Now().Unix() + max(30, seconds-60)
}
func (b *backend) beginLogin(method, email, password string) {
	previous := b.session
	if previous == nil {
		previous = b.previousSession
	}
	b.stop()
	b.clearData()
	b.sessionEvent("connecting", "正在連線至 LINE…")
	ctx, cancel := context.WithTimeout(b.ctx, 10*time.Minute)
	b.loginCancel = cancel
	generation := b.generation
	if b.demo {
		b.enqueue(ctx, workerEvent{generation, "auth", map[string]any{"stage": "pin", "pin": "123456", "message": "離線示範不會登入 LINE。請取消後重新啟動示範模式。"}})
		return
	}
	b.workers.Add(1)
	go func() {
		defer b.workers.Done()
		defer cancel()
		emit := func(data any) { b.enqueue(ctx, workerEvent{generation, "auth", data}) }
		result := performLogin(ctx, method, email, password, previous, emit)
		password = ""
		b.enqueue(ctx, workerEvent{generation, "login", result})
	}()
}
func performLogin(ctx context.Context, method, email, password string, previous *savedSession, emit func(any)) loginOutcome {
	c := apiClient(ctx, "")
	defer func() {
		if c.LoginAttempt != nil {
			c.LoginAttempt.Close()
		}
	}()
	certificate := ""
	if previous != nil && len(previous.Keys) > 0 {
		certificate = previous.Certificate
	}
	var result *line.LoginResult
	var err error
	if method == "login.qr" {
		var session string
		var qr *line.QRCodeResponse
		session, qr, err = c.StartQRLogin(ctx)
		if err != nil {
			return loginOutcome{err: err}
		}
		png, _ := qrcode.Encode(qr.CallbackURL, qrcode.Medium, 320)
		emit(map[string]any{"stage": "qr", "qr_url": qr.CallbackURL, "qr_png": base64.StdEncoding.EncodeToString(png)})
		timeout := time.Duration(qr.LongPollingIntervalSeconds) * time.Second
		if timeout <= 0 || timeout > 180*time.Second {
			timeout = 150 * time.Second
		}
		for attempt := 0; attempt < max(1, min(qr.LongPollingMaxCount, 10)); attempt++ {
			err = c.CheckQRCodeVerifiedContext(ctx, session, timeout)
			if err == nil || !line.IsQRLoginPollExpired(err) {
				break
			}
			timer := time.NewTimer(min(time.Second<<attempt, 30*time.Second))
			select {
			case <-ctx.Done():
				timer.Stop()
				return loginOutcome{err: ctx.Err()}
			case <-timer.C:
			}
		}
		if err != nil {
			return loginOutcome{err: err}
		}
		verified, err := c.VerifyQRCertificate(ctx, session, certificate)
		if err != nil {
			return loginOutcome{err: err}
		}
		if !verified {
			pin, err := c.CreatePinCode(ctx, session)
			if err != nil {
				return loginOutcome{err: err}
			}
			emit(map[string]any{"stage": "pin", "pin": pin, "message": "請在手機 LINE 輸入此驗證碼。"})
			if err = c.CheckPinCodeVerifiedContext(ctx, session); err != nil {
				return loginOutcome{err: err}
			}
		}
		result, err = c.QRCodeLoginV2(ctx, session)
	} else {
		result, err = c.LoginContext(ctx, email, password, certificate)
		password = ""
		if err == nil && result.AuthToken == "" {
			if result.Verifier == "" {
				return loginOutcome{err: fail("login_failed", "LINE 未提供登入驗證資料，請使用 QR code 登入。")}
			}
			pin := result.PinCode
			if pin == "" {
				pin = result.Pin
			}
			emit(map[string]any{"stage": "pin", "pin": pin, "message": "請在手機 LINE 輸入此驗證碼。"})
			result, err = c.WaitForLogin(result.Verifier, result.NoE2EE)
		}
	}
	if err != nil {
		return loginOutcome{err: err}
	}
	if result == nil {
		return loginOutcome{err: fail("login_failed", "LINE 未回傳登入資料。")}
	}
	token := result.AuthToken
	if result.TokenV3IssueResult != nil && result.TokenV3IssueResult.AccessToken != "" {
		token = result.TokenV3IssueResult.AccessToken
	}
	if token == "" {
		return loginOutcome{err: fail("login_failed", "LINE 登入未完成，請重試。")}
	}
	loggedIn := apiClient(ctx, token)
	loggedIn.LoginAttempt = result.Attempt
	profile, err := loggedIn.GetProfileContext(ctx)
	if err != nil {
		return loginOutcome{err: err}
	}
	if problem := accountProblem(profile, result.Mid); problem != "" {
		return loginOutcome{err: fail("account_mismatch", "LINE 登入資料不一致，請重新登入。（診斷："+problem+"）")}
	}
	keys, err := exportLoginKeys(result, loggedIn)
	if err != nil {
		return loginOutcome{err: fail("encryption_keys", "無法取得 Letter Sealing 金鑰。請啟用手機 LINE 的 Letter Sealing，並重新掃描 QR code。")}
	}
	if previous != nil && previous.MID == profile.Mid {
		keys = mergeKeys(previous.Keys, keys)
	}
	runtime, err := e2ee.NewManager()
	if err != nil {
		return loginOutcome{err: err}
	}
	err = runtime.LoadMyKeyFromExportedMap(keys)
	runtime.Close()
	if err != nil {
		return loginOutcome{err: fail("encryption_keys", "未取得可用的加密金鑰，請重新掃描 QR code 登入。")}
	}
	s := &savedSession{Version: 1, AccessToken: token, Certificate: result.Certificate, MID: profile.Mid, Keys: keys}
	if s.Certificate == "" && previous != nil && previous.MID == s.MID {
		s.Certificate = previous.Certificate
	}
	if result.TokenV3IssueResult != nil {
		s.RefreshToken = result.TokenV3IssueResult.RefreshToken
		s.RefreshAt = refreshTime(result.TokenV3IssueResult)
	}
	return loginOutcome{session: s, profile: profile}
}

// accountProblem checks that the verified profile and the login response name
// the same account. When the profile omits its MID, the login response's MID
// is used. The returned diagnosis describes MID shapes, never MID values.
func accountProblem(profile *line.Profile, resultMID string) string {
	if profile == nil {
		return "profile-missing"
	}
	if profile.Mid == "" && validMID(resultMID) {
		profile.Mid = resultMID
	}
	if profile.Mid == "" {
		return "profile-mid-empty, login " + midShape(resultMID)
	}
	if !validMID(profile.Mid) {
		return "profile-mid-format " + midShape(profile.Mid)
	}
	if resultMID != "" && resultMID != profile.Mid {
		return "mid-mismatch login " + midShape(resultMID) + " / profile " + midShape(profile.Mid)
	}
	return ""
}

func midShape(mid string) string {
	if mid == "" {
		return "empty"
	}
	chars := "hex"
	for _, c := range mid[1:] {
		switch {
		case c >= '0' && c <= '9', c >= 'a' && c <= 'f':
		case c >= 'A' && c <= 'F':
			if chars == "hex" {
				chars = "HEX"
			}
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z':
			chars = "alnum"
		default:
			return fmt.Sprintf("len=%d prefix=%q chars=other", len(mid), mid[:1])
		}
	}
	return fmt.Sprintf("len=%d prefix=%q chars=%s", len(mid), mid[:1], chars)
}

func exportLoginKeys(result *line.LoginResult, c *line.Client) (map[string]string, error) {
	if result.NoE2EE {
		return nil, errors.New("Letter Sealing disabled")
	}
	if result.EncryptedKeyChain == "" || result.E2EEPublicKey == "" {
		return map[string]string{}, nil
	}
	if result.Attempt == nil || result.Attempt.Runner == nil {
		return nil, errors.New("missing login runner")
	}
	mgr := e2ee.NewManagerWithRunner(result.Attempt.Runner)
	c.LoginAttempt = result.Attempt
	identity, err := c.GetEncryptedIdentityV3()
	if err != nil {
		return nil, err
	}
	if err = mgr.InitStorage(identity.WrappedNonce, identity.KDFParameter1, identity.KDFParameter2); err != nil {
		return nil, err
	}
	return mgr.InitFromLoginKeyChain(result.E2EEPublicKey, result.EncryptedKeyChain)
}
func mergeKeys(previous, current map[string]string) map[string]string {
	result := make(map[string]string, len(previous)+len(current))
	for k, v := range previous {
		result[k] = v
	}
	for k, v := range current {
		result[k] = v
	}
	return result
}
func (b *backend) handleEvent(ev workerEvent) {
	if ev.kind == "stream" || ev.kind == "stream_error" {
		b.handleStreamEvent(ev)
		return
	}
	if ev.generation != b.generation {
		return
	}
	if ev.kind == "auth" {
		b.emit("auth", ev.data)
		return
	}
	result, ok := ev.data.(loginOutcome)
	if !ok {
		return
	}
	b.loginCancel = nil
	if result.stored {
		b.stored = true
		b.previousSession = result.session
	}
	if result.err != nil {
		if errors.Is(result.err, errNoSession) {
			b.sessionEvent("logged_out", "請使用手機 LINE 掃描 QR code 登入。")
			return
		}
		text := userError(result.err).Message
		if line.IsLoggedOut(result.err) || line.IsRefreshRequired(result.err) || line.HTTPStatusCode(result.err) == 401 {
			text = "LINE 登入已過期，請重新登入。"
		}
		b.emit("auth", map[string]any{"stage": "error", "message": text})
		b.sessionEvent("logged_out", text)
		return
	}
	crypto, err := e2ee.NewManager()
	if err == nil {
		err = crypto.LoadMyKeyFromExportedMap(result.session.Keys)
	}
	if err != nil {
		if crypto != nil {
			crypto.Close()
		}
		b.sessionEvent("logged_out", "加密金鑰無法載入，請重新登入。")
		return
	}
	b.crypto = crypto
	b.session = result.session
	b.previousSession = nil
	p := result.profile
	b.region = p.RegionCode
	b.data.Profile = &person{ID: p.Mid, Name: p.DisplayName, Status: p.StatusMessage, Avatar: avatarURL(p.PicturePath)}
	b.saveSession()
	if b.session.Revision == 0 {
		if rev, err := b.api().GetLastOpRevisionContext(b.ctx); err == nil {
			b.session.Revision = rev
		}
	}
	b.preservePhoneNotifications()
	b.pushPreserved = true
	if err := b.refreshSnapshot(); err != nil {
		b.notice("登入成功，但聊天清單暫時無法同步。請稍後重新整理。")
	}
	b.sessionEvent("ready", "已連線至 LINE。")
	b.startStream()
}

// preservePhoneNotifications keeps push notifications on the user's phone
// while this desktop session is connected. LINE silences the primary device
// when a secondary (Chrome) client logs in unless notificationDisabledWithSub
// is explicitly false; upstream beeper/line does the same on connect. It is
// best effort: a failure must not block an otherwise healthy session.
func (b *backend) preservePhoneNotifications() {
	if b.demo || b.session == nil {
		return
	}
	disabled := false
	b.sequence++
	if err := b.api().UpdateSettingsAttributes2Context(b.ctx, b.sequence, []int{line.SettingsAttributeNotificationDisabledWithSub}, line.Settings{NotificationDisabledWithSub: &disabled}); err != nil && !isAuthError(err) {
		b.notice("無法確認手機推播設定；若手機收不到 LINE 通知，請重新登入。")
	}
}
func (b *backend) saveSession() {
	if b.session == nil {
		return
	}
	if err := b.store.Save(b.ctx, b.session); err != nil {
		b.persistent = false
		b.notice(userError(err).Message)
	} else {
		b.persistent = true
		b.stored = true
	}
}
