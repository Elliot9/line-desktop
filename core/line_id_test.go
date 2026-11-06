package main

import (
	"strings"
	"testing"

	"github.com/beeper/line/pkg/line"
)

func TestLINEIDFormats(t *testing.T) {
	long := "U" + strings.Repeat("a1B2c3D4e5", 4) + "xYz"
	for _, mid := range []string{"u8ae764e8e69e6bd4ecdd9b6ea0c40fce", long, "C" + long[1:], "r" + long[1:]} {
		if !validMID(mid) {
			t.Fatalf("rejected LINE ID %q", mid)
		}
	}
	for _, mid := range []string{"", "u-short", "x" + long[1:], "U" + strings.Repeat("a", 30) + "/../x", "U" + strings.Repeat("a", 200)} {
		if validMID(mid) {
			t.Fatalf("accepted invalid ID %q", mid)
		}
	}
	if chatKind("C"+long[1:]) != "group" || chatKind("R"+long[1:]) != "room" || chatKind(long) != "direct" {
		t.Fatal("chat kind must ignore prefix case")
	}
	if toType("C"+long[1:]) != 2 || toType("R"+long[1:]) != 1 || toType(long) != 0 {
		t.Fatal("send type must ignore prefix case")
	}
}

func TestAccountProblemDiagnosis(t *testing.T) {
	mid := "u8ae764e8e69e6bd4ecdd9b6ea0c40fce"
	long := "U" + strings.Repeat("a1B2c3D4e5", 4) + "xYz"
	for _, id := range []string{mid, long} {
		if problem := accountProblem(&line.Profile{Mid: id}, id); problem != "" {
			t.Fatalf("matching account rejected: %s", problem)
		}
	}
	profile := &line.Profile{}
	if problem := accountProblem(profile, long); problem != "" || profile.Mid != long {
		t.Fatalf("empty profile MID should fall back to the login MID: %q", problem)
	}
	other := "uf1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"
	problem := accountProblem(&line.Profile{Mid: mid}, other)
	if !strings.HasPrefix(problem, "mid-mismatch") || strings.Contains(problem, mid) || strings.Contains(problem, other) {
		t.Fatalf("mismatch must be reported without MID values: %q", problem)
	}
	if problem := accountProblem(&line.Profile{Mid: "u-short"}, ""); problem != `profile-mid-format len=7 prefix="u" chars=other` {
		t.Fatalf("unexpected format diagnosis: %q", problem)
	}
	if accountProblem(nil, mid) != "profile-missing" || accountProblem(&line.Profile{}, "") != "profile-mid-empty, login empty" {
		t.Fatal("missing profile data must be diagnosed")
	}
}
