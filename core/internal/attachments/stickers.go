package attachments

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/beeper/line/pkg/line"
)

const ContentSticker = 7

type Sticker struct {
	ID        string `json:"id"`
	PackageID string `json:"package_id"`
	URL       string `json:"url"`
}

type Pack struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Stickers []Sticker `json:"stickers"`
}

type ownedSticker struct {
	version string
	option  string
	hash    string
	text    string
	expiry  int64
}

type Catalog struct {
	packs []Pack
	owned map[string]map[string]ownedSticker
}

type ShopClient interface {
	GetOwnedProducts(context.Context, string, string) ([]line.ShopProduct, error)
}

func LoadCatalog(ctx context.Context, client ShopClient, country string, now time.Time) (*Catalog, error) {
	if len(country) != 2 || country[0] < 'A' || country[0] > 'Z' || country[1] < 'A' || country[1] > 'Z' {
		return nil, fmt.Errorf("LINE profile has no valid shop region")
	}
	products, err := client.GetOwnedProducts(ctx, line.StickerShop, country)
	if err != nil {
		return nil, err
	}
	return CatalogFromProducts(products, now)
}

func CatalogFromProducts(products []line.ShopProduct, now time.Time) (*Catalog, error) {
	catalog := &Catalog{packs: []Pack{}, owned: make(map[string]map[string]ownedSticker)}
	count := 0
	for _, product := range products {
		if _, exists := catalog.owned[product.ID]; exists {
			continue
		}
		pack, records, err := productStickers(product, now)
		if err != nil {
			continue
		}
		count += len(pack.Stickers)
		if count > 10000 {
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
	if !exists {
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
