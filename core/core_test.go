package main

import (
 "context"
 "encoding/json"
 "io"
	"os"
	"errors"
 "testing"
 "time"
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
