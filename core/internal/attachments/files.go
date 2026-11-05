package attachments

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/beeper/line/pkg/line"
)

const MaxFileSize = 25 << 20

var ErrFileSize = errors.New("檔案大小超過 25 MiB 限制。")
var ErrMediaFlow = errors.New("此對話未提供加密檔案傳送流程，檔案未送出。")

type PreparedFile struct {
	ContentType int
	Metadata    map[string]string
	Payload     []byte
	FileName    string
	Size        int64
}

func ReadFile(path string) ([]byte, error) {
	if !filepath.IsAbs(path) {
		return nil, errors.New("請選擇本機檔案的完整路徑。")
	}
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, errors.New("無法讀取檔案。")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("只能傳送一般檔案。")
	}
	if info.Size() > MaxFileSize {
		return nil, ErrFileSize
	}
	data, err := io.ReadAll(io.LimitReader(file, MaxFileSize+1))
	if err != nil {
		return nil, errors.New("無法讀取檔案。")
	}
	if len(data) > MaxFileSize {
		return nil, ErrFileSize
	}
	return data, nil
}

func PrepareFile(ctx context.Context, client *line.Client, chatID, path string) (*PreparedFile, error) {
	data, err := ReadFile(path)
	if err != nil {
		return nil, err
	}
	flow, err := client.DetermineMediaMessageFlow(chatID)
	if err != nil {
		return nil, err
	}
	if flow == nil || flow.FlowMap["14"] != 2 {
		return nil, ErrMediaFlow
	}
	encrypted, key, err := EncryptBinary(data)
	if err != nil {
		return nil, err
	}
	limitOBS(client)
	oid, err := client.UploadOBSWithSID(encrypted, "emf")
	if err != nil {
		return nil, err
	}
	name := filepath.Base(path)
	payload, err := json.Marshal(map[string]string{"fileName": name, "keyMaterial": key})
	if err != nil {
		return nil, err
	}
	return &PreparedFile{ContentType: 14, FileName: name, Size: int64(len(data)), Payload: payload, Metadata: map[string]string{"FILE_NAME": name, "FILE_SIZE": strconv.Itoa(len(data)), "contentType": "14", "OID": oid, "SID": "emf"}}, nil
}

func DownloadFile(ctx context.Context, client *line.Client, raw *line.Message, payload []byte, path string) error {
	if raw == nil || raw.ContentType != 14 {
		return errors.New("此版本只能下載一般檔案附件。")
	}
	if !filepath.IsAbs(path) {
		return errors.New("請選擇完整的儲存路徑。")
	}
	if _, err := os.Lstat(path); err == nil {
		return errors.New("目的檔案已存在，請選擇新的檔名。")
	} else if !errors.Is(err, os.ErrNotExist) {
		return errors.New("無法存取儲存位置。")
	}
	var decoded struct {
		KeyMaterial string `json:"keyMaterial"`
	}
	oid, sid := raw.ID, "m"
	encrypted := len(raw.Chunks) > 0
	if encrypted {
		if json.Unmarshal(payload, &decoded) != nil || decoded.KeyMaterial == "" {
			return errors.New("缺少此附件的解密金鑰，請重新載入聊天記錄。")
		}
		oid = raw.ContentMetadata["OID"]
		sid = raw.ContentMetadata["SID"]
		if oid == "" || sid != "emf" {
			return errors.New("尚未支援此附件格式。")
		}
	} else if raw.ContentMetadata["ENC_KM"] != "" {
		return errors.New("加密附件缺少可驗證的訊息內容。")
	}
	limitOBS(client)
	data, err := client.DownloadOBSWithSID(ctx, oid, raw.ID, sid)
	if err != nil {
		return err
	}
	if encrypted {
		data, err = DecryptBinary(data, decoded.KeyMaterial)
		if err != nil {
			return err
		}
	}
	if len(data) > MaxFileSize {
		return ErrFileSize
	}
	return SaveFile(path, data)
}

func SaveFile(path string, data []byte) error {
	if !filepath.IsAbs(path) {
		return errors.New("請選擇完整的儲存路徑。")
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".line-download-*")
	if err != nil {
		return errors.New("無法建立下載檔案。")
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(data); err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.New("儲存檔案失敗。")
	}
	if err = os.Link(file.Name(), path); err != nil {
		return errors.New("無法儲存檔案；請確認目的檔名尚未存在，且有寫入權限。")
	}
	return nil
}

type limitTransport struct{ base http.RoundTripper }

func (t limitTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	response, err := t.base.RoundTrip(req)
	if err != nil {
		return nil, err
	}
	limit := int64(MaxFileSize + 32)
	if req.Method != "GET" || strings.HasSuffix(req.URL.Path, "object_info.obs") || response.StatusCode != 200 {
		limit = 65536
	}
	if response.ContentLength > limit {
		response.Body.Close()
		return nil, ErrFileSize
	}
	response.Body = &limitedBody{ReadCloser: response.Body, remaining: limit}
	return response, nil
}

type limitedBody struct {
	io.ReadCloser
	remaining int64
}

func (b *limitedBody) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	if b.remaining == 0 {
		var probe [1]byte
		n, err := b.ReadCloser.Read(probe[:])
		if n > 0 {
			return 0, ErrFileSize
		}
		return 0, err
	}
	if int64(len(p)) > b.remaining {
		p = p[:b.remaining]
	}
	n, err := b.ReadCloser.Read(p)
	b.remaining -= int64(n)
	return n, err
}
func limitOBS(client *line.Client) {
	base := client.OBSClient
	if base == nil {
		base = client.HTTPClient
	}
	if base == nil {
		base = http.DefaultClient
	}
	copyClient := *base
	transport := copyClient.Transport
	if transport == nil {
		transport = http.DefaultTransport
	}
	copyClient.Transport = limitTransport{transport}
	client.OBSClient = &copyClient
}
