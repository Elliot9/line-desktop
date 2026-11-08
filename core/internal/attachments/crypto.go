package attachments

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"io"

	"golang.org/x/crypto/hkdf"
)

var ErrAuthentication = errors.New("LINE file authentication failed")

// LINE's binary format and HKDF parameters follow beeper/line media.go, under MIT.
func EncryptBinary(plain []byte) ([]byte, string, error) {
	key := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, key); err != nil {
		return nil, "", errors.New("could not generate LINE file encryption key")
	}
	return encryptBinaryWithKey(plain, key)
}

func encryptBinaryWithKey(plain, key []byte) ([]byte, string, error) {
	stream, mac, err := binaryCipher(key)
	if err != nil {
		return nil, "", err
	}
	ciphertext := make([]byte, len(plain)+sha256.Size)
	stream.XORKeyStream(ciphertext[:len(plain)], plain)
	h := hmac.New(sha256.New, mac)
	h.Write(ciphertext[:len(plain)])
	copy(ciphertext[len(plain):], h.Sum(nil))
	return ciphertext, base64.StdEncoding.EncodeToString(key), nil
}

func decodeKey(keyMaterial string) ([]byte, error) {
	key, err := base64.StdEncoding.DecodeString(keyMaterial)
	if err != nil || len(key) != 32 {
		return nil, ErrAuthentication
	}
	return key, nil
}

func DecryptBinary(ciphertext []byte, keyMaterial string) ([]byte, error) {
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
	h.Write(body)
	if !hmac.Equal(h.Sum(nil), ciphertext[len(body):]) {
		return nil, ErrAuthentication
	}
	plain := make([]byte, len(body))
	stream.XORKeyStream(plain, body)
	return plain, nil
}

func binaryCipher(key []byte) (cipher.Stream, []byte, error) {
	if len(key) != 32 {
		return nil, nil, ErrAuthentication
	}
	derived := make([]byte, 76)
	if _, err := io.ReadFull(hkdf.New(sha256.New, key, nil, []byte("FileEncryption")), derived); err != nil {
		return nil, nil, errors.New("could not derive LINE file encryption keys")
	}
	block, err := aes.NewCipher(derived[:32])
	if err != nil {
		return nil, nil, errors.New("could not create LINE file cipher")
	}
	counter := make([]byte, aes.BlockSize)
	copy(counter, derived[64:76])
	return cipher.NewCTR(block, counter), derived[32:64], nil
}
