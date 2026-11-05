package attachments

import (
 "bytes"
 "encoding/base64"
 "encoding/json"
 "errors"
 "io"
 "os"
 "path/filepath"
 "strings"
 "testing"
 "time"
 "github.com/beeper/line/pkg/line"
)

func TestFileFixtureAndTamperRejection(t *testing.T){
 // Fixture produced independently with Python HKDF/HMAC and OpenSSL AES-256-CTR.
 const key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
 ciphertext,_:=base64.StdEncoding.DecodeString("GSlIlouPP2lz30Kzzq4deGGOEctHcJjnsoEC3bA94B0GECurDLx6MGBQMQFp7SQtP9803ceVRs8=")
 plain,err:=DecryptBinary(ciphertext,key)
 if err!=nil||string(plain)!="LINE file fixture 測試"{t.Fatalf("known file could not decrypt: %v",err)}
 for _,index:=range []int{0,len(ciphertext)-1}{tampered:=bytes.Clone(ciphertext);tampered[index]^=1;if _,err:=DecryptBinary(tampered,key);!errors.Is(err,ErrAuthentication){t.Fatal("tampered file was accepted")}}
 rawKey,_:=base64.StdEncoding.DecodeString(key)
 encrypted,_,err:=encryptBinaryWithKey(plain,rawKey)
 if err!=nil||!bytes.Equal(encrypted,ciphertext){t.Fatal("encryption does not match independent fixture")}
 for _,invalid:=range []string{"bad",base64.StdEncoding.EncodeToString(make([]byte,31))}{if _,err:=DecryptBinary(ciphertext,invalid);err==nil{t.Fatal("invalid key accepted")}}
}

func TestFileSaveDoesNotOverwrite(t *testing.T){
 directory:=t.TempDir();path:=filepath.Join(directory,"download.txt")
 if err:=SaveFile(path,[]byte("first"));err!=nil{t.Fatal(err)}
 if err:=SaveFile(path,[]byte("second"));err==nil{t.Fatal("existing file overwritten")}
 data,_:=os.ReadFile(path);if string(data)!="first"{t.Fatal("existing file changed")}
 info,_:=os.Stat(path);if info.Mode().Perm()!=0600{t.Fatal("download is not private")}
 if _,err:=ReadFile(directory);err==nil{t.Fatal("directory accepted as attachment")}
 oversized:=filepath.Join(directory,"large.bin");file,_:=os.Create(oversized);file.Truncate(MaxFileSize+1);file.Close()
 if _,err:=ReadFile(oversized);!errors.Is(err,ErrFileSize){t.Fatal("oversized file accepted")}
}

func TestBoundedDownload(t *testing.T){
 for _,size:=range []int{3,4}{reader:=&limitedBody{ReadCloser:io.NopCloser(strings.NewReader(strings.Repeat("a",size))),remaining:3};data,err:=io.ReadAll(reader);if len(data)>3{t.Fatal("download exceeded bound")};if size==4&&!errors.Is(err,ErrFileSize){t.Fatal("oversized body accepted")};if size==3&&err!=nil{t.Fatal(err)}}
}

func TestOwnedStickerCatalog(t *testing.T){
 var product line.ShopProduct
 err:=json.Unmarshal([]byte(`{"id":"123","name":"Owned pack","latestVersion":1,"validUntil":-1,"productTypeSummary":{"stickerSummary":{"stickerResourceType":2,"stickerIdRanges":[{"start":100,"size":3}]}}}`),&product);if err!=nil{t.Fatal(err)}
 catalog,err:=CatalogFromProducts([]line.ShopProduct{product},time.Now());if err!=nil{t.Fatal(err)}
 if len(catalog.Packs())!=1||len(catalog.Packs()[0].Stickers)!=3{t.Fatal("owned stickers missing")}
 msg,err:=catalog.StickerMessage("chat","123","101");if err!=nil||msg.ContentMetadata["STKOPT"]!="A"||msg.ContentMetadata["STKVER"]!="1"{t.Fatal("native metadata incorrect")}
 if _,err=catalog.StickerMessage("chat","123","999");err==nil{t.Fatal("unowned sticker accepted")}
 product.ValidUntil=json.Number("1")
 expired,_:=CatalogFromProducts([]line.ShopProduct{product},time.Now());if len(expired.Packs())!=0{t.Fatal("expired pack was offered")}
 product.ValidUntil=json.Number("-1");product.ProductTypeSummary.Sticker.Ranges[0].Size=1001
 invalid,_:=CatalogFromProducts([]line.ShopProduct{product},time.Now());if len(invalid.Packs())!=0{t.Fatal("excessive range accepted")}
}
