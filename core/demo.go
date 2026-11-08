package main

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Offline demo content mirrors index.html so the GTK client can be compared
// with the reference design without connecting to LINE.
var demoMoods = []string{"出發！", "收到 ♡", "好期待", "謝謝你", "早安", "一起加油", "哈哈哈", "晚安"}

type demoLine struct {
	sender string
	text   string
	ago    time.Duration
	kind   string
	extra  func(*message)
}

func (b *backend) seedDemo() {
	now := time.Now()
	b.data = snapshot{
		Profile: &person{ID: "demo-me", Name: "林以安", Status: "把日子過成喜歡的樣子。"},
		Contacts: []person{
			{ID: "demo-yiting", Name: "陳怡庭", Status: "山裡見，咖啡店也見。", Friend: true},
			{ID: "demo-hao", Name: "林子豪", Status: "今天也想出去走走。", Friend: true},
			{ID: "demo-momo", Name: "Momo", Status: "生活的靈感收集中 ✨", Friend: true},
			{ID: "demo-jia", Name: "嘉嘉", Status: "慢慢來，比較快。", Friend: true},
			{ID: "demo-kai", Name: "許凱文", Status: "設計是生活的練習。", Friend: true},
		},
		// A group member who is not a friend still resolves to a name.
		People: map[string]person{"demo-lee": {ID: "demo-lee", Name: "李小薇"}},
	}
	day := 24 * time.Hour
	conversations := []struct {
		row   chat
		lines []demoLine
	}{
		{chat{ID: "demo-weekend", Name: "週末散步計畫", Kind: "group", Favorite: true, Members: []string{"demo-me", "demo-yiting", "demo-hao", "demo-momo", "demo-lee"}, ReadBy: map[string]int64{"demo-yiting": now.UnixMilli(), "demo-hao": now.UnixMilli(), "demo-momo": now.Add(-5 * time.Minute).UnixMilli()}}, []demoLine{
			{"demo-yiting", "這週六天氣好像很不錯 ☀️\n我們去陽明山走走，好不好？", 9 * time.Minute, "text", nil},
			{"demo-hao", "好啊！想念山上的空氣了 🌿", 7 * time.Minute, "text", nil},
			{"demo-yiting", "[照片]", 6 * time.Minute, "image", func(m *message) { m.URL = "demo:collage"; m.Width = 640; m.Height = 480 }},
			{"demo-me", "光看照片就開始期待了！\n走完再去喝杯咖啡吧 ☕", 4 * time.Minute, "text", func(m *message) { m.ReplyTo = "demo-0-2" }},
			{"demo-lee", "我也想去！可以帶我家的狗嗎 🐶", 3*time.Minute + 30*time.Second, "text", nil},
			{"demo-momo", "我把路線整理好了，大家可以先看看", 3 * time.Minute, "text", nil},
			{"demo-momo", "[檔案] 散步路線提案.pdf", 3*time.Minute - time.Second, "file", func(m *message) { m.Filename = "散步路線提案.pdf"; m.Size = 2468 }},
			{"demo-hao", demoMoods[0], time.Minute, "sticker", func(m *message) { m.URL = "demo:bear-0"; m.StickerID = "bear-0"; m.PackageID = "demo" }},
		}},
		{chat{ID: "demo-yiting", Name: "陳怡庭", Kind: "direct", Unread: 2}, []demoLine{
			{"demo-yiting", "下次去這家咖啡店吧 ☕", 45 * time.Minute, "text", nil},
			{"demo-yiting", "聽說他們的肉桂捲很好吃！", 44 * time.Minute, "text", nil},
		}},
		{chat{ID: "demo-design", Name: "日常設計研究所", Kind: "group", Unread: 5, Members: []string{"demo-me", "demo-kai", "demo-momo"}}, []demoLine{
			{"demo-kai", "新的版面我放在筆記本了，大家有空幫我看看～", 70 * time.Minute, "text", nil},
		}},
		{chat{ID: "demo-family", Name: "我們這一家", Kind: "group", Unread: 1, Members: []string{"demo-me", "demo-jia", "demo-hao"}}, []demoLine{
			{"demo-jia", "晚上回家吃飯嗎？今天煮了你最愛的湯 🍲", day + 2*time.Hour, "text", nil},
		}},
		{chat{ID: "demo-hao", Name: "林子豪", Kind: "direct", ReadBy: map[string]int64{"demo-hao": now.UnixMilli()}}, []demoLine{
			{"demo-me", "沒問題，週末見！", day + 3*time.Hour, "text", nil},
			{"demo-hao", "收到，週末見 🙌", day + 3*time.Hour - time.Minute, "text", nil},
		}},
		{chat{ID: "demo-momo", Name: "Momo", Kind: "direct", Muted: true}, []demoLine{
			{"demo-momo", "謝謝你分享的歌單，好喜歡 🎧", 3*day + 5*time.Hour, "text", nil},
		}},
	}
	b.data.Chats = make([]chat, 0, len(conversations))
	for c, conversation := range conversations {
		row := conversation.row
		row.MemberCount = len(row.Members)
		row.HasMessages = true
		for i, line := range conversation.lines {
			m := message{ID: fmt.Sprintf("demo-%d-%d", c, i), ChatID: row.ID, SenderID: line.sender, SenderName: b.personName(line.sender), Text: line.text, Kind: line.kind, Mine: line.sender == b.data.Profile.ID, Time: now.Add(-line.ago).UnixMilli(), Status: "received"}
			if m.Mine {
				m.Status = "sent"
			}
			if line.extra != nil {
				line.extra(&m)
			}
			b.rememberMessage(m)
			row.LastMessage = demoPreview(row, m)
			row.UpdatedAt = m.Time
		}
		b.data.Chats = append(b.data.Chats, row)
	}
	b.sessionEvent("ready", "離線展示。訊息只保留在這次執行，不會傳到 LINE。")
	b.emit("snapshot", b.data)
}

func demoPreview(row chat, m message) string {
	text := map[string]string{"sticker": "[貼圖]", "image": "[照片]", "file": "[檔案] " + m.Filename}[m.Kind]
	if text == "" {
		text = m.Text
	}
	if row.Kind == "group" {
		if m.Mine {
			return "你：" + text
		}
		return m.SenderName + "：" + text
	}
	return text
}

func demoSticker(id string) (string, bool) {
	index, err := strconv.Atoi(strings.TrimPrefix(id, "bear-"))
	if !strings.HasPrefix(id, "bear-") || err != nil || index < 0 || index >= len(demoMoods) {
		return "", false
	}
	return demoMoods[index], true
}

func (b *backend) demoSend(chatID, text string) message {
	b.sequence++
	return message{ID: fmt.Sprintf("demo-sent-%d", b.sequence), ChatID: chatID, SenderID: b.data.Profile.ID, SenderName: b.data.Profile.Name, Text: text, Mine: true, Time: time.Now().UnixMilli(), Kind: "text", Status: "sent"}
}
