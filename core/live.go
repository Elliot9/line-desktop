package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/beeper/line/pkg/e2ee"
	"github.com/beeper/line/pkg/line"
)

func avatarURL(path string) string {
	if path == "" {
		return ""
	}
	if strings.HasPrefix(path, "https://") {
		return path
	}
	return "https://profile.line-scdn.net/" + strings.TrimPrefix(path, "/")
}
func chatKind(id string) string {
	switch midKind(id) {
	case 'c':
		return "group"
	case 'r':
		return "room"
	}
	return "direct"
}

// groupMembers lists up to maxListedMembers member IDs in a stable order so the
// UI can draw member avatars without inflating every snapshot.
const maxListedMembers = 24

func groupMembers(mids line.FlexibleMidMap) []string {
	members := make([]string, 0, len(mids))
	for mid := range mids {
		if validMID(mid) {
			members = append(members, mid)
		}
	}
	sort.Strings(members)
	if len(members) > maxListedMembers {
		members = members[:maxListedMembers]
	}
	return members
}
func (b *backend) personName(id string) string {
	if b.data.Profile != nil && id == b.data.Profile.ID {
		return b.data.Profile.Name
	}
	for _, p := range b.data.Contacts {
		if p.ID == id {
			return p.Name
		}
	}
	for _, c := range b.data.Chats {
		if c.ID == id {
			return c.Name
		}
	}
	if p, ok := b.data.People[id]; ok && p.Name != "" {
		return p.Name
	}
	return id
}

// maxResolve bounds how many unknown people one snapshot refresh looks up.
const maxResolve = 400

// resolvePeople fills snapshot.People with names and pictures for everyone the
// UI may show who is not a friend: group members, direct chat partners and
// message senders. Friends are resolved from friendByID; the rest are fetched
// with GetContactsV2 in batches and, for official accounts that the contact
// API does not return, GetBuddyProfile.
func (b *backend) resolvePeople(friendByID map[string]person) {
	people := map[string]person{}
	for id, p := range b.data.People {
		people[id] = p
	}
	for id, p := range friendByID {
		people[id] = p
	}
	wanted := map[string]bool{}
	want := func(id string) {
		if _, ok := people[id]; !ok && validMID(id) && midKind(id) == 'u' && (b.session == nil || id != b.session.MID) {
			wanted[id] = true
		}
	}
	for _, c := range b.data.Chats {
		if c.Kind == "direct" {
			want(c.ID)
		}
		for _, m := range c.Members {
			want(m)
		}
	}
	for _, list := range b.messages {
		for _, m := range list {
			want(m.SenderID)
		}
	}
	ids := make([]string, 0, len(wanted))
	for id := range wanted {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	if len(ids) > maxResolve {
		ids = ids[:maxResolve]
	}
	c := b.api()
	for start := 0; start < len(ids); start += 100 {
		found, err := c.GetContactsV2(ids[start:min(start+100, len(ids))])
		if err != nil {
			break
		}
		for _, wrapped := range found.Contacts {
			p := wrapped.Contact
			if p.Mid != "" {
				people[p.Mid] = person{ID: p.Mid, Name: p.EffectiveDisplayName(), Status: p.StatusMessage, Avatar: avatarURL(p.PicturePath)}
			}
		}
	}
	lookups := 0
	for _, id := range ids {
		if _, ok := people[id]; ok || lookups >= 20 {
			continue
		}
		lookups++
		if buddy, err := c.GetBuddyProfile(id); err == nil && buddy != nil && buddy.DisplayName != "" {
			people[id] = person{ID: id, Name: buddy.DisplayName, Avatar: avatarURL(buddy.PicturePath), Official: true}
		}
	}
	for _, id := range ids {
		if _, ok := people[id]; !ok {
			people[id] = person{ID: id} // negative cache: do not look up again
		}
	}
	b.data.People = people
	for i := range b.data.Chats {
		row := &b.data.Chats[i]
		if p, ok := people[row.ID]; ok && row.Kind == "direct" {
			if row.Name == "" || row.Name == row.ID {
				row.Name = p.Name
			}
			if row.Avatar == "" {
				row.Avatar = p.Avatar
			}
		}
	}
	for chatID, list := range b.messages {
		for i := range list {
			if p, ok := people[list[i].SenderID]; ok && (list[i].SenderName == "" || list[i].SenderName == list[i].SenderID) {
				list[i].SenderName = p.Name
			}
		}
		b.messages[chatID] = list
	}
}

// preview is the chat-list subtitle for a message, as LINE Desktop shows it:
// media are described ("傳送了貼圖") and unsent messages say so.
func (b *backend) preview(row chat, m message) string {
	name := m.SenderName
	if m.Mine {
		name = "你"
	} else if name == "" || name == m.SenderID {
		name = "對方"
	}
	switch {
	case m.Unsent:
		return name + "已收回訊息"
	case m.Kind == "sticker":
		return name + "傳送了貼圖"
	case m.Kind == "image":
		return name + "傳送了照片"
	case m.Kind == "file":
		return name + "傳送了檔案"
	}
	// LINE emoji sit in Text as U+FFFC; previews name them instead.
	text := strings.ReplaceAll(m.Text, "\uFFFC", "(表情貼)")
	if row.Kind != "direct" && !m.Mine && m.Kind == "text" {
		return name + "：" + text
	}
	return text
}

// fillGroup fetches one group's name, picture and members (live mode only).
func (b *backend) fillGroup(row *chat) {
	if b.demo || b.session == nil {
		return
	}
	found, err := b.api().GetChats([]string{row.ID}, true, false)
	if err != nil || found == nil || len(found.Chats) == 0 {
		if row.Name == row.ID {
			row.Name = "群組聊天"
		}
		return
	}
	g := found.Chats[0]
	if g.ChatName != "" {
		row.Name = g.ChatName
	} else if row.Name == row.ID {
		row.Name = "群組聊天"
	}
	row.Avatar = avatarURL(g.PicturePath)
	row.Muted = g.NotificationDisabled
	if g.Extra.GroupExtra != nil {
		row.MemberCount = len(g.Extra.GroupExtra.MemberMids)
		row.Members = groupMembers(g.Extra.GroupExtra.MemberMids)
	}
}

func (b *backend) refreshSnapshot() error {
	c := b.api()
	ids, err := c.GetAllContactIds()
	if err != nil {
		return err
	}
	blocked := map[string]bool{}
	if list, err := c.GetBlockedContactIds(); err == nil {
		for _, id := range list {
			blocked[id] = true
		}
	}
	contacts := []person{}
	for start := 0; start < len(ids); start += 100 {
		batch := ids[start:min(start+100, len(ids))]
		found, err := fetchContacts(b.ctx, b.session.AccessToken, batch)
		if err != nil {
			// Fall back to the library call: it reports token refresh and
			// logout errors in the form handleAuthError understands.
			plain, err := c.GetContactsV2(batch)
			if err != nil {
				return err
			}
			found = found[:0]
			for _, wrapped := range plain.Contacts {
				p := wrapped.Contact
				found = append(found, contactEntry{person: person{ID: p.Mid, Name: p.EffectiveDisplayName(), Status: p.StatusMessage, Avatar: avatarURL(p.PicturePath), Friend: true}})
			}
		}
		for _, p := range found {
			// The phone's friend list leaves out hidden (隱藏), blocked
			// (封鎖) and deleted friends; getAllContactIds still returns them.
			if p.ID == "" || blocked[p.ID] || p.hidden || p.blocked || p.deleted {
				continue
			}
			contacts = append(contacts, p.person)
		}
	}
	sort.Slice(contacts, func(i, j int) bool { return contacts[i].Name < contacts[j].Name })
	groups, err := c.GetAllChatMids(true, false)
	if err != nil {
		return err
	}
	// Groups and rooms are listed only while the account is a member. Message
	// boxes also return groups the user left (or was removed from); those have
	// no name and used to show up as raw chat IDs, inflating the group count.
	member := make(map[string]bool, len(groups.MemberChatMids))
	for _, id := range groups.MemberChatMids {
		member[id] = true
	}
	chats := map[string]chat{}
	for _, old := range b.data.Chats {
		if old.Kind == "direct" || member[old.ID] {
			// Listed again only if its message box is still active below.
			old.HasMessages = false
			chats[old.ID] = old
		}
	}
	// Direct chats get the friend's current name and picture; a friend without
	// any message box is not listed as a chat (LINE Desktop only lists talks).
	friendByID := map[string]person{}
	for _, p := range contacts {
		friendByID[p.ID] = p
	}
	for start := 0; start < len(groups.MemberChatMids); start += 100 {
		found, err := c.GetChats(groups.MemberChatMids[start:min(start+100, len(groups.MemberChatMids))], true, false)
		if err != nil {
			return err
		}
		for _, g := range found.Chats {
			row := chats[g.ChatMid]
			row.ID = g.ChatMid
			row.Name = g.ChatName
			row.Kind = chatKind(g.ChatMid)
			row.Avatar = avatarURL(g.PicturePath)
			if row.Name == "" {
				row.Name = "群組聊天"
			}
			row.Muted = g.NotificationDisabled
			favorite, _ := g.FavoriteTimestamp.Int64()
			row.Favorite = favorite > 0
			if g.Extra.GroupExtra != nil {
				row.MemberCount = len(g.Extra.GroupExtra.MemberMids)
				row.Members = groupMembers(g.Extra.GroupExtra.MemberMids)
			}
			chats[row.ID] = row
		}
	}
	b.data.Contacts = contacts
	minID := ""
	for page := 0; page < 100; page++ {
		// ActiveOnly returns the talks LINE itself lists. Without it the
		// server also returns every box ever opened (hundreds), most of them
		// empty because their messages were deleted or have expired.
		boxes, err := c.GetMessageBoxes(line.MessageBoxesOptions{MinChatID: minID, ActiveOnly: true, MessageBoxCountLimit: 100, WithUnreadCount: true, LastMessagesPerMessageBoxCount: 1})
		if err != nil {
			return err
		}
		for _, box := range boxes.MessageBoxes {
			if chatKind(box.ID) != "direct" && !member[box.ID] {
				continue
			}
			if !validMID(box.ID) {
				continue
			}
			row := chats[box.ID]
			row.ID = box.ID
			row.Kind = chatKind(box.ID)
			row.HasMessages = true
			if p, ok := friendByID[box.ID]; ok {
				row.Name, row.Avatar = p.Name, p.Avatar
			}
			if row.Name == "" {
				row.Name = b.personName(box.ID)
			}
			unread, _ := box.UnreadCount.Int64()
			row.Unread = int(unread)
			for i := range box.LastMessages {
				msg := b.convertMessage(&box.LastMessages[i])
				b.rememberMessage(msg)
				if msg.Time >= row.UpdatedAt {
					row.LastMessage = b.preview(row, msg)
					row.UpdatedAt = msg.Time
				}
			}
			chats[box.ID] = row
		}
		if !boxes.HasNext || len(boxes.MessageBoxes) == 0 {
			break
		}
		next := boxes.MessageBoxes[len(boxes.MessageBoxes)-1].ID
		if next == minID {
			break
		}
		minID = next
	}
	b.data.Chats = make([]chat, 0, len(chats))
	for _, row := range chats {
		if row.Kind == "direct" {
			if p, ok := friendByID[row.ID]; ok {
				row.Name, row.Avatar = p.Name, p.Avatar
			}
		}
		if row.HasMessages || row.Kind != "direct" {
			b.data.Chats = append(b.data.Chats, row)
		}
	}
	b.resolvePeople(friendByID)
	sort.Slice(b.data.Chats, func(i, j int) bool {
		if b.data.Chats[i].UpdatedAt == b.data.Chats[j].UpdatedAt {
			return b.data.Chats[i].Name < b.data.Chats[j].Name
		}
		return b.data.Chats[i].UpdatedAt > b.data.Chats[j].UpdatedAt
	})
	b.emit("snapshot", b.data)
	return nil
}
func (b *backend) rememberMessage(m message) {
	if m.ID == "" {
		return
	}
	for _, old := range b.messages[m.ChatID] {
		if old.ID == m.ID {
			return
		}
	}
	list := append(b.messages[m.ChatID], m)
	if len(list) > 500 {
		list = list[len(list)-500:]
	}
	b.messages[m.ChatID] = list
	b.seen[m.ID] = struct{}{}
	b.seenOrder = append(b.seenOrder, m.ID)
	if len(b.seenOrder) > 10000 {
		id := b.seenOrder[0]
		delete(b.seen, id)
		delete(b.rawMessages, id)
		delete(b.decryptedPayloads, id)
		b.seenOrder = b.seenOrder[1:]
	}
}
func (b *backend) listMessages(chatID string, limit int) ([]message, error) {
	raw, err := b.api().GetRecentMessagesV2(chatID, limit)
	if err != nil {
		return nil, err
	}
	result := make([]message, 0, len(raw))
	for _, m := range raw {
		if m == nil {
			continue
		}
		msg := b.convertMessage(m)
		if msg.ChatID != chatID {
			continue
		}
		b.rememberMessage(msg)
		result = append(result, msg)
	}
	sort.SliceStable(result, func(i, j int) bool {
		if result[i].Time == result[j].Time {
			return result[i].ID < result[j].ID
		}
		return result[i].Time < result[j].Time
	})
	return result, nil
}
func (b *backend) convertMessage(raw *line.Message) message {
	mid := b.session.MID
	chatID := raw.To
	if raw.ToType == 0 && raw.From != mid {
		chatID = raw.From
	}
	created, _ := raw.CreatedTime.Int64()
	m := message{ID: raw.ID, ChatID: chatID, SenderID: raw.From, SenderName: b.personName(raw.From), Mine: raw.From == mid, Time: created, Text: raw.Text, Kind: "text", Status: "received"}
	if m.Mine {
		m.Status = "sent"
	}
	if raw.RelatedMessageID != "" && raw.MessageRelationType == 3 {
		m.ReplyTo = raw.RelatedMessageID
	}
	if b.rawMessages == nil {
		b.rawMessages = map[string]*line.Message{}
	}
	if b.decryptedPayloads == nil {
		b.decryptedPayloads = map[string][]byte{}
	}
	b.rawMessages[raw.ID] = raw
	if len(raw.Chunks) == 0 && raw.ContentType == 0 && raw.Text != "" {
		m.Unencrypted = true
	}
	if len(raw.Chunks) > 0 {
		payload, cached := b.decryptedPayloads[raw.ID]
		var err error
		if !cached {
			payload, err = b.decryptPayload(raw)
		}
		if err != nil {
			m.Kind = "system"
			m.Text = "無法解密此訊息。請確認 Letter Sealing 已開啟，必要時重新掃描 QR code 登入。"
			return m
		}
		b.decryptedPayloads[raw.ID] = payload
		var parsed struct {
			Text string `json:"text"`
		}
		if err = json.Unmarshal(payload, &parsed); err != nil {
			m.Kind = "system"
			m.Text = "此加密訊息格式尚未支援。"
			return m
		}
		m.Text = parsed.Text
	}
	switch raw.ContentType {
	case 0:
		m.Text, m.Emoji = inlineEmoji(m.Text, parseReplace(b.decryptedPayloads[raw.ID], raw.ContentMetadata["REPLACE"]))
	case 7:
		m.Kind = "sticker"
		m.Text = "[貼圖]"
		m.StickerID = raw.ContentMetadata["STKID"]
		m.PackageID = raw.ContentMetadata["STKPKGID"]
		if numericID(m.StickerID) {
			m.URL = "https://stickershop.line-scdn.net/stickershop/v1/sticker/" + m.StickerID + "/android/sticker.png"
		}
	case 1:
		m.Kind = "image"
		m.Text = "[照片]"
		var thumb struct {
			Width  int `json:"width"`
			Height int `json:"height"`
		}
		if json.Unmarshal([]byte(raw.ContentMetadata["MEDIA_THUMB_INFO"]), &thumb) == nil {
			m.Width, m.Height = thumb.Width, thumb.Height
		}
	case 14:
		m.Kind = "file"
		m.Filename = raw.ContentMetadata["FILE_NAME"]
		m.Size, _ = strconv.ParseInt(raw.ContentMetadata["FILE_SIZE"], 10, 64)
		m.Text = "[檔案] " + m.Filename
	case 2:
		m.Kind = "system"
		m.Text = "[影片] 此版本尚未支援播放。"
	case 3:
		m.Kind = "system"
		m.Text = "[語音訊息] 此版本尚未支援播放。"
	case 6:
		m.Kind = "system"
		m.Text = "[通話通知]"
	default:
		m.Kind = "system"
		if m.Text == "" {
			m.Text = "[此版本尚未支援的 LINE 訊息]"
		}
	}
	return m
}
func numericID(id string) bool {
	if len(id) == 0 || len(id) > 30 {
		return false
	}
	for _, r := range id {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
func (b *backend) decryptPayload(raw *line.Message) ([]byte, error) {
	if b.crypto == nil {
		return nil, errors.New("missing crypto manager")
	}
	if len(raw.Chunks) < 5 {
		return nil, errors.New("incomplete encrypted message")
	}
	sender, err := e2ee.DecodeKeyID(raw.Chunks[len(raw.Chunks)-2])
	if err != nil {
		return nil, err
	}
	receiver, err := e2ee.DecodeKeyID(raw.Chunks[len(raw.Chunks)-1])
	if err != nil {
		return nil, err
	}
	if raw.ToType == 1 || raw.ToType == 2 {
		if err = b.ensurePublicKey(raw.From, sender); err != nil {
			return nil, err
		}
		if err = b.ensureGroupKey(raw.To, receiver, false); err != nil {
			return nil, err
		}
		text, _, err := b.crypto.DecryptGroupMessage(raw, raw.To)
		return []byte(text), err
	}
	peerMID, peerKey := raw.From, sender
	if b.crypto.IsMyKey(sender) {
		peerMID, peerKey = raw.To, receiver
	}
	if err = b.ensurePublicKey(peerMID, peerKey); err != nil {
		return nil, err
	}
	text, err := b.crypto.DecryptMessageV2(raw)
	return []byte(text), err
}
func (b *backend) ensurePublicKey(mid string, keyID int) error {
	if b.crypto.HasPeerPublicKey(keyID) {
		return nil
	}
	key, err := b.api().GetE2EEPublicKey(mid, 1, keyID)
	if err != nil {
		return err
	}
	id, err := key.KeyID.Int64()
	if err != nil || id != int64(keyID) {
		return errors.New("public key ID mismatch")
	}
	b.crypto.RegisterPeerPublicKey(keyID, key.PublicKey)
	if !b.crypto.HasPeerPublicKey(keyID) {
		return errors.New("invalid peer public key")
	}
	return nil
}
func (b *backend) ensureGroupKey(chatID string, keyID int, register bool) error {
	var key *line.E2EEGroupSharedKey
	var err error
	if keyID == 0 {
		key, err = b.api().GetLastE2EEGroupSharedKey(chatID)
	} else {
		key, err = b.api().GetE2EEGroupSharedKey(chatID, keyID)
	}
	if err != nil && register && line.IsGroupKeyNotFound(err) {
		if err = b.registerGroupKey(chatID); err != nil {
			return err
		}
		key, err = b.api().GetLastE2EEGroupSharedKey(chatID)
	}
	if err != nil {
		return err
	}
	if key == nil {
		return errors.New("missing group key")
	}
	if err = b.ensurePublicKey(key.Creator, key.CreatorKeyID); err != nil {
		return err
	}
	_, err = b.crypto.UnwrapGroupSharedKey(chatID, key)
	if errors.Is(err, e2ee.ErrMissingOwnPrivateKey) && register {
		if err = b.registerGroupKey(chatID); err != nil {
			return err
		}
		return b.ensureGroupKey(chatID, 0, false)
	}
	return err
}
func (b *backend) registerGroupKey(chatID string) error {
	for attempt := 0; attempt < 2; attempt++ {
		keys, err := b.api().GetLastE2EEPublicKeys(chatID)
		if err != nil {
			return err
		}
		if _, ok := keys[b.session.MID]; !ok {
			return errors.New("group registration does not contain caller")
		}
		members := make([]string, 0, len(keys))
		for mid, key := range keys {
			if !validMID(mid) || midKind(mid) != 'u' || key.KeyID <= 0 || key.KeyData == "" {
				return errors.New("invalid group registration member")
			}
			members = append(members, mid)
		}
		sort.Strings(members)
		ids := make([]int, len(members))
		pubs := make([]string, len(members))
		for i, mid := range members {
			ids[i] = keys[mid].KeyID
			pubs[i] = keys[mid].KeyData
		}
		encrypted, err := b.crypto.WrapRegistrationGroupKey(pubs)
		if err != nil {
			return err
		}
		err = b.api().RegisterE2EEGroupKey(1, chatID, members, ids, encrypted)
		if attempt == 0 && line.IsE2EEGroupMemberMismatch(err) {
			continue
		}
		return err
	}
	return errors.New("group members changed during registration")
}

// encryptPayload seals payload for chatID; register allows creating a group
// key when the group has none (false only for read-only diagnostics).
func (b *backend) encryptPayload(chatID string, contentType int, payload []byte, register bool) (chunks []string, toType int, err error) {
	if kind := midKind(chatID); kind == 'c' || kind == 'r' {
		toType = 2
		if kind == 'r' {
			toType = 1
		}
		if err = b.ensureGroupKey(chatID, 0, register); err == nil {
			chunks, err = b.crypto.EncryptGroupMessageRaw(chatID, b.session.MID, contentType, payload)
		}
	} else {
		key, fetchErr := b.api().NegotiateE2EEPublicKey(chatID)
		err = fetchErr
		if err == nil {
			var peer int64
			peer, err = key.KeyID.Int64()
			// negotiateE2EEPublicKey has no e2eeVersion field (it reports
			// specVersion), so E2EEVersion is 0 for normal peers.
			if err == nil && (peer <= 0 || key.PublicKey == "") {
				err = fmt.Errorf("recipient Letter Sealing unavailable: %w", line.ErrNoUsableE2EEPublicKey)
			}
			if err == nil {
				myRaw, myRunner, keyErr := b.crypto.MyKeyIDs()
				err = keyErr
				if err == nil {
					chunks, err = b.crypto.EncryptMessageV2Raw(chatID, b.session.MID, myRunner, key.PublicKey, myRaw, int(peer), contentType, payload)
				}
			}
		}
	}
	return chunks, toType, err
}

func (b *backend) sendPayload(chatID string, contentType int, metadata map[string]string, payload []byte) (*line.Message, error) {
	if b.crypto == nil {
		return nil, fail("encryption_keys", "加密金鑰尚未就緒，請重新登入。")
	}
	chunks, toType, err := b.encryptPayload(chatID, contentType, payload, true)
	if err != nil {
		if isAuthError(err) {
			return nil, err
		}
		if noSealing(err) {
			// Official accounts and very large groups have no Letter Sealing
			// keys at all. Like upstream beeper/line, text goes out unencrypted.
			if contentType == 0 {
				return b.sendUnsealedText(chatID, metadata, payload)
			}
			return nil, fail("encryption_unavailable", "此聊天不支援 Letter Sealing（官方帳號或超大群組），目前只能傳送文字、貼圖與照片。")
		}
		return nil, fail("encryption_unavailable", "無法建立加密通道。請確認對方已啟用 Letter Sealing，然後重試。訊息未送出。")
	}
	if metadata == nil {
		metadata = map[string]string{}
	}
	metadata["e2eeVersion"] = strconv.Itoa(e2ee.ChunksVersion(chunks))
	raw := &line.Message{From: b.session.MID, To: chatID, ToType: toType, ContentType: contentType, HasContent: contentType != 0, Chunks: chunks, ContentMetadata: metadata, CreatedTime: json.Number(strconv.FormatInt(time.Now().UnixMilli(), 10))}
	if b.pendingReply != "" {
		raw.RelatedMessageID = b.pendingReply
		raw.MessageRelationType = 3
		raw.RelatedMessageServiceCode = 1
	}
	b.sequence++
	sent, err := b.api().SendMessage(b.sequence, raw)
	if err != nil {
		if strings.Contains(err.Error(), "Extension does not support file upload") {
			return nil, fail("file_type", "LINE 拒絕此檔案格式。請壓縮成 ZIP 後再傳送。")
		}
		if isAuthError(err) {
			return nil, err
		}
		return nil, fail("send_uncertain", "無法確認訊息是否已送出。請先重新整理聊天記錄，再決定是否重送。")
	}
	if sent == nil || sent.ID == "" {
		return nil, fail("send_uncertain", "LINE 未回傳訊息編號。請先重新整理聊天記錄，再決定是否重送。")
	}
	raw.ID = sent.ID
	if sent.CreatedTime != "" {
		raw.CreatedTime = sent.CreatedTime
	}
	if b.decryptedPayloads == nil {
		b.decryptedPayloads = map[string][]byte{}
	}
	b.decryptedPayloads[raw.ID] = payload
	return raw, nil
}

// noSealing reports whether LINE says the chat has no usable Letter Sealing
// key (peer without keys, member with sealing off, group too large), as
// opposed to a network or key-handling failure, which must not fall back.
func noSealing(err error) bool {
	return line.IsNoUsableE2EEPublicKey(err) || line.IsNoUsableE2EEGroupKey(err)
}

// sendUnsealedText sends a text payload ({"text", optional "REPLACE"}) as a
// plain LINE message: body in Text, emoji ranges in ContentMetadata.
func (b *backend) sendUnsealedText(chatID string, metadata map[string]string, payload []byte) (*line.Message, error) {
	var body struct {
		Text    string          `json:"text"`
		Replace json.RawMessage `json:"REPLACE"`
	}
	if err := json.Unmarshal(payload, &body); err != nil {
		return nil, err
	}
	if metadata == nil {
		metadata = map[string]string{}
	}
	if len(body.Replace) > 0 {
		metadata["REPLACE"] = string(body.Replace)
	}
	raw := &line.Message{From: b.session.MID, To: chatID, ToType: toType(chatID), Text: body.Text, ContentMetadata: metadata, CreatedTime: json.Number(strconv.FormatInt(time.Now().UnixMilli(), 10))}
	if b.pendingReply != "" {
		raw.RelatedMessageID = b.pendingReply
		raw.MessageRelationType = 3
		raw.RelatedMessageServiceCode = 1
	}
	b.sequence++
	sent, err := b.api().SendMessage(b.sequence, raw)
	if err != nil {
		if isAuthError(err) {
			return nil, err
		}
		return nil, fail("send_uncertain", "無法確認訊息是否已送出。請先重新整理聊天記錄，再決定是否重送。")
	}
	if sent == nil || sent.ID == "" {
		return nil, fail("send_uncertain", "LINE 未回傳訊息編號。請先重新整理聊天記錄，再決定是否重送。")
	}
	raw.ID = sent.ID
	if sent.CreatedTime != "" {
		raw.CreatedTime = sent.CreatedTime
	}
	if b.unsealedNoticed == nil {
		b.unsealedNoticed = map[string]bool{}
	}
	if !b.unsealedNoticed[chatID] {
		b.unsealedNoticed[chatID] = true
		b.notice("此聊天不支援 Letter Sealing（官方帳號或超大群組），訊息以未加密方式傳送。")
	}
	return raw, nil
}

// sendPlain sends a message without Letter Sealing, used for media when the
// server's media flow for the chat is plain (DetermineMediaMessageFlow != 2).
func (b *backend) sendPlain(chatID string, contentType int, metadata map[string]string) (*line.Message, error) {
	raw := &line.Message{From: b.session.MID, To: chatID, ToType: toType(chatID), ContentType: contentType, HasContent: contentType != 0, ContentMetadata: metadata, CreatedTime: json.Number(strconv.FormatInt(time.Now().UnixMilli(), 10))}
	b.sequence++
	sent, err := b.api().SendMessage(b.sequence, raw)
	if err != nil {
		if isAuthError(err) {
			return nil, err
		}
		return nil, fail("send_uncertain", "無法確認訊息是否已送出。請先重新整理聊天記錄，再決定是否重送。")
	}
	if sent == nil || sent.ID == "" {
		return nil, fail("send_uncertain", "LINE 未回傳訊息編號。請先重新整理聊天記錄，再決定是否重送。")
	}
	raw.ID = sent.ID
	if sent.CreatedTime != "" {
		raw.CreatedTime = sent.CreatedTime
	}
	return raw, nil
}

func (b *backend) sendText(chatID, text string) (message, error) {
	return b.sendReply(chatID, text, "")
}

// sendReply sends text, quoting replyTo when set (messageRelationType 3, as
// upstream beeper/line does for Matrix replies).
func (b *backend) sendReply(chatID, text, replyTo string) (message, error) {
	payload, _ := json.Marshal(map[string]string{"text": text})
	b.pendingReply = replyTo
	raw, err := b.sendPayload(chatID, 0, nil, payload)
	b.pendingReply = ""
	if err != nil {
		return message{}, err
	}
	msg := b.convertMessage(raw)
	msg.Text = text
	msg.Kind = "text"
	return msg, nil
}
func isAuthError(err error) bool {
	return err != nil && (line.IsLoggedOut(err) || line.IsRefreshRequired(err) || line.HTTPStatusCode(err) == 401 || line.HTTPStatusCode(err) == 403)
}
func (b *backend) handleAuthError(err error) {
	if !isAuthError(err) || b.session == nil {
		return
	}
	if line.IsLoggedOut(err) {
		b.stop()
		b.clearData()
		b.sessionEvent("logged_out", "LINE 已終止此工作階段，請重新登入。")
		return
	}
	if refreshErr := refreshSaved(b.api(), b.session); refreshErr != nil {
		b.stop()
		b.clearData()
		b.sessionEvent("logged_out", "登入已過期，請重新登入。")
		return
	}
	b.saveSession()
	b.stopStream()
	b.startStream()
	b.notice("登入憑證已更新。請重試剛才的操作；傳送前請先查看聊天記錄。")
}

var _ = fmt.Sprint
