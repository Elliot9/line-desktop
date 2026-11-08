package main

import (
	"crypto/md5"
	"encoding/json"
	"fmt"
	"strings"
	"unicode/utf16"
)

// LINE emoji (sticons) arrive either as REPLACE.sticon resources that point at
// UTF-16 ranges of the text, or as private-use placeholder code points
// (EMTVER3: one code point; EMTVER4: product, sticon, alt text, terminator).
// This mirrors upstream beeper/line pkg/connector/handlers/emoji.go, whose
// helpers are unexported. The UI receives clean text plus image runs.
const (
	emtver3Start      = 0x100000
	emtver3End        = 0x1000ff
	emtver4Start      = 0x100100
	emtver4End        = 0x10fffe
	emtver4Terminator = 0x10ffff
	emtver3Product    = 1048843
)

type sticonResource struct {
	Start        int    `json:"S"`
	End          int    `json:"E"`
	ProductID    string `json:"productId"`
	SticonID     string `json:"sticonId"`
	ResourceType string `json:"resourceType"`
	derived      bool
}

func sticonURL(r sticonResource) string {
	if r.ProductID == "" || r.SticonID == "" {
		return ""
	}
	if r.derived {
		if len(r.ProductID) < 3 {
			return ""
		}
		return fmt.Sprintf("https://stickershop.line-scdn.net/sticon/v1/%c/%c/%c/%s/android/%s_k.png",
			r.ProductID[0], r.ProductID[1], r.ProductID[2], r.ProductID, r.SticonID)
	}
	return fmt.Sprintf("https://stickershop.line-scdn.net/sticonshop/v1/sticon/%s/android/%s.png", r.ProductID, r.SticonID)
}

func sticonHash(code rune) string {
	sum := md5.Sum([]byte(fmt.Sprintf("%X", code)))
	return fmt.Sprintf("%x", sum)[:16]
}

// parseReplace reads REPLACE.sticon resources from a decrypted body
// ({"REPLACE":{"sticon":{...}}}) or plain metadata ({"sticon":{...}}).
func parseReplace(body []byte, metadata string) []sticonResource {
	var wrapped struct {
		Replace struct {
			Sticon struct {
				Resources []sticonResource `json:"resources"`
			} `json:"sticon"`
		} `json:"REPLACE"`
	}
	var plain struct {
		Sticon struct {
			Resources []sticonResource `json:"resources"`
		} `json:"sticon"`
	}
	var out []sticonResource
	if len(body) > 0 && json.Unmarshal(body, &wrapped) == nil {
		out = append(out, wrapped.Replace.Sticon.Resources...)
	}
	if metadata != "" && json.Unmarshal([]byte(metadata), &plain) == nil {
		out = append(out, plain.Sticon.Resources...)
	}
	return out
}

// inlineEmoji returns text with every sticon range replaced by U+FFFC and the
// matching runs (rune offsets into the returned text).
func inlineEmoji(text string, explicit []sticonResource) (string, []emojiRun) {
	units := utf16.Encode([]rune(text))
	type span struct {
		start, end int
		url        string
	}
	spans := map[int]span{}
	for _, r := range explicit {
		if r.Start >= 0 && r.End > r.Start && r.End <= len(units) {
			if url := sticonURL(r); url != "" {
				spans[r.Start] = span{r.Start, r.End, url}
			}
		}
	}
	// Derived placeholders (EMTVER3/4) when no explicit resource covers them.
	runes := []rune(text)
	pos := 0
	for i := 0; i < len(runes); {
		start := pos
		r := runes[i]
		if r >= emtver3Start && r <= emtver3End {
			pos += len(utf16.Encode([]rune{r}))
			if _, ok := spans[start]; !ok {
				spans[start] = span{start, pos, sticonURL(sticonResource{ProductID: sticonHash(emtver3Product), SticonID: fmt.Sprintf("%X", r+0x100), derived: true})}
			}
			i++
			continue
		}
		if r >= emtver4Start && r <= emtver4End && i+3 < len(runes) && runes[i+1] >= emtver4Start && runes[i+1] <= emtver4End {
			end := -1
			for j := i + 2; j < len(runes); j++ {
				if runes[j] == emtver4Terminator {
					end = j + 1
					break
				}
			}
			if end > i+2 {
				for _, x := range runes[i:end] {
					pos += len(utf16.Encode([]rune{x}))
				}
				if _, ok := spans[start]; !ok {
					spans[start] = span{start, pos, sticonURL(sticonResource{ProductID: sticonHash(runes[i]), SticonID: fmt.Sprintf("%X", runes[i+1]), derived: true})}
				}
				i = end
				continue
			}
		}
		pos += len(utf16.Encode([]rune{r}))
		i++
	}
	if len(spans) == 0 {
		return text, nil
	}
	var b strings.Builder
	var runs []emojiRun
	outRunes := 0
	for u := 0; u < len(units); {
		if s, ok := spans[u]; ok && s.url != "" {
			b.WriteRune('\uFFFC')
			runs = append(runs, emojiRun{Start: outRunes, End: outRunes + 1, URL: s.url})
			outRunes++
			u = s.end
			continue
		}
		n := 1
		if utf16.IsSurrogate(rune(units[u])) && u+1 < len(units) {
			n = 2
		}
		decoded := utf16.Decode(units[u : u+n])
		for _, r := range decoded {
			if r >= emtver3Start {
				continue // stray private-use placeholder without an image
			}
			b.WriteRune(r)
			outRunes++
		}
		u += n
	}
	return b.String(), runs
}
