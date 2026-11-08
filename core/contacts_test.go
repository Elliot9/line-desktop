package main

import "testing"

func TestParseContactFilters(t *testing.T) {
	cases := []struct {
		name                     string
		raw                      map[string]any
		hidden, blocked, deleted bool
		favorite                 bool
	}{
		{"friend", map[string]any{"mid": "u1", "displayName": "A", "status": 1.0, "settings": 0.0}, false, false, false, false},
		{"hidden bit", map[string]any{"mid": "u2", "status": 1.0, "settings": 4.0}, true, false, false, false},
		{"favorite bit", map[string]any{"mid": "u3", "status": 1.0, "settings": 8.0}, false, false, false, true},
		{"blocked", map[string]any{"mid": "u4", "status": 2.0}, false, true, false, false},
		{"deleted name", map[string]any{"mid": "u5", "status": "DELETED"}, false, false, true, false},
		{"recommend", map[string]any{"mid": "u6", "status": 3.0}, true, false, false, false},
		{"setting names", map[string]any{"mid": "u7", "status": 1.0, "settings": []any{"CONTACT_SETTING_CONTACT_HIDE"}}, true, false, false, false},
		{"string settings hidden", map[string]any{"mid": "u10", "status": 1.0, "settings": "5"}, true, false, false, false},
		{"string settings favorite", map[string]any{"mid": "u11", "status": 1.0, "settings": "10", "favoriteTime": "0"}, false, false, false, true},
		{"favorite time", map[string]any{"mid": "u8", "status": 1.0, "favoriteTime": "1700000000000"}, false, false, false, true},
	}
	for _, c := range cases {
		got := parseContact(c.raw)
		if got.hidden != c.hidden || got.blocked != c.blocked || got.deleted != c.deleted || got.Favorite != c.favorite {
			t.Errorf("%s: got hidden=%v blocked=%v deleted=%v favorite=%v", c.name, got.hidden, got.blocked, got.deleted, got.Favorite)
		}
	}
	if got := parseContact(map[string]any{"mid": "u9", "displayName": "原名", "displayNameOverridden": "暱稱"}); got.Name != "暱稱" {
		t.Errorf("overridden name not used: %q", got.Name)
	}
}
