package main

import (
	"context"
	"encoding/json"
	"sort"

	"github.com/beeper/line/pkg/line"
)

// Older messages. LINE serves this login only about the last 14 days of a
// chat: on a real account the oldest message it would give, in any chat, was
// 14 days old to within minutes, and paging further returned nothing.
// getRecentMessagesV2 gives the newest messages; getPreviousMessagesV2WithRequest
// gives the ones before a message, a page at a time.

// historyLimit is how many messages the core keeps per chat (the UI keeps
// the same number), so that photos and files of older pages still open.
const historyLimit = 5000

// syncUserInitiated is LINE's SyncReason USER_INITIATED: the person scrolled up.
const syncUserInitiated = 8

// previousMessages asks LINE for up to limit messages of chatID from before
// the message beforeID, sent at beforeTime. The answer may include that
// message itself.
func previousMessages(ctx context.Context, token, chatID, beforeID string, beforeTime int64, limit int) ([]*line.Message, error) {
	request := map[string]any{
		"messageBoxId":  chatID,
		"endMessageId":  map[string]any{"deliveredTime": beforeTime, "messageId": json.Number(beforeID)},
		"messagesCount": limit,
		"withReadCount": false,
		"receivedOnly":  false,
	}
	body, err := rawRequest(ctx, token, "getPreviousMessagesV2WithRequest", request, syncUserInitiated)
	if err != nil {
		return nil, err
	}
	var answer struct {
		Data []*line.Message `json:"data"`
	}
	if err := json.Unmarshal(body, &answer); err != nil {
		return nil, err
	}
	return answer.Data, nil
}

// olderMessages returns the messages of chatID before beforeID, oldest
// first, and keeps them so their photos and files can be opened.
func (b *backend) olderMessages(chatID, beforeID string, beforeTime int64, limit int) ([]message, error) {
	raw, err := previousMessages(b.ctx, b.session.AccessToken, chatID, beforeID, beforeTime, limit)
	if err != nil {
		return nil, err
	}
	b.prefetchKeys(raw)
	// All of them go back to the UI, also those the core already held: after
	// a resync the UI may hold fewer than the core does.
	older := olderOnly(raw, chatID, beforeID, beforeTime, b.convertMessage)
	b.rememberOlder(chatID, older)
	return older, nil
}

// olderOnly converts the messages of a page that belong to chatID and are
// not after the message the page was asked for, oldest first.
func olderOnly(raw []*line.Message, chatID, beforeID string, beforeTime int64, convert func(*line.Message) message) []message {
	result := make([]message, 0, len(raw))
	for _, m := range raw {
		if m == nil || m.ID == "" || m.ID == beforeID {
			continue
		}
		msg := convert(m)
		if msg.ChatID != chatID || msg.Time > beforeTime {
			continue
		}
		result = append(result, msg)
	}
	sort.SliceStable(result, func(i, j int) bool {
		if result[i].Time == result[j].Time {
			return result[i].ID < result[j].ID
		}
		return result[i].Time < result[j].Time
	})
	return result
}

// rememberOlder puts messages from before what the core holds for chatID in
// front of it (the end stays the newest message, which marking a chat read
// relies on) and returns the ones it did not hold yet.
func (b *backend) rememberOlder(chatID string, older []message) []message {
	if b.messages == nil {
		b.messages = map[string][]message{}
	}
	list := b.messages[chatID]
	known := make(map[string]bool, len(list)+len(older))
	for _, m := range list {
		known[m.ID] = true
	}
	fresh := make([]message, 0, len(older))
	for _, m := range older {
		if known[m.ID] {
			continue
		}
		known[m.ID] = true
		fresh = append(fresh, m)
		b.markSeen(m.ID)
	}
	if room := historyLimit - len(list); len(fresh) > room {
		fresh = fresh[len(fresh)-max(room, 0):]
	}
	b.messages[chatID] = append(append(make([]message, 0, len(fresh)+len(list)), fresh...), list...)
	return fresh
}
