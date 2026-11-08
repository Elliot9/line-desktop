package attachments

import (
	"bytes"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"testing"
)

func TestThumbnailFitsAndFlattens(t *testing.T) {
	src := image.NewNRGBA(image.Rect(0, 0, 3000, 1500))
	for y := 0; y < 1500; y++ {
		for x := 0; x < 3000; x++ {
			src.Set(x, y, color.NRGBA{0, 0, 0, 0})
		}
	}
	var buf bytes.Buffer
	_ = png.Encode(&buf, src)
	out, w, h, err := Thumbnail(buf.Bytes(), 1280)
	if err != nil || w != 1280 || h != 640 {
		t.Fatalf("thumbnail %dx%d %v", w, h, err)
	}
	img, err := jpeg.Decode(bytes.NewReader(out))
	if err != nil {
		t.Fatal(err)
	}
	if r, _, _, _ := img.At(10, 10).RGBA(); r < 0xf000 {
		t.Fatal("transparent pixels should become white")
	}
}

func TestStripJPEGMetadataDropsAPP1(t *testing.T) {
	var buf bytes.Buffer
	_ = jpeg.Encode(&buf, image.NewGray(image.Rect(0, 0, 4, 4)), nil)
	plain := buf.Bytes()
	exif := append([]byte{0xFF, 0xE1, 0x00, 0x08}, []byte("Exif\x00\x00")...)
	withExif := append(append(append([]byte{}, plain[:2]...), exif...), plain[2:]...)
	stripped := StripJPEGMetadata(withExif)
	if bytes.Contains(stripped, []byte("Exif")) || !bytes.Equal(stripped, plain) {
		t.Fatal("APP1 segment not removed cleanly")
	}
}

func TestPreviewEncryptionUsesSameKey(t *testing.T) {
	sealed, key, err := EncryptBinary([]byte("original"))
	if err != nil {
		t.Fatal(err)
	}
	preview, err := encryptBinaryWithKeyB64([]byte("preview"), key)
	if err != nil {
		t.Fatal(err)
	}
	for in, want := range map[string]string{string(sealed): "original", string(preview): "preview"} {
		got, err := DecryptBinary([]byte(in), key)
		if err != nil || string(got) != want {
			t.Fatalf("decrypt %q: %v", got, err)
		}
	}
}
