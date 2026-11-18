package attachments

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/beeper/line/pkg/line"
)

// MaxVideoSize bounds a downloaded video. LINE sends videos of up to five
// minutes, compressed well below this.
const MaxVideoSize = 300 << 20

// videoChunk is the size of the ciphertext pieces whose SHA-256 a video's MAC
// covers (LINE checks a video while it streams).
const videoChunk = 128 << 10

// mediaKeys are the keys a sealed photo or video may be encrypted with: the
// one in the Letter Sealing payload, then the one in the metadata.
func mediaKeys(raw *line.Message, payload []byte) []string {
	keys := []string{}
	var body struct {
		KeyMaterial string `json:"keyMaterial"`
	}
	if len(payload) > 0 && json.Unmarshal(payload, &body) == nil && body.KeyMaterial != "" {
		keys = append(keys, body.KeyMaterial)
	}
	if km := raw.ContentMetadata["ENC_KM"]; km != "" && (len(keys) == 0 || keys[0] != km) {
		keys = append(keys, km)
	}
	return keys
}

// DownloadVideo downloads a video message, or its preview picture, and
// decrypts it. A sealed video lives under the message's OID with the SID
// "emv" and its preview under OID + "__ud-preview"; older, unsealed ones
// under the message ID. The preview is sealed like a photo, the video like a
// file except for its MAC (see DecryptVideo).
func DownloadVideo(ctx context.Context, client *line.Client, raw *line.Message, payload []byte, preview bool) ([]byte, error) {
	if raw == nil || raw.ContentType != 2 {
		return nil, errors.New("不是影片訊息。")
	}
	meta := raw.ContentMetadata
	keys := mediaKeys(raw, payload)
	encrypted := len(keys) > 0 || len(raw.Chunks) > 0
	if preview {
		limitOBS(client)
	} else {
		limitOBSTo(client, MaxVideoSize+32)
	}
	opts := line.OBSDownloadOptions{OBSPop: meta["OBS_POP"]}
	var data []byte
	var err error
	switch {
	case !preview && meta["DOWNLOAD_URL"] != "":
		data, err = client.DownloadOBSPublicResource(ctx, meta["DOWNLOAD_URL"])
	case preview && meta["PREVIEW_URL"] != "":
		data, err = client.DownloadOBSPublicResource(ctx, meta["PREVIEW_URL"])
	case meta["OID"] != "":
		oid, sid := meta["OID"], meta["SID"]
		if sid == "" {
			sid = "emv"
		}
		if preview {
			oid += "__ud-preview"
		}
		data, err = client.DownloadOBSWithSIDOptions(ctx, oid, raw.ID, sid, opts)
	default:
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
		return nil, errors.New("缺少此影片的解密金鑰，請重新載入聊天記錄。")
	}
	var lastErr error
	for _, key := range keys {
		var plain []byte
		if preview {
			plain, err = DecryptBinary(data, key)
		} else {
			plain, err = DecryptVideo(data, key)
		}
		if err == nil {
			return plain, nil
		}
		lastErr = err
	}
	return nil, fmt.Errorf("影片解密失敗：%w", lastErr)
}

// DecryptVideo decrypts a sealed video. LINE encrypts it like a file
// (AES-CTR with keys from HKDF "FileEncryption") but its HMAC covers the
// SHA-256 of each 128 KiB chunk of the ciphertext instead of the ciphertext
// itself. A video whose MAC covers the whole ciphertext, as a file's does,
// is accepted too.
func DecryptVideo(ciphertext []byte, keyMaterial string) ([]byte, error) {
	key, err := base64.StdEncoding.DecodeString(keyMaterial)
	if err != nil || len(key) != 32 || len(ciphertext) < sha256.Size {
		return nil, ErrAuthentication
	}
	stream, mac, err := binaryCipher(key)
	if err != nil {
		return nil, ErrAuthentication
	}
	body := ciphertext[:len(ciphertext)-sha256.Size]
	h := hmac.New(sha256.New, mac)
	for start := 0; start < len(body); start += videoChunk {
		sum := sha256.Sum256(body[start:min(start+videoChunk, len(body))])
		h.Write(sum[:])
	}
	if !hmac.Equal(h.Sum(nil), ciphertext[len(body):]) {
		return DecryptBinary(ciphertext, keyMaterial)
	}
	plain := make([]byte, len(body))
	stream.XORKeyStream(plain, body)
	return plain, nil
}
