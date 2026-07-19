package services

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestExplodeTurnsToMessages_Empty verifies that an empty turn slice produces
// an empty message slice (not nil — the function always allocates).
func TestExplodeTurnsToMessages_Empty(t *testing.T) {
	msgs := ExplodeTurnsToMessages(nil)
	if len(msgs) != 0 {
		t.Errorf("expected 0 messages, got %d", len(msgs))
	}
}

// TestExplodeTurnsToMessages_SinglePlanTurn verifies the basic explosion: one
// plan turn → one user message + one assistant message.
func TestExplodeTurnsToMessages_SinglePlanTurn(t *testing.T) {
	turns := []ConversationTurn{
		{
			Question: "What are the top products?",
			SQL:      "SELECT name FROM products LIMIT 10",
			RowCount: 10,
			Kind:     "plan",
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}

	// User message
	if msgs[0].Role != "user" || msgs[0].Kind != "question" {
		t.Errorf("user message: got role=%q kind=%q", msgs[0].Role, msgs[0].Kind)
	}
	if msgs[0].Content != "What are the top products?" {
		t.Errorf("user content: %q", msgs[0].Content)
	}

	// Assistant message
	if msgs[1].Role != "assistant" || msgs[1].Kind != "plan" {
		t.Errorf("assistant message: got role=%q kind=%q", msgs[1].Role, msgs[1].Kind)
	}
	if !strings.Contains(string(msgs[1].Content), "SELECT name FROM products") {
		t.Errorf("assistant content missing SQL: %q", msgs[1].Content)
	}
	if !strings.Contains(string(msgs[1].Content), "returned 10 rows") {
		t.Errorf("assistant content missing row count: %q", msgs[1].Content)
	}

	// Payload should be valid JSON with sql and row_count
	var payload map[string]interface{}
	if err := json.Unmarshal(msgs[1].Payload, &payload); err != nil {
		t.Fatalf("payload unmarshal: %v", err)
	}
	if payload["sql"] != "SELECT name FROM products LIMIT 10" {
		t.Errorf("payload sql: %v", payload["sql"])
	}
	if payload["row_count"].(float64) != 10 {
		t.Errorf("payload row_count: %v", payload["row_count"])
	}
}

// TestExplodeTurnsToMessages_LegacyTurn verifies that a turn with Kind=""
// (pre-PR4 legacy row) is treated as a plan turn.
func TestExplodeTurnsToMessages_LegacyTurn(t *testing.T) {
	turns := []ConversationTurn{
		{
			Question: "Legacy question",
			SQL:      "SELECT 1",
			RowCount: 1,
			Kind:     "", // legacy — no kind column before PR4
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}
	if msgs[1].Kind != "plan" {
		t.Errorf("legacy turn should become kind=plan, got %q", msgs[1].Kind)
	}
}

// TestExplodeTurnsToMessages_ExistingPayloadPreserved verifies that when a turn
// already has a structured payload, it is used as-is (not re-synthesized).
func TestExplodeTurnsToMessages_ExistingPayloadPreserved(t *testing.T) {
	existingPayload, _ := json.Marshal(map[string]interface{}{
		"sql":        "SELECT 1",
		"row_count":  5,
		"confidence": 0.95,
	})
	turns := []ConversationTurn{
		{
			Question: "Q",
			SQL:      "SELECT 1",
			RowCount: 5,
			Kind:     "plan",
			Payload:  existingPayload,
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(msgs[1].Payload, &payload); err != nil {
		t.Fatalf("payload unmarshal: %v", err)
	}
	// The existing payload includes confidence — verify it's preserved
	if payload["confidence"].(float64) != 0.95 {
		t.Errorf("existing payload not preserved: %v", payload)
	}
}

// TestExplodeTurnsToMessages_SQLTruncation verifies that very long SQL is
// truncated in the assistant message content.
func TestExplodeTurnsToMessages_SQLTruncation(t *testing.T) {
	longSQL := "SELECT " + strings.Repeat("x, ", 1000)
	turns := []ConversationTurn{
		{
			Question: "Q",
			SQL:      longSQL,
			RowCount: 1,
			Kind:     "plan",
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}
	// Content should be truncated (content < full SQL)
	if len(msgs[1].Content) >= len(longSQL) {
		t.Errorf("SQL not truncated: content len=%d, sql len=%d", len(msgs[1].Content), len(longSQL))
	}
	// Should contain the ellipsis marker
	if !strings.Contains(msgs[1].Content, "…") {
		t.Error("truncated SQL should contain ellipsis marker")
	}
}

// TestExplodeTurnsToMessages_TotalBudgetTruncation verifies that when the
// total content exceeds maxTotalTranscriptChars, oldest messages are dropped.
func TestExplodeTurnsToMessages_TotalBudgetTruncation(t *testing.T) {
	var turns []ConversationTurn
	for i := 0; i < 50; i++ {
		turns = append(turns, ConversationTurn{
			Question: strings.Repeat("Q", 500), // 500 chars per question
			SQL:      "SELECT 1",
			RowCount: 1,
			Kind:     "plan",
		})
	}
	msgs := ExplodeTurnsToMessages(turns)

	// Should have fewer messages than 50*2 = 100 due to truncation
	total := 0
	for _, m := range msgs {
		total += len(m.Content)
	}
	if total > maxTotalTranscriptChars {
		t.Errorf("total content %d exceeds budget %d", total, maxTotalTranscriptChars)
	}
	if len(msgs) >= 100 {
		t.Errorf("expected truncated messages, got %d", len(msgs))
	}
}

// TestExplodeTurnsToMessages_ClarificationTurn verifies clarification turn
// explosion (forward-compatible for PR5).
func TestExplodeTurnsToMessages_ClarificationTurn(t *testing.T) {
	clarPayload, _ := json.Marshal(map[string]interface{}{
		"question": "Which region do you mean?",
		"options":  []string{"Europe", "Asia"},
	})
	turns := []ConversationTurn{
		{
			Question: "Show data for India",
			Kind:     "clarification",
			Payload:  clarPayload,
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}
	if msgs[1].Kind != "clarification" {
		t.Errorf("expected kind=clarification, got %q", msgs[1].Kind)
	}
	if msgs[1].Content != "Which region do you mean?" {
		t.Errorf("clarification content: %q", msgs[1].Content)
	}
}

// TestExplodeTurnsToMessages_ClarificationWithoutPayload verifies graceful
// handling of a clarification turn with no payload.
func TestExplodeTurnsToMessages_ClarificationWithoutPayload(t *testing.T) {
	turns := []ConversationTurn{
		{
			Question: "Something",
			Kind:     "clarification",
		},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(msgs))
	}
	if msgs[1].Content != "(asked a clarifying question)" {
		t.Errorf("expected fallback content, got: %q", msgs[1].Content)
	}
}

// TestExplodeTurnsToMessages_MultiTurnConversation verifies a realistic
// multi-turn conversation with mixed kinds.
func TestExplodeTurnsToMessages_MultiTurnConversation(t *testing.T) {
	turns := []ConversationTurn{
		{Question: "Show revenue", SQL: "SELECT SUM(revenue) FROM sales", RowCount: 1, Kind: "plan"},
		{Question: "Break down by region", SQL: "SELECT region, SUM(revenue) FROM sales GROUP BY 1", RowCount: 5, Kind: "plan"},
	}
	msgs := ExplodeTurnsToMessages(turns)
	if len(msgs) != 4 {
		t.Fatalf("expected 4 messages, got %d", len(msgs))
	}
	// Verify ordering: user, assistant, user, assistant
	for i, expected := range []string{"user", "assistant", "user", "assistant"} {
		if msgs[i].Role != expected {
			t.Errorf("msgs[%d].Role = %q, want %q", i, msgs[i].Role, expected)
		}
	}
}
