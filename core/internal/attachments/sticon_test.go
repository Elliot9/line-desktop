package attachments

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/beeper/line/pkg/line"
)

func sticonProduct(id string, resourceType int) line.ShopProduct {
	var product line.ShopProduct
	product.ID, product.Name = id, "Moon"
	product.LatestVersion, product.ValidUntil = json.Number("3"), json.Number("-1")
	product.ProductTypeSummary.Sticon = &line.SticonSummary{ResourceType: resourceType}
	return product
}

type shopStub struct{ stickers, sticons []line.ShopProduct }

func (s shopStub) GetOwnedProducts(_ context.Context, shop, _ string) ([]line.ShopProduct, error) {
	if shop == line.SticonShop {
		return s.sticons, nil
	}
	return s.stickers, nil
}

func TestEmojiPacksLoadAndBuildReplace(t *testing.T) {
	meta := []byte(`{"orders":["001","002"],"altTexts":{"001":"smile"}}`)
	fetched := 0
	fetch := func(_ context.Context, id string) ([]byte, error) {
		fetched++
		if id == "bad" {
			return nil, errors.New("offline")
		}
		return meta, nil
	}
	shop := shopStub{sticons: []line.ShopProduct{sticonProduct("5ac1bfd5", 2), sticonProduct("bad", 1), sticonProduct("../x", 1)}}
	catalog, err := LoadCatalog(context.Background(), shop, "TW", time.Now(), fetch)
	if err != nil {
		t.Fatal(err)
	}
	packs := catalog.Packs()
	if fetched != 2 || len(packs) != 1 || packs[0].Kind != "emoji" || len(packs[0].Stickers) != 2 {
		t.Fatalf("unexpected packs %+v (fetched %d)", packs, fetched)
	}
	if !strings.HasSuffix(packs[0].Stickers[0].URL, "/sticonshop/v1/sticon/5ac1bfd5/android/001.png") {
		t.Fatalf("bad preview URL %q", packs[0].Stickers[0].URL)
	}
	if _, err := catalog.StickerMessage("u1", "5ac1bfd5", "001"); err == nil {
		t.Fatal("an emoji was sent as a sticker")
	}
	refs := []EmojiRef{{"5ac1bfd5", "001"}, {"5ac1bfd5", "002"}, {"5ac1bfd5", "001"}}
	body, resources, products, err := catalog.EmojiText("😀a\uFFFCb\uFFFC\uFFFC", refs, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if body != "😀a(smile)b(emoji)(smile)" || len(products) != 1 || products[0] != "5ac1bfd5" {
		t.Fatalf("body %q products %v", body, products)
	}
	// 😀 is two UTF-16 units, so the first emoji starts at 3.
	first := resources[0]
	if first.Start != 3 || first.End != 10 || first.ResourceType != "ANIMATION" || first.Version != 3 || resources[2].Start != 18 {
		t.Fatalf("resources %+v", resources)
	}
	if _, _, _, err := catalog.EmojiText("\uFFFC", []EmojiRef{{"5ac1bfd5", "999"}}, time.Now()); err == nil {
		t.Fatal("unowned emoji accepted")
	}
	if _, _, _, err := catalog.EmojiText("no placeholder", refs[:1], time.Now()); err == nil {
		t.Fatal("placeholder mismatch accepted")
	}
}
