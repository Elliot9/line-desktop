package main

import (
 "context"
 "encoding/json"
 "io"
	"os"
	"errors"
	"fmt"
 "testing"
 "time"

	"github.com/beeper/line/pkg/line"
)

func TestSecretServiceRoundTrip(t *testing.T) {
 if os.Getenv("LINE_TEST_KEYRING")!="1" {t.Skip("run in a disposable D-Bus session with LINE_TEST_KEYRING=1")}
 store:=newKeyringStore()
 ctx,cancel:=context.WithTimeout(context.Background(),20*time.Second);defer cancel()
 if _,err:=store.Load(ctx);!errors.Is(err,errNoSession){t.Fatal("test requires an empty isolated keyring")}
 record:=&savedSession{Version:1,AccessToken:"fixture-access",RefreshToken:"fixture-refresh",MID:"u11111111111111111111111111111111",Keys:map[string]string{"1":"fixture-key"}}
 if err:=store.Save(ctx,record);err!=nil{t.Fatal(err)}
 defer store.Clear(context.Background())
 restored,err:=store.Load(ctx);if err!=nil{t.Fatal(err)}
 if restored.AccessToken!=record.AccessToken||restored.Keys["1"]!=record.Keys["1"]{t.Fatal("saved session did not round trip")}
 if err:=store.Clear(ctx);err!=nil{t.Fatal(err)}
 if _,err:=store.Load(ctx);!errors.Is(err,errNoSession){t.Fatal("logout did not remove saved session")}
}

type waitingStore struct{ canceled chan struct{} }
func (s *waitingStore) Load(ctx context.Context)(*savedSession,error){<-ctx.Done();close(s.canceled);return nil,ctx.Err()}
func (s *waitingStore) Save(context.Context,*savedSession)error{return nil}
func (s *waitingStore) Clear(context.Context)error{return nil}

func TestLoginCancelStopsPendingRestore(t *testing.T){
 in,writer:=io.Pipe();reader,out:=io.Pipe()
 store:=&waitingStore{canceled:make(chan struct{})}
 done:=make(chan error,1)
 ctx,cancel:=context.WithTimeout(context.Background(),5*time.Second);defer cancel()
 go func(){done<-serve(ctx,in,out,false,store);out.Close()}()
 defer reader.Close();defer writer.Close()
 decoder:=json.NewDecoder(reader)
 call:=func(id,method string){
  t.Helper()
  if _,err:=io.WriteString(writer,`{"id":"`+id+`","method":"`+method+`","params":{}}`+"\n");err!=nil{t.Fatal(err)}
  for{var envelope map[string]any;if err:=decoder.Decode(&envelope);err!=nil{t.Fatal(err)};if envelope["id"]==id{if envelope["ok"]!=true{t.Fatal("request failed")};break}}
 }
 call("1","initialize");call("2","login.cancel")
 select{case <-store.canceled:case <-ctx.Done():t.Fatal("login cancel left keyring restore running")}
 call("3","shutdown")
 select{case err:=<-done:if err!=nil{t.Fatal(err)};case <-ctx.Done():t.Fatal("shutdown left workers running")}
}

func TestSessionKeysMergeWithoutCrossAccountMutation(t *testing.T){
 old:=map[string]string{"1":"old"};merged:=mergeKeys(old,map[string]string{"2":"new"})
 if merged["1"]!="old"||merged["2"]!="new"{t.Fatal("historical key lost")}
 merged["1"]="changed";if old["1"]!="old"{t.Fatal("previous session mutated")}
}

func TestStaleAuthCannotRestoreCanceledSession(t *testing.T){
 b:=&backend{generation:2,state:"logged_out"}
 b.handleEvent(workerEvent{generation:1,kind:"login",data:loginOutcome{session:&savedSession{AccessToken:"stale"}}})
 if b.session!=nil||b.state!="logged_out"{t.Fatal("stale login changed canceled session")}
}

// A QR/email login while the saved session is still being restored used to
// wipe the restored session and leave the app stuck "reconnecting".
func TestLoginRejectedWhileRestoring(t *testing.T) {
	in, writer := io.Pipe()
	reader, out := io.Pipe()
	store := &waitingStore{canceled: make(chan struct{})}
	done := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() { done <- serve(ctx, in, out, false, store); out.Close() }()
	defer reader.Close()
	defer writer.Close()
	decoder := json.NewDecoder(reader)
	call := func(id, method string) map[string]any {
		t.Helper()
		if _, err := io.WriteString(writer, `{"id":"`+id+`","method":"`+method+`","params":{}}`+"\n"); err != nil {
			t.Fatal(err)
		}
		for {
			var envelope map[string]any
			if err := decoder.Decode(&envelope); err != nil {
				t.Fatal(err)
			}
			if envelope["id"] == id {
				return envelope
			}
		}
	}
	call("1", "initialize")
	reply := call("2", "login.qr")
	errInfo, _ := reply["error"].(map[string]any)
	if reply["ok"] != false || errInfo["code"] != "restoring" {
		t.Fatalf("login during restore was not rejected: %v", reply)
	}
	// Cancelling the restore must allow logging in again.
	if reply := call("3", "login.cancel"); reply["ok"] != true {
		t.Fatalf("cancel failed: %v", reply)
	}
	<-store.canceled
	call("4", "shutdown")
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

type recordingStore struct{ saved, cleared int }

func (s *recordingStore) Load(context.Context) (*savedSession, error) { return nil, errNoSession }
func (s *recordingStore) Save(context.Context, *savedSession) error   { s.saved++; return nil }
func (s *recordingStore) Clear(context.Context) error                 { s.cleared++; return nil }

// Unticking 自動登入 keeps the session in memory only and removes an older
// keyring copy, so the next start shows the login page.
func TestForgetSkipsKeyring(t *testing.T) {
	store := &recordingStore{}
	b := &backend{ctx: context.Background(), store: store, session: &savedSession{AccessToken: "x"}, stored: true, forget: true}
	b.saveSession()
	if store.saved != 0 || store.cleared != 1 || b.persistent || b.stored {
		t.Fatalf("forget: saved=%d cleared=%d persistent=%v stored=%v", store.saved, store.cleared, b.persistent, b.stored)
	}
	b.forget = false
	b.saveSession()
	if store.saved != 1 || !b.persistent {
		t.Fatal("remembered session was not saved")
	}
}

func TestNoSealingOnlyForMissingKeys(t *testing.T) {
	cases := map[string]struct {
		err  error
		want bool
	}{
		"official account": {fmt.Errorf("recipient Letter Sealing unavailable: %w", line.ErrNoUsableE2EEPublicKey), true},
		"large group":      {errors.New(`API error 400: {"code":10051,"message":"RESPONSE_ERROR","data":{"name":"TalkException","message":"TalkException","code":100,"reason":"exceed max member","parameterMap":{}}}`), true},
		"network":          {errors.New("dial tcp: i/o timeout"), false},
		"key not found":    {errors.New(`API error 400: {"code":10051,"message":"RESPONSE_ERROR","data":{"name":"TalkException","code":5,"reason":"not found"}}`), false},
		"own key missing":  {errors.New("missing own private key"), false},
	}
	for name, c := range cases {
		if got := noSealing(c.err); got != c.want {
			t.Errorf("%s: noSealing=%v, want %v", name, got, c.want)
		}
	}
}
