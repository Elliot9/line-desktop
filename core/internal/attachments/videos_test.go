package attachments

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"testing"
)

// sealVideo encrypts like LINE's apps (and beeper/line's sender) do for a
// video: file encryption, with the MAC over the SHA-256 of each 128 KiB chunk.
func sealVideo(t *testing.T, plain []byte) ([]byte, string) {
	t.Helper()
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	stream, mac, err := binaryCipher(key)
	if err != nil {
		t.Fatal(err)
	}
	body := make([]byte, len(plain))
	stream.XORKeyStream(body, plain)
	h := hmac.New(sha256.New, mac)
	for start := 0; start < len(body); start += videoChunk {
		sum := sha256.Sum256(body[start:min(start+videoChunk, len(body))])
		h.Write(sum[:])
	}
	return append(body, h.Sum(nil)...), base64.StdEncoding.EncodeToString(key)
}

func TestDecryptVideo(t *testing.T) {
	plain := make([]byte, 3*videoChunk+12345) // several chunks and a short last one
	if _, err := rand.Read(plain); err != nil {
		t.Fatal(err)
	}
	sealed, key := sealVideo(t, plain)
	got, err := DecryptVideo(sealed, key)
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("chunked MAC: %v", err)
	}
	// Sealed the way files are (MAC over the ciphertext): accepted as well.
	whole, wholeKey, err := EncryptBinary(plain)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := DecryptVideo(whole, wholeKey); err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("whole MAC: %v", err)
	}
	tampered := append([]byte(nil), sealed...)
	tampered[2*videoChunk+7] ^= 1
	if _, err := DecryptVideo(tampered, key); !errors.Is(err, ErrAuthentication) {
		t.Fatalf("a changed byte was not caught: %v", err)
	}
	if _, err := DecryptVideo(sealed[:20], key); !errors.Is(err, ErrAuthentication) {
		t.Fatal("a cut-off video was accepted")
	}
	if _, err := DecryptVideo(sealed, wholeKey); !errors.Is(err, ErrAuthentication) {
		t.Fatal("the wrong key was accepted")
	}
}
