package attachments

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	_ "image/gif"
	"image/jpeg"
	_ "image/png"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/beeper/line/pkg/line"
)

// MaxImageSize is LINE's photo limit; larger pictures are sent as files.
const MaxImageSize = 20 << 20

var ErrNotImage = errors.New("不是可傳送的圖片格式（支援 JPEG、PNG、GIF）。")

// PreparedImage is an image message ready for sendPayload (E2EE) or for a
// plain send followed by PlainUpload (servers that disable media E2EE).
type PreparedImage struct {
	ContentType int
	Metadata    map[string]string
	Payload     []byte
	Plain       bool
	PlainData   []byte
	Width       int
	Height      int
	FileName    string
}

// PrepareImage follows beeper/line send_message.go (MsgImage): EXIF is
// stripped from JPEGs, a ≤1280 px preview is generated, and depending on
// DetermineMediaMessageFlow the picture is encrypted and uploaded to OBS
// ("emi", preview at OID__ud-preview) or kept for a post-send plain upload.
func PrepareImage(ctx context.Context, client *line.Client, chatID, path string, data []byte) (*PreparedImage, error) {
	var err error
	if data == nil {
		data, err = ReadFile(path)
		if err != nil {
			return nil, err
		}
	}
	if len(data) > MaxImageSize {
		return nil, ErrFileSize
	}
	cfg, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, ErrNotImage
	}
	extension := map[string]string{"jpeg": "jpg", "png": "png", "gif": "gif"}[format]
	if extension == "" {
		return nil, ErrNotImage
	}
	if extension == "jpg" {
		data = StripJPEGMetadata(data)
	}
	name := filepath.Base(path)
	if path == "" || name == "." || name == "/" {
		name = "image." + extension
	}
	preview, pw, ph, previewErr := Thumbnail(data, 1280)
	flow, err := client.DetermineMediaMessageFlow(chatID)
	if err != nil {
		return nil, err
	}
	metadata := map[string]string{"FILE_SIZE": strconv.Itoa(len(data)), "contentType": "1", "FILE_NAME": name}
	info := map[string]any{"category": "original", "fileSize": len(data), "extension": extension}
	if raw, err := json.Marshal(info); err == nil {
		metadata["MEDIA_CONTENT_INFO"] = string(raw)
	}
	out := &PreparedImage{ContentType: 1, Metadata: metadata, Width: cfg.Width, Height: cfg.Height, FileName: name}
	if previewErr == nil {
		thumb, _ := json.Marshal(map[string]int{"width": pw, "height": ph})
		metadata["MEDIA_THUMB_INFO"] = string(thumb)
	}
	if flow == nil || flow.FlowMap["1"] != 2 {
		out.Plain = true
		out.PlainData = data
		return out, nil
	}
	encrypted, key, err := EncryptBinary(data)
	if err != nil {
		return nil, err
	}
	limitOBS(client)
	oid, err := client.UploadOBS(encrypted)
	if err != nil {
		return nil, err
	}
	if previewErr == nil {
		if sealed, err := encryptBinaryWithKeyB64(preview, key); err == nil {
			if client.UploadOBSWithOID(sealed, oid+"__ud-preview") != nil {
				delete(metadata, "MEDIA_THUMB_INFO")
			}
		}
	}
	metadata["OID"] = oid
	metadata["SID"] = "emi"
	metadata["ENC_KM"] = key
	out.Payload, _ = json.Marshal(map[string]string{"keyMaterial": key})
	return out, nil
}

// PlainUpload stores the picture of a plain (non-E2EE) image message after
// the message has been sent, at r/talk/m/{messageID}.
func PlainUpload(client *line.Client, messageID string, data []byte) error {
	limitOBS(client)
	return client.UploadOBSPlain(data, messageID, "image")
}

func encryptBinaryWithKeyB64(plain []byte, keyB64 string) ([]byte, error) {
	key, err := decodeKey(keyB64)
	if err != nil {
		return nil, err
	}
	sealed, _, err := encryptBinaryWithKey(plain, key)
	return sealed, err
}

// DownloadImage fetches an image message's picture into memory and decrypts
// it. preview=true asks for the ≤1280 px "__ud-preview" object that LINE
// clients show in the chat bubble; callers fall back to the original.
func DownloadImage(ctx context.Context, client *line.Client, raw *line.Message, payload []byte, preview bool) ([]byte, error) {
	if raw == nil || raw.ContentType != 1 {
		return nil, errors.New("不是照片訊息。")
	}
	meta := raw.ContentMetadata
	keys := []string{}
	var body struct {
		KeyMaterial string `json:"keyMaterial"`
	}
	if len(payload) > 0 && json.Unmarshal(payload, &body) == nil && body.KeyMaterial != "" {
		keys = append(keys, body.KeyMaterial)
	}
	if km := meta["ENC_KM"]; km != "" && (len(keys) == 0 || keys[0] != km) {
		keys = append(keys, km)
	}
	encrypted := len(keys) > 0 || len(raw.Chunks) > 0
	limitOBS(client)
	var data []byte
	var err error
	switch {
	case meta["DOWNLOAD_URL"] != "":
		data, err = client.DownloadOBSPublicResource(ctx, meta["DOWNLOAD_URL"])
	case meta["OID"] != "":
		oid := meta["OID"]
		if preview {
			oid += "__ud-preview"
		}
		data, err = client.DownloadOBSWithOptions(ctx, oid, raw.ID, line.OBSDownloadOptions{OBSPop: meta["OBS_POP"]})
	default:
		opts := line.OBSDownloadOptions{OBSPop: meta["OBS_POP"]}
		if !preview && strings.Contains(meta["MEDIA_CONTENT_INFO"], `"original"`) {
			opts.TID = "original"
		}
		oid := raw.ID
		if preview {
			oid += "__ud-preview"
		}
		data, err = client.DownloadOBSWithSIDOptions(ctx, oid, "", "m", opts)
		encrypted = len(keys) > 0
	}
	if err != nil {
		return nil, err
	}
	if !encrypted {
		return data, nil
	}
	if len(keys) == 0 {
		return nil, errors.New("缺少此照片的解密金鑰，請重新載入聊天記錄。")
	}
	var lastErr error
	for _, key := range keys {
		plain, err := DecryptBinary(data, key)
		if err == nil {
			return plain, nil
		}
		lastErr = err
	}
	return nil, fmt.Errorf("照片解密失敗：%w", lastErr)
}

// StripJPEGMetadata removes APP1 (EXIF/XMP) segments, which carry GPS data,
// as beeper/line does before upload.
func StripJPEGMetadata(data []byte) []byte {
	if len(data) < 4 || data[0] != 0xFF || data[1] != 0xD8 {
		return data
	}
	out := make([]byte, 0, len(data))
	out = append(out, data[:2]...)
	pos := 2
	for pos+4 <= len(data) && data[pos] == 0xFF {
		marker := data[pos+1]
		if marker == 0xDA {
			break
		}
		length := int(data[pos+2])<<8 | int(data[pos+3])
		end := pos + 2 + length
		if length < 2 || end > len(data) {
			return data
		}
		if marker != 0xE1 {
			out = append(out, data[pos:end]...)
		}
		pos = end
	}
	return append(out, data[pos:]...)
}

// Thumbnail scales an image to fit maxDim with a box filter and encodes it
// as JPEG (quality 85), returning the new size.
func Thumbnail(data []byte, maxDim int) ([]byte, int, int, error) {
	src, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, 0, 0, err
	}
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	if w <= 0 || h <= 0 {
		return nil, 0, 0, ErrNotImage
	}
	nw, nh := w, h
	if w > maxDim || h > maxDim {
		if w >= h {
			nw, nh = maxDim, max(1, h*maxDim/w)
		} else {
			nw, nh = max(1, w*maxDim/h), maxDim
		}
	}
	dst := image.NewRGBA(image.Rect(0, 0, nw, nh))
	for y := 0; y < nh; y++ {
		y0, y1 := b.Min.Y+y*h/nh, b.Min.Y+max((y+1)*h/nh, y*h/nh+1)
		for x := 0; x < nw; x++ {
			x0, x1 := b.Min.X+x*w/nw, b.Min.X+max((x+1)*w/nw, x*w/nw+1)
			var r, g, bl, a, n uint64
			for sy := y0; sy < y1; sy++ {
				for sx := x0; sx < x1; sx++ {
					cr, cg, cb, ca := src.At(sx, sy).RGBA()
					r, g, bl, a, n = r+uint64(cr), g+uint64(cg), bl+uint64(cb), a+uint64(ca), n+1
				}
			}
			// Flatten transparency onto white, as JPEG has no alpha.
			alpha := a / n
			white := uint64(0xffff) - alpha
			dst.Set(x, y, color.RGBA64{uint16(r/n + white), uint16(g/n + white), uint16(bl/n + white), 0xffff})
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, dst, &jpeg.Options{Quality: 85}); err != nil {
		return nil, 0, 0, err
	}
	return buf.Bytes(), nw, nh, nil
}
