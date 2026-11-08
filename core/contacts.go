package main

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
)

// LINE Contact fields the bundled client library does not decode
// (ContactStatus, ContactSetting bits, capableBuddy, favoriteTime).
const (
	contactSettingHide     = 4
	contactSettingFavorite = 8
	contactSettingDelete   = 16
)

type contactEntry struct {
	person
	hidden, blocked, deleted bool
}

// enumValue reads a Thrift enum that the JSON gateway may send as a number
// or as its name.
func enumValue(value any, names map[string]int) int {
	switch v := value.(type) {
	case float64:
		return int(v)
	case json.Number:
		n, _ := v.Int64()
		return int(n)
	case string:
		return names[strings.ToUpper(v)]
	}
	return 0
}

var contactStatusNames = map[string]int{"FRIEND": 1, "FRIEND_BLOCKED": 2, "RECOMMEND": 3, "RECOMMEND_BLOCKED": 4, "DELETED": 5, "DELETED_BLOCKED": 6}

func parseContact(raw map[string]any) contactEntry {
	text := func(key string) string { v, _ := raw[key].(string); return v }
	name := text("displayNameOverridden")
	if name == "" {
		name = text("displayName")
	}
	entry := contactEntry{person: person{ID: text("mid"), Name: name, Status: text("statusMessage"), Avatar: avatarURL(text("picturePath")), Friend: true}}
	status := enumValue(raw["status"], contactStatusNames)
	entry.blocked = status == 2 || status == 4 || status == 6
	entry.deleted = status == 5 || status == 6
	// RECOMMEND rows are "people you may know", not friends.
	entry.hidden = status == 3
	var settings int
	switch v := raw["settings"].(type) {
	case float64:
		settings = int(v)
	case string: // the gateway sends the i64 bit set as a decimal string
		n, _ := strconv.ParseInt(v, 10, 64)
		settings = int(n)
	case []any: // some gateways send the set bits as a list of names
		for _, item := range v {
			name, _ := item.(string)
			switch strings.TrimPrefix(strings.ToUpper(name), "CONTACT_SETTING_") {
			case "CONTACT_HIDE":
				settings |= contactSettingHide
			case "FAVORITE":
				settings |= contactSettingFavorite
			case "DELETE":
				settings |= contactSettingDelete
			}
		}
	}
	entry.hidden = entry.hidden || settings&contactSettingHide != 0
	entry.deleted = entry.deleted || settings&contactSettingDelete != 0
	favorite := settings&contactSettingFavorite != 0
	switch v := raw["favoriteTime"].(type) {
	case float64:
		favorite = favorite || v > 0
	case string:
		favorite = favorite || (v != "" && v != "0")
	}
	entry.Favorite = favorite
	// Official accounts (官方帳號) are listed separately from friends.
	entry.Official, _ = raw["capableBuddy"].(bool)
	return entry
}

// fetchContacts is getContactsV2 with the full contact record.
func fetchContacts(ctx context.Context, token string, ids []string) ([]contactEntry, error) {
	response, err := rawCall(ctx, token, "getContactsV2", map[string]any{"targetUserMids": ids})
	if err != nil {
		return nil, err
	}
	data, _ := response["data"].(map[string]any)
	found, _ := data["contacts"].(map[string]any)
	out := make([]contactEntry, 0, len(found))
	for _, item := range found {
		wrapper, _ := item.(map[string]any)
		if raw, ok := wrapper["contact"].(map[string]any); ok {
			out = append(out, parseContact(raw))
		}
	}
	return out, nil
}
