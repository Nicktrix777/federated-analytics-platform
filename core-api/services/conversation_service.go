package services

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"

	"github.com/federated-analytics/core-api/models"
)

// ConversationService persists NL-query conversations in postgres-meta so
// follow-up questions can be answered with the prior turns as context.
//
// The conversation id is client-supplied (the frontend mints one UUID per chat
// session). EnsureConversation upserts it; RecentTurns / AppendTurn read and
// grow the transcript. All methods are best-effort at the call site — a failure
// here must never fail the query itself, only drop the multi-turn context.
//
// PR4: turns now carry a kind (plan|clarification) and a JSON payload. The
// transcript is replayed as structured ChatMessage objects via BuildMessages,
// replacing the old flat text context.

type ConversationService struct {
	db *sql.DB
}

func NewConversationService(db *sql.DB) *ConversationService {
	return &ConversationService{db: db}
}

// ConversationTurn is one completed exchange in a conversation.
type ConversationTurn struct {
	ID        int64
	Question  string
	SQL       string
	RowCount  int
	Kind      string          // "plan" | "clarification"
	Payload   json.RawMessage // structured data (plan: {sql, row_count, confidence}; clarification: {options, ...})
	CreatedAt time.Time
}

// ConversationSummary is a row for the sidebar "Chats" list — one entry per
// conversation that has at least one recorded turn.
type ConversationSummary struct {
	ID           string    `json:"id"`
	Title        string    `json:"title"`
	TurnCount    int       `json:"turn_count"`
	CreatedAt    time.Time `json:"created_at"`
	LastActiveAt time.Time `json:"last_active_at"`
}

// ValidConversationID reports whether id is a well-formed UUID. Invalid/empty
// ids are treated as "no conversation" by callers (memory simply disabled).
func ValidConversationID(id string) bool {
	if id == "" {
		return false
	}
	_, err := uuid.Parse(id)
	return err == nil
}

// EnsureConversation creates the conversation row if absent, or bumps its
// last_active_at if it exists. Returns an error only on a real DB failure.
func (s *ConversationService) EnsureConversation(id string) error {
	if !ValidConversationID(id) {
		return fmt.Errorf("invalid conversation id")
	}
	_, err := s.db.Exec(`
		INSERT INTO conversations (id) VALUES ($1)
		ON CONFLICT (id) DO UPDATE SET last_active_at = now()
	`, id)
	return err
}

// RecentTurns returns up to `limit` most-recent turns for a conversation,
// re-ordered oldest-first so they read as a transcript.
func (s *ConversationService) RecentTurns(id string, limit int) ([]ConversationTurn, error) {
	if !ValidConversationID(id) {
		return nil, nil
	}
	rows, err := s.db.Query(`
		SELECT question, COALESCE(sql, ''), COALESCE(row_count, 0),
		       COALESCE(kind, 'plan'), payload, created_at
		FROM conversation_turns
		WHERE conversation_id = $1
		ORDER BY created_at DESC
		LIMIT $2
	`, id, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var turns []ConversationTurn
	for rows.Next() {
		var t ConversationTurn
		var payload []byte
		if err := rows.Scan(&t.Question, &t.SQL, &t.RowCount, &t.Kind, &payload, &t.CreatedAt); err != nil {
			return nil, err
		}
		if payload != nil {
			t.Payload = json.RawMessage(payload)
		}
		turns = append(turns, t)
	}
	// Reverse into chronological order (oldest first).
	for i, j := 0, len(turns)-1; i < j; i, j = i+1, j-1 {
		turns[i], turns[j] = turns[j], turns[i]
	}
	return turns, nil
}

// ListConversations returns up to `limit` conversations that have at least one
// recorded turn, most-recently-active first. Zero-turn conversations (created
// by EnsureConversation but never used) are skipped so the sidebar only shows
// real chats. Title falls back to a placeholder if never populated.
func (s *ConversationService) ListConversations(limit int) ([]ConversationSummary, error) {
	rows, err := s.db.Query(`
		SELECT c.id::text,
		       COALESCE(NULLIF(c.title, ''), 'Untitled chat'),
		       COUNT(t.id),
		       c.created_at,
		       c.last_active_at
		FROM conversations c
		JOIN conversation_turns t ON t.conversation_id = c.id
		GROUP BY c.id, c.title, c.created_at, c.last_active_at
		ORDER BY c.last_active_at DESC
		LIMIT $1
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []ConversationSummary
	for rows.Next() {
		var cs ConversationSummary
		if err := rows.Scan(&cs.ID, &cs.Title, &cs.TurnCount, &cs.CreatedAt, &cs.LastActiveAt); err != nil {
			return nil, err
		}
		out = append(out, cs)
	}
	return out, rows.Err()
}

// ListTurns returns all turns for a conversation, oldest-first, for hydrating
// the transcript when a chat is reopened from the sidebar. Unlike RecentTurns
// it is unbounded (a single chat is small) and carries the turn id.
func (s *ConversationService) ListTurns(id string) ([]ConversationTurn, error) {
	if !ValidConversationID(id) {
		return nil, nil
	}
	rows, err := s.db.Query(`
		SELECT id, question, COALESCE(sql, ''), COALESCE(row_count, 0),
		       COALESCE(kind, 'plan'), payload, created_at
		FROM conversation_turns
		WHERE conversation_id = $1
		ORDER BY created_at ASC
	`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var turns []ConversationTurn
	for rows.Next() {
		var t ConversationTurn
		var payload []byte
		if err := rows.Scan(&t.ID, &t.Question, &t.SQL, &t.RowCount, &t.Kind, &payload, &t.CreatedAt); err != nil {
			return nil, err
		}
		if payload != nil {
			t.Payload = json.RawMessage(payload)
		}
		turns = append(turns, t)
	}
	return turns, rows.Err()
}

// AppendTurn records a completed exchange with its kind and structured payload.
// The parent conversation's title is set from the first recorded question
// (COALESCE keeps an existing title untouched).
func (s *ConversationService) AppendTurn(id, question, kind, sqlText string, rowCount int, payload []byte) error {
	if !ValidConversationID(id) {
		return fmt.Errorf("invalid conversation id")
	}
	_, err := s.db.Exec(`
		INSERT INTO conversation_turns (conversation_id, question, sql, row_count, kind, payload)
		VALUES ($1, $2, $3, $4, $5, $6)
	`, id, question, sqlText, rowCount, kind, payload)
	if err != nil {
		return err
	}
	// Keep the parent's activity timestamp fresh and name the thread from the
	// first question (conversations.title has been unused since the baseline —
	// this finally populates it).
	_, _ = s.db.Exec(
		`UPDATE conversations SET last_active_at = now(), title = COALESCE(title, LEFT($2, 80)) WHERE id = $1`,
		id, question,
	)
	return nil
}

// ── Structured transcript replay ─────────────────────────────

// maxSQLCharsPerMessage caps how much SQL text is included in a single
// assistant message's content — long CTEs bloat the prompt without adding
// signal for the follow-up.
const maxSQLCharsPerMessage = 2000

// maxTotalTranscriptChars caps the total character budget for the rendered
// transcript. This is a successor to the old 8k conversation_context cap,
// applied at the Go layer before the messages even reach the AI Engine.
const maxTotalTranscriptChars = 12000

// BuildMessages loads recent turns and explodes them into ChatMessage objects
// suitable for sending to the AI Engine. Returns nil (not an error) when there
// are no turns or the conversation is invalid.
func (s *ConversationService) BuildMessages(id string, turnLimit int) ([]models.ChatMessage, error) {
	turns, err := s.RecentTurns(id, turnLimit)
	if err != nil {
		return nil, err
	}
	if len(turns) == 0 {
		return nil, nil
	}
	return ExplodeTurnsToMessages(turns), nil
}

// ExplodeTurnsToMessages converts a chronological slice of ConversationTurn
// into the ChatMessage pairs the AI Engine expects. This is a free function
// (no DB dependency) so it can be unit-tested trivially.
//
// Each turn produces:
//   - user message  {role:user, kind:question, content:<question>}
//   - assistant message whose shape depends on turn.Kind:
//     - "plan": {role:assistant, kind:plan, content:"SQL: <sql> (returned N rows)", payload:{sql, row_count}}
//     - "clarification": {role:assistant, kind:clarification, content:<question from payload>}
//
// Legacy rows (Kind="" or missing fields) are treated as plan turns with
// payload synthesized from the turn's question/sql/row_count columns.
//
// The total content length is bounded by maxTotalTranscriptChars; messages
// are dropped from the oldest end when the budget is exceeded.
func ExplodeTurnsToMessages(turns []ConversationTurn) []models.ChatMessage {
	msgs := make([]models.ChatMessage, 0, len(turns)*2)

	for _, t := range turns {
		kind := t.Kind
		if kind == "" {
			kind = "plan"
		}

		// User message — always present.
		msgs = append(msgs, models.ChatMessage{
			Role:    "user",
			Kind:    "question",
			Content: t.Question,
		})

		// Assistant message — shape depends on kind.
		switch kind {
		case "plan":
			sqlDisplay := t.SQL
			if len(sqlDisplay) > maxSQLCharsPerMessage {
				sqlDisplay = sqlDisplay[:maxSQLCharsPerMessage] + "…"
			}
			content := fmt.Sprintf("SQL: %s", sqlDisplay)
			if t.RowCount > 0 {
				content += fmt.Sprintf(" (returned %d rows)", t.RowCount)
			}

			// Build payload from turn fields (works for both new and legacy rows).
			payload := buildPlanPayload(t.SQL, t.RowCount, t.Payload)

			msgs = append(msgs, models.ChatMessage{
				Role:    "assistant",
				Kind:    "plan",
				Content: content,
				Payload: payload,
			})

		case "clarification":
			// Extract the clarification question from the payload if available.
			clarContent := "(asked a clarifying question)"
			if t.Payload != nil {
				var p struct {
					Question string `json:"question"`
				}
				if json.Unmarshal(t.Payload, &p) == nil && p.Question != "" {
					clarContent = p.Question
				}
			}
			msgs = append(msgs, models.ChatMessage{
				Role:    "assistant",
				Kind:    "clarification",
				Content: clarContent,
				Payload: t.Payload,
			})
		}
	}

	// Enforce total content budget — drop from the oldest end.
	return truncateMessages(msgs, maxTotalTranscriptChars)
}

// buildPlanPayload constructs the plan payload JSON. If the turn already has
// a structured payload, it is returned as-is; otherwise one is synthesized
// from the turn's sql/row_count fields (legacy compatibility).
func buildPlanPayload(sqlText string, rowCount int, existing json.RawMessage) json.RawMessage {
	if existing != nil && len(existing) > 2 { // > 2 skips "{}" or "null"
		return existing
	}
	p, _ := json.Marshal(map[string]interface{}{
		"sql":       sqlText,
		"row_count": rowCount,
	})
	return p
}

// truncateMessages drops messages from the front (oldest) until the total
// content length fits within maxChars.
func truncateMessages(msgs []models.ChatMessage, maxChars int) []models.ChatMessage {
	total := 0
	for _, m := range msgs {
		total += len(m.Content)
	}
	if total <= maxChars {
		return msgs
	}
	for len(msgs) > 0 && total > maxChars {
		total -= len(msgs[0].Content)
		msgs = msgs[1:]
	}
	return msgs
}

