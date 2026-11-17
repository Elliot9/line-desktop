package main

import (
	"context"
	"encoding/json"
	"strconv"
	"time"
)

// Receipts that arrive while LINE runs (op 55) only cover reads from then
// on. For what was read earlier, LINE keeps a read range per member and
// chat (TalkService.getMessageReadRange); it is asked for when a chat's
// history is loaded, so its older messages get 已讀 too.

const readRangeTimeout = 5 * time.Second

// fetchReadRanges returns chat → member → time of the last message the
// member has read.
func fetchReadRanges(ctx context.Context, token string, chatIDs []string) (map[string]map[string]int64, error) {
	ctx, cancel := context.WithTimeout(ctx, readRangeTimeout)
	defer cancel()
	response, err := rawCall(ctx, token, "getMessageReadRange", chatIDs, 2)
	if err != nil {
		return nil, err
	}
	return parseReadRanges(response["data"]), nil
}

// parseReadRanges reads [{chatId, ranges: {member: [{endTime, ...}]}}].
func parseReadRanges(data any) map[string]map[string]int64 {
	out := map[string]map[string]int64{}
	results, _ := data.([]any)
	for _, item := range results {
		result, _ := item.(map[string]any)
		chatID, _ := result["chatId"].(string)
		ranges, _ := result["ranges"].(map[string]any)
		if chatID == "" {
			continue
		}
		for member, list := range ranges {
			entries, _ := list.([]any)
			for _, e := range entries {
				entry, _ := e.(map[string]any)
				if end := int64Value(entry["endTime"]); end > 0 {
					if out[chatID] == nil {
						out[chatID] = map[string]int64{}
					}
					out[chatID][member] = max(out[chatID][member], end)
				}
			}
		}
	}
	return out
}

func int64Value(value any) int64 {
	switch v := value.(type) {
	case string:
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	case json.Number:
		n, _ := v.Int64()
		return n
	case float64:
		return int64(v)
	}
	return 0
}

// mergeReadBy records read times for a chat (never moving one back) and
// returns the chat's read times.
func (b *backend) mergeReadBy(chatID string, readers map[string]int64) map[string]int64 {
	for i := range b.data.Chats {
		c := &b.data.Chats[i]
		if c.ID != chatID {
			continue
		}
		for reader, at := range readers {
			if b.session != nil && reader == b.session.MID {
				continue
			}
			if c.ReadBy == nil {
				c.ReadBy = map[string]int64{}
			}
			if at > c.ReadBy[reader] {
				c.ReadBy[reader] = at
			}
		}
		return c.ReadBy
	}
	return nil
}
