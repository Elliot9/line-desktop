package main

// Diagnostic mode (`line-core --diagnose`): prints counts and field names of
// the raw contact/chat responses using the saved login, without names,
// messages or IDs, so list filtering can be checked against the phone app.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"time"

	gen "github.com/beeper/line/pkg"
	"github.com/beeper/line/pkg/e2ee"
	"github.com/beeper/line/pkg/line"
	"matrix-line/core/internal/attachments"
)

func rawCall(ctx context.Context, token, method string, args ...any) (map[string]any, error) {
	url := line.BaseURL + "/TalkService/" + method
	body := []byte("[]")
	if len(args) > 0 {
		body, _ = json.Marshal(args)
	}
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", line.UserAgent)
	req.Header.Set("x-line-chrome-version", line.ExtensionVersion)
	req.Header.Set("x-line-application", "CHROMEOS\t3.7.2\tChrome_OS")
	req.Header.Set("x-lal", "en_US")
	req.Header.Set("x-line-access", token)
	req.Header.Set("Cookie", "lct="+token)
	runner, err := gen.GetRunner()
	if err != nil {
		return nil, err
	}
	signature, err := runner.GetSignature(strings.TrimPrefix(url, "https://line-chrome-gw.line-apps.com"), string(body), token)
	if err != nil {
		return nil, err
	}
	req.Header.Set("x-hmac", signature)
	resp, err := (&http.Client{Timeout: 35 * time.Second, Transport: contextTransport{ctx, http.DefaultTransport}}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, err
	}
	if code, ok := out["code"].(float64); ok && code != 0 {
		message, _ := out["message"].(string)
		return nil, fmt.Errorf("%s: code %v %s", method, code, message)
	}
	return out, nil
}

// shape summarises the values a field takes across records, hiding strings.
func shape(records []map[string]any) map[string]map[string]int {
	out := map[string]map[string]int{}
	for _, r := range records {
		for key, value := range r {
			if out[key] == nil {
				out[key] = map[string]int{}
			}
			switch v := value.(type) {
			case string:
				if v == "" {
					out[key]["\"\""]++
				} else {
					out[key]["<str>"]++
				}
			case float64, bool, nil:
				out[key][fmt.Sprint(v)]++
			case map[string]any:
				keys := make([]string, 0, len(v))
				for k := range v {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				out[key]["{"+strings.Join(keys, ",")+"}"]++
			default:
				out[key][fmt.Sprintf("<%T>", v)]++
			}
		}
	}
	return out
}

func printShape(title string, records []map[string]any) {
	fmt.Printf("== %s (%d)\n", title, len(records))
	s := shape(records)
	keys := make([]string, 0, len(s))
	for k := range s {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		parts := []string{}
		for value, count := range s[k] {
			parts = append(parts, fmt.Sprintf("%s×%d", value, count))
		}
		sort.Strings(parts)
		if len(parts) > 8 {
			parts = append(parts[:8], "…")
		}
		fmt.Printf("  %-28s %s\n", k, strings.Join(parts, " "))
	}
}

func diagnose(ctx context.Context, store sessionStore) error {
	saved, err := store.Load(ctx)
	if err != nil || saved == nil {
		return fmt.Errorf("no saved login: %v", err)
	}
	token := saved.AccessToken
	diagnoseEncryption(ctx, saved)
	// Owned sticker and emoji packs, as the sticker panel loads them.
	if client := apiClient(ctx, token); client != nil {
		if profile, err := client.GetProfile(); err == nil {
			catalog, err := attachments.LoadCatalog(ctx, client, profile.RegionCode, time.Now(), fetchSticonMeta)
			kinds := map[string]int{}
			if err == nil {
				for _, pack := range catalog.Packs() {
					kinds["packs:"+pack.Kind] += 1
					kinds["items:"+pack.Kind] += len(pack.Stickers)
				}
			}
			fmt.Printf("sticker catalog: %v err=%v\n", kinds, err)
		}
	}
	ids, err := rawCall(ctx, token, "getAllContactIds")
	if err != nil {
		return fmt.Errorf("getAllContactIds: %w", err)
	}
	contactIDs, _ := ids["data"].([]any)
	fmt.Printf("getAllContactIds: %d\n", len(contactIDs))
	for _, method := range []string{"getBlockedContactIds", "getBlockedRecommendationIds", "getRecommendationIds"} {
		if r, err := rawCall(ctx, token, method); err == nil {
			list, _ := r["data"].([]any)
			fmt.Printf("%s: %d\n", method, len(list))
		} else {
			fmt.Printf("%s: %v\n", method, err)
		}
	}
	contacts := []map[string]any{}
	wrappers := []map[string]any{}
	for start := 0; start < len(contactIDs); start += 100 {
		batch := contactIDs[start:min(start+100, len(contactIDs))]
		r, err := rawCall(ctx, token, "getContactsV2", map[string]any{"targetUserMids": batch})
		if err != nil {
			return fmt.Errorf("getContactsV2: %w", err)
		}
		data, _ := r["data"].(map[string]any)
		found, _ := data["contacts"].(map[string]any)
		for _, w := range found {
			wrapper, _ := w.(map[string]any)
			wrappers = append(wrappers, wrapper)
			if c, ok := wrapper["contact"].(map[string]any); ok {
				contacts = append(contacts, c)
			}
		}
	}
	cross := map[string]int{}
	for _, c := range contacts {
		settings, _ := c["settings"].(string)
		fav, _ := c["favoriteTime"].(string)
		cross[fmt.Sprintf("settings=%s buddy=%v type=%v fav=%v", settings, c["capableBuddy"], c["type"], fav != "" && fav != "0")]++
	}
	keys := make([]string, 0, len(cross))
	for k := range cross {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	fmt.Println("== settings × capableBuddy × type")
	for _, k := range keys {
		fmt.Printf("  %-60s %d\n", k, cross[k])
	}
	friends, official, favorites, hidden := 0, 0, 0, 0
	for _, c := range contacts {
		e := parseContact(c)
		switch {
		case e.hidden || e.blocked || e.deleted:
			hidden++
		case e.Official:
			official++
		default:
			friends++
		}
		if e.Favorite && !e.hidden {
			favorites++
		}
	}
	fmt.Printf("after filter: friends=%d official=%d favorites=%d filtered=%d\n", friends, official, favorites, hidden)
	printShape("contact wrapper", wrappers)
	printShape("contact", contacts)

	mids, err := rawCall(ctx, token, "getAllChatMids", map[string]any{"withMemberChats": true, "withInvitedChats": true})
	if err != nil {
		return fmt.Errorf("getAllChatMids: %w", err)
	}
	data, _ := mids["data"].(map[string]any)
	member, _ := data["memberChatMids"].([]any)
	invited, _ := data["invitedChatMids"].([]any)
	fmt.Printf("memberChatMids: %d (c=%d r=%d)  invitedChatMids: %d\n", len(member), countPrefix(member, "c"), countPrefix(member, "r"), len(invited))
	chats := []map[string]any{}
	extras := []map[string]any{}
	for start := 0; start < len(member); start += 100 {
		r, err := rawCall(ctx, token, "getChats", map[string]any{"chatMids": member[start:min(start+100, len(member))], "withMembers": true, "withInvitees": false})
		if err != nil {
			return fmt.Errorf("getChats: %w", err)
		}
		d, _ := r["data"].(map[string]any)
		list, _ := d["chats"].([]any)
		for _, item := range list {
			c, _ := item.(map[string]any)
			chats = append(chats, c)
			if extra, ok := c["extra"].(map[string]any); ok {
				if g, ok := extra["groupExtra"].(map[string]any); ok {
					flat := map[string]any{}
					for k, v := range g {
						if m, ok := v.(map[string]any); ok {
							flat[k] = float64(len(m))
						} else {
							flat[k] = v
						}
					}
					extras = append(extras, flat)
				}
			}
		}
	}
	printShape("chat", chats)
	printShape("groupExtra (map fields → sizes)", extras)
	contactSet := map[string]contactEntry{}
	for _, c := range contacts {
		e := parseContact(c)
		contactSet[e.ID] = e
	}
	memberSet := map[string]bool{}
	for _, m := range member {
		if id, ok := m.(string); ok {
			memberSet[id] = true
		}
	}
	for _, active := range []bool{false, true} {
		boxes := []map[string]any{}
		stats := map[string]int{}
		minID := ""
		for page := 0; page < 100; page++ {
			opts := map[string]any{"activeOnly": active, "unreadOnly": false, "messageBoxCountLimit": 100, "withUnreadCount": true, "lastMessagesPerMessageBoxCount": 1}
			if minID != "" {
				opts["minChatId"] = minID
			}
			r, err := rawCall(ctx, token, "getMessageBoxes", opts, 2)
			if err != nil {
				return fmt.Errorf("getMessageBoxes: %w", err)
			}
			d, _ := r["data"].(map[string]any)
			list, _ := d["messageBoxes"].([]any)
			for _, item := range list {
				box, _ := item.(map[string]any)
				id, _ := box["id"].(string)
				flat := map[string]any{}
				for k, v := range box {
					if k == "lastMessages" {
						msgs, _ := v.([]any)
						flat[k] = float64(len(msgs))
						age := "none"
						if len(msgs) > 0 {
							m, _ := msgs[0].(map[string]any)
							var ms float64
							switch t := m["createdTime"].(type) {
							case string:
								fmt.Sscan(t, &ms)
							case float64:
								ms = t
							}
							days := time.Since(time.UnixMilli(int64(ms))).Hours() / 24
							switch {
							case days < 30:
								age = "<30d"
							case days < 180:
								age = "<180d"
							case days < 365:
								age = "<1y"
							default:
								age = ">1y"
							}
							flat["lastMsgContentType"] = m["contentType"]
						}
						flat["age"] = age
						continue
					}
					if k == "id" {
						continue
					}
					flat[k] = v
				}
				kind := "direct"
				if p := strings.ToLower(id); strings.HasPrefix(p, "c") || strings.HasPrefix(p, "r") {
					kind = "group"
					if !memberSet[id] {
						kind = "group-left"
					}
				} else if e, ok := contactSet[id]; ok {
					switch {
					case e.hidden || e.blocked || e.deleted:
						kind = "direct-hidden"
					case e.Official:
						kind = "direct-official"
					default:
						kind = "direct-friend"
					}
				} else {
					kind = "direct-nonfriend"
				}
				flat["kind"] = kind
				stats[kind+" "+fmt.Sprint(flat["age"])]++
				boxes = append(boxes, flat)
			}
			more, _ := d["hasNext"].(bool)
			if !more || len(list) == 0 {
				break
			}
			last, _ := list[len(list)-1].(map[string]any)
			next, _ := last["id"].(string)
			if next == minID {
				break
			}
			minID = next
		}
		printShape(fmt.Sprintf("messageBoxes activeOnly=%v", active), boxes)
		keys := make([]string, 0, len(stats))
		for k := range stats {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			fmt.Printf("    %-32s %d\n", k, stats[k])
		}
	}
	return nil
}

func countPrefix(list []any, prefix string) int {
	n := 0
	for _, v := range list {
		if s, ok := v.(string); ok && strings.HasPrefix(strings.ToLower(s), prefix) {
			n++
		}
	}
	return n
}

var midPattern = regexp.MustCompile(`[ucr][0-9a-f]{32}`)

// diagnoseEncryption seals a test payload for every active chat without
// sending it or registering keys, and prints anonymised failure reasons.
func diagnoseEncryption(ctx context.Context, saved *savedSession) {
	crypto, err := e2ee.NewManager()
	if err == nil {
		err = crypto.LoadMyKeyFromExportedMap(saved.Keys)
	}
	if err != nil {
		fmt.Printf("encryption: own keys unavailable: %v\n", err)
		return
	}
	defer crypto.Close()
	b := &backend{ctx: ctx, session: saved, crypto: crypto}
	boxes, err := b.api().GetMessageBoxes(line.MessageBoxesOptions{ActiveOnly: true, MessageBoxCountLimit: 100})
	if err != nil {
		fmt.Printf("encryption: message boxes: %v\n", err)
		return
	}
	results := map[string]int{}
	for _, box := range boxes.MessageBoxes {
		kind := string(midKind(box.ID))
		_, _, err := b.encryptPayload(box.ID, 0, []byte(`{"text":"test"}`), false)
		reason := "ok"
		if err != nil {
			reason = midPattern.ReplaceAllString(err.Error(), "<mid>")
			if len(reason) > 160 {
				reason = reason[:160] + "…"
			}
		}
		if err != nil && kind != "u" && line.IsGroupKeyNotFound(err) {
			keys, kerr := b.api().GetLastE2EEPublicKeys(box.ID)
			usable := 0
			for mid, key := range keys {
				if validMID(mid) && midKind(mid) == 'u' && key.KeyID > 0 && key.KeyData != "" {
					usable++
				}
			}
			_, self := keys[saved.MID]
			reason = fmt.Sprintf("no group key yet; registration members=%d usable=%d includesMe=%v err=%v", len(keys), usable, self, midPattern.ReplaceAllString(fmt.Sprint(kerr), "<mid>"))
		}
		if err != nil && kind == "u" {
			official := "?"
			if r, cerr := rawCall(ctx, saved.AccessToken, "getContactsV2", map[string]any{"targetUserMids": []string{box.ID}}); cerr == nil {
				data, _ := r["data"].(map[string]any)
				found, _ := data["contacts"].(map[string]any)
				for _, w := range found {
					wrapper, _ := w.(map[string]any)
					if c, ok := wrapper["contact"].(map[string]any); ok {
						official = fmt.Sprint(parseContact(c).Official)
					}
				}
			}
			if key, kerr := b.api().NegotiateE2EEPublicKey(box.ID); kerr == nil {
				reason += fmt.Sprintf(" [official=%s e2eeVersion=%d]", official, key.E2EEVersion)
			} else {
				reason = "no key [official=" + official + "]"
			}
		}
		results[kind+" "+reason]++
		if err == nil && kind == "u" {
			if key, kerr := b.api().NegotiateE2EEPublicKey(box.ID); kerr == nil {
				results[fmt.Sprintf("u e2eeVersion=%d", key.E2EEVersion)]++
			}
		}
	}
	keys := make([]string, 0, len(results))
	for k := range results {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	fmt.Println("== encryption dry run (nothing sent)")
	for _, k := range keys {
		fmt.Printf("  %3d  %s\n", results[k], k)
	}
}
