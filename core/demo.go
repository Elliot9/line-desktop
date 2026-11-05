package main

import (
	"fmt"
	"time"
)

func (b *backend) seedDemo() {
	now := time.Now().UnixMilli()
	b.data = snapshot{
		Profile:  &person{ID: "demo-me", Name: "Elliot", Status: "把日子過成喜歡的樣子。"},
		Contacts: []person{{ID: "demo-anna", Name: "安安", Status: "慢慢走，也是一種旅行。"}, {ID: "demo-yu", Name: "宇軒", Status: "今天也要喝杯好咖啡"}, {ID: "demo-lin", Name: "小林", Status: "午後散步中"}},
		Chats: []chat{
			{ID: "demo-anna", Name: "安安", Kind: "direct", LastMessage: "週末一起去山裡走走吧 🌿", UpdatedAt: now - 60000, Unread: 2},
			{ID: "demo-weekend", Name: "週末散步計畫", Kind: "group", LastMessage: "宇軒：我找到一家很棒的咖啡店", UpdatedAt: now - 600000, MemberCount: 5, Unread: 3},
			{ID: "demo-yu", Name: "宇軒", Kind: "direct", LastMessage: "照片整理好再傳給你！", UpdatedAt: now - 3600000},
			{ID: "demo-lin", Name: "小林", Kind: "direct", LastMessage: "收到，謝謝你 ✨", UpdatedAt: now - 7200000},
			{ID: "demo-design", Name: "日常設計研究室", Kind: "group", LastMessage: "靈感就藏在生活的小地方。", UpdatedAt: now - 86400000, MemberCount: 8},
		},
	}
	texts := []struct {
		text string
		mine bool
	}{{"嗨！這週過得還好嗎？", false}, {"忙裡偷閒，剛整理完手邊的事情。", true}, {"週末一起去山裡走走吧 🌿", false}, {"想找一條安靜的步道，帶杯咖啡，慢慢走。", false}, {"好呀，聽起來是很棒的週末。", true}}
	for i, t := range texts {
		sender, name := "demo-anna", "安安"
		if t.mine {
			sender = "demo-me"
			name = "Elliot"
		}
		m := message{ID: fmt.Sprintf("demo-%d", i), ChatID: "demo-anna", SenderID: sender, SenderName: name, Text: t.text, Mine: t.mine, Time: now - int64(15-i)*60000, Kind: "text", Status: "received"}
		if t.mine {
			m.Status = "sent"
		}
		b.rememberMessage(m)
	}
	for _, c := range b.data.Chats[1:] {
		b.rememberMessage(message{ID: "seed-" + c.ID, ChatID: c.ID, SenderID: c.ID, SenderName: c.Name, Text: c.LastMessage, Time: c.UpdatedAt, Kind: "text", Status: "received"})
	}
	b.sessionEvent("ready", "離線展示。訊息只保留在這次執行，不會傳到 LINE。")
	b.emit("snapshot", b.data)
}

func (b *backend) demoSend(chatID, text string) message {
	b.sequence++
	return message{ID: fmt.Sprintf("demo-sent-%d", b.sequence), ChatID: chatID, SenderID: b.data.Profile.ID, SenderName: b.data.Profile.Name, Text: text, Mine: true, Time: time.Now().UnixMilli(), Kind: "text", Status: "sent"}
}
