package attachments

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"

	"github.com/beeper/line/pkg/line"
)

const ContentSticker = 7

type Sticker struct {
	ID        string `json:"id"`
	PackageID string `json:"package_id"`
	URL       string `json:"url"`
	// Text is an emoji's alt text, such as "(smile)"; empty for stickers.
	Text string `json:"text,omitempty"`
}

type Pack struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Stickers []Sticker `json:"stickers"`
	// Kind is "emoji" for LINE emoji (sticon) packs, which go inline in text.
	Kind string `json:"kind,omitempty"`
}

type ownedSticker struct {
	version string
	option  string
	hash    string
	text    string
	expiry  int64
	sticon  bool
}

type Catalog struct {
	packs []Pack
	owned map[string]map[string]ownedSticker
	count int
}

// MetaFetcher downloads the meta.json of an owned LINE emoji pack.
type MetaFetcher func(ctx context.Context, productID string) ([]byte, error)

var assetID = regexp.MustCompile(`^[0-9A-Za-z]{1,32}$`)

type ShopClient interface {
	GetOwnedProducts(context.Context, string, string) ([]line.ShopProduct, error)
}

func LoadCatalog(ctx context.Context, client ShopClient, country string, now time.Time, fetch MetaFetcher) (*Catalog, error) {
	if len(country) != 2 || country[0] < 'A' || country[0] > 'Z' || country[1] < 'A' || country[1] > 'Z' {
		return nil, fmt.Errorf("LINE profile has no valid shop region")
	}
	products, err := client.GetOwnedProducts(ctx, line.StickerShop, country)
	if err != nil {
		return nil, err
	}
	catalog, err := CatalogFromProducts(products, now)
	if err != nil || fetch == nil {
		return catalog, err
	}
	// Emoji packs are optional: stickers still work when the emoji shop fails.
	emoji, err := client.GetOwnedProducts(ctx, line.SticonShop, country)
	if err != nil {
		return catalog, nil
	}
	for _, product := range emoji {
		if _, exists := catalog.owned[product.ID]; exists || !assetID.MatchString(product.ID) {
			continue
		}
		meta, err := fetch(ctx, product.ID)
		if err != nil {
			continue
		}
		catalog.AddEmojiPack(product, meta, now)
	}
	return catalog, nil
}

// AddEmojiPack adds an owned LINE emoji pack from its meta.json; packs that
// are expired, malformed or over the catalog limit are skipped.
func (c *Catalog) AddEmojiPack(product line.ShopProduct, metaJSON []byte, now time.Time) bool {
	if _, exists := c.owned[product.ID]; exists {
		return false
	}
	pack, records, err := productSticons(product, metaJSON, now)
	if err != nil || c.count+len(pack.Stickers) > 10000 {
		return false
	}
	c.count += len(pack.Stickers)
	c.packs = append(c.packs, pack)
	c.owned[pack.ID] = records
	return true
}

func CatalogFromProducts(products []line.ShopProduct, now time.Time) (*Catalog, error) {
	catalog := &Catalog{packs: []Pack{}, owned: make(map[string]map[string]ownedSticker)}
	for _, product := range products {
		if _, exists := catalog.owned[product.ID]; exists {
			continue
		}
		pack, records, err := productStickers(product, now)
		if err != nil {
			continue
		}
		catalog.count += len(pack.Stickers)
		if catalog.count > 10000 {
			return nil, fmt.Errorf("LINE sticker catalog exceeds the application limit")
		}
		catalog.packs = append(catalog.packs, pack)
		catalog.owned[pack.ID] = records
	}
	return catalog, nil
}

func (c *Catalog) Packs() []Pack {
	result := make([]Pack, len(c.packs))
	for i, pack := range c.packs {
		result[i] = pack
		result[i].Stickers = append([]Sticker{}, pack.Stickers...)
	}
	return result
}

func (c *Catalog) StickerMessage(chatID, packageID, stickerID string) (*line.Message, error) {
	if c == nil || strings.TrimSpace(chatID) == "" {
		return nil, fmt.Errorf("a chat and owned sticker catalog are required")
	}
	record, exists := c.owned[packageID][stickerID]
	if !exists || record.sticon {
		return nil, fmt.Errorf("this sticker is not in an owned LINE pack")
	}
	if record.expiry != -1 && record.expiry <= time.Now().UnixMilli() {
		return nil, fmt.Errorf("this LINE sticker pack has expired")
	}
	metadata := map[string]string{
		"STKID": stickerID, "STKPKGID": packageID, "STKVER": record.version, "STKTXT": record.text,
	}
	if record.option != "" {
		metadata["STKOPT"] = record.option
	}
	if record.hash != "" {
		metadata["STKHASH"] = record.hash
	}
	return &line.Message{To: chatID, ContentType: ContentSticker, ContentMetadata: metadata}, nil
}

// Range, version, and resource mappings follow beeper/line imagepack.go, under MIT.
func productStickers(product line.ShopProduct, now time.Time) (Pack, map[string]ownedSticker, error) {
	if id, err := strconv.ParseUint(product.ID, 10, 64); err != nil || id == 0 {
		return Pack{}, nil, fmt.Errorf("invalid sticker pack ID")
	}
	expiry, err := product.ValidUntil.Int64()
	if err != nil || (expiry != -1 && expiry <= now.UnixMilli()) {
		return Pack{}, nil, fmt.Errorf("expired sticker pack")
	}
	version := product.LatestVersion.String()
	if n, err := strconv.ParseUint(version, 10, 32); err != nil || n == 0 {
		return Pack{}, nil, fmt.Errorf("invalid sticker pack version")
	}
	summary := product.ProductTypeSummary.Sticker
	if summary == nil || summary.ResourceType < 1 || summary.ResourceType > 6 {
		return Pack{}, nil, fmt.Errorf("unsupported sticker resource type")
	}
	record := ownedSticker{
		version: version, expiry: expiry, hash: summary.Hash, text: product.Name,
		option: []string{"", "A", "S", "AS", "P", "PS"}[summary.ResourceType-1],
	}
	pack := Pack{ID: product.ID, Name: product.Name, Stickers: []Sticker{}}
	records := make(map[string]ownedSticker)
	for _, idRange := range summary.Ranges {
		start, err := strconv.ParseUint(idRange.Start.String(), 10, 63)
		if err != nil || start == 0 || idRange.Size <= 0 || idRange.Size > 1000-len(records) || start > math.MaxInt64-uint64(idRange.Size-1) {
			return Pack{}, nil, fmt.Errorf("invalid sticker ID range")
		}
		for i := 0; i < idRange.Size; i++ {
			id := strconv.FormatUint(start+uint64(i), 10)
			if _, duplicate := records[id]; duplicate {
				return Pack{}, nil, fmt.Errorf("overlapping sticker ID ranges")
			}
			records[id] = record
			pack.Stickers = append(pack.Stickers, Sticker{ID: id, PackageID: product.ID, URL: StickerPreviewURL(id)})
		}
	}
	if len(records) == 0 {
		return Pack{}, nil, fmt.Errorf("empty sticker pack")
	}
	return pack, records, nil
}

func StickerPreviewURL(stickerID string) string {
	if id, err := strconv.ParseUint(stickerID, 10, 64); err != nil || id == 0 {
		return ""
	}
	return "https://stickershop.line-scdn.net/stickershop/v1/sticker/" + stickerID + "/android/sticker.png"
}

// Sticon mappings follow beeper/line imagepack.go and sticon_send.go, under MIT.
func productSticons(product line.ShopProduct, metaJSON []byte, now time.Time) (Pack, map[string]ownedSticker, error) {
	if !assetID.MatchString(product.ID) {
		return Pack{}, nil, fmt.Errorf("invalid emoji pack ID")
	}
	expiry, err := product.ValidUntil.Int64()
	if err != nil || (expiry != -1 && expiry <= now.UnixMilli()) {
		return Pack{}, nil, fmt.Errorf("expired emoji pack")
	}
	version := product.LatestVersion.String()
	if n, err := strconv.ParseUint(version, 10, 31); err != nil || n == 0 {
		return Pack{}, nil, fmt.Errorf("invalid emoji pack version")
	}
	summary := product.ProductTypeSummary.Sticon
	if summary == nil || (summary.ResourceType != 1 && summary.ResourceType != 2) {
		return Pack{}, nil, fmt.Errorf("unsupported emoji resource type")
	}
	var meta struct {
		Orders   []string          `json:"orders"`
		AltTexts map[string]string `json:"altTexts"`
	}
	if err = json.Unmarshal(metaJSON, &meta); err != nil {
		return Pack{}, nil, err
	}
	if len(meta.Orders) == 0 || len(meta.Orders) > 1000 {
		return Pack{}, nil, fmt.Errorf("invalid emoji pack size")
	}
	pack := Pack{ID: product.ID, Name: product.Name, Kind: "emoji", Stickers: []Sticker{}}
	records := make(map[string]ownedSticker)
	for _, id := range meta.Orders {
		if !assetID.MatchString(id) {
			return Pack{}, nil, fmt.Errorf("invalid emoji ID")
		}
		if _, duplicate := records[id]; duplicate {
			continue
		}
		text := "(emoji)"
		if alt := strings.TrimSpace(meta.AltTexts[id]); alt != "" && len([]rune(alt)) <= 30 {
			text = "(" + alt + ")"
		}
		record := ownedSticker{version: version, expiry: expiry, text: text, sticon: true}
		if summary.ResourceType == 2 {
			record.option = "A"
		}
		records[id] = record
		pack.Stickers = append(pack.Stickers, Sticker{ID: id, PackageID: product.ID, URL: SticonPreviewURL(product.ID, id), Text: text})
	}
	return pack, records, nil
}

func SticonPreviewURL(productID, sticonID string) string {
	if !assetID.MatchString(productID) || !assetID.MatchString(sticonID) {
		return ""
	}
	return "https://stickershop.line-scdn.net/sticonshop/v1/sticon/" + productID + "/android/" + sticonID + ".png"
}

// EmojiRef names one owned LINE emoji placed in a text message.
type EmojiRef struct {
	PackageID string `json:"package_id"`
	StickerID string `json:"sticker_id"`
}

// SticonResource is one REPLACE.sticon entry; S and E are UTF-16 offsets.
type SticonResource struct {
	Start        int    `json:"S"`
	End          int    `json:"E"`
	ProductID    string `json:"productId"`
	SticonID     string `json:"sticonId"`
	Version      int    `json:"version"`
	ResourceType string `json:"resourceType"`
}

// EmojiText replaces each U+FFFC in text, in order, with the alt text of an
// owned LINE emoji, and returns the REPLACE resources and the products used.
func (c *Catalog) EmojiText(text string, refs []EmojiRef, now time.Time) (string, []SticonResource, []string, error) {
	if len(refs) == 0 {
		return text, nil, nil, nil
	}
	parts := strings.Split(text, "\uFFFC")
	if len(parts) != len(refs)+1 || len(refs) > 200 {
		return "", nil, nil, fmt.Errorf("emoji placeholders do not match")
	}
	if c == nil {
		return "", nil, nil, fmt.Errorf("the emoji catalog is not loaded")
	}
	var body strings.Builder
	var products []string
	resources := make([]SticonResource, len(refs))
	offset := 0
	for i, ref := range refs {
		record, ok := c.owned[ref.PackageID][ref.StickerID]
		if !ok || !record.sticon {
			return "", nil, nil, fmt.Errorf("this emoji is not in an owned LINE pack")
		}
		if record.expiry != -1 && record.expiry <= now.UnixMilli() {
			return "", nil, nil, fmt.Errorf("this LINE emoji pack has expired")
		}
		body.WriteString(parts[i])
		offset += len(utf16.Encode([]rune(parts[i])))
		version, _ := strconv.Atoi(record.version)
		resources[i] = SticonResource{Start: offset, End: offset + len(utf16.Encode([]rune(record.text))),
			ProductID: ref.PackageID, SticonID: ref.StickerID, Version: version, ResourceType: "STATIC"}
		if record.option == "A" {
			resources[i].ResourceType = "ANIMATION"
		}
		body.WriteString(record.text)
		offset = resources[i].End
		seen := false
		for _, p := range products {
			seen = seen || p == ref.PackageID
		}
		if !seen {
			products = append(products, ref.PackageID)
		}
	}
	body.WriteString(parts[len(refs)])
	return body.String(), resources, products, nil
}
