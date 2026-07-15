package services

import (
	"database/sql"
	"fmt"

	"github.com/google/uuid"
)

// ConversationService persists NL-query conversations in postgres-meta so
// follow-up questions can be answered with the prior turns as context.
//
// The conversation id is client-supplied (the frontend mints one UUID per chat
// session). EnsureConversation upserts it; RecentTurns / AppendTurn read and
// grow the transcript. All methods are best-effort at the call site — a failure
// here must never fail the query itself, only drop the multi-turn context.

type ConversationService struct {
	db *sql.DB
}

func NewConversationService(db *sql.DB) *ConversationService {
	return &ConversationService{db: db}
}

// ConversationTurn is one completed NL→SQL exchange.
type ConversationTurn struct {
	Question string
	SQL      string
	RowCount int
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
		SELECT question, COALESCE(sql, ''), COALESCE(row_count, 0)
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
		if err := rows.Scan(&t.Question, &t.SQL, &t.RowCount); err != nil {
			return nil, err
		}
		turns = append(turns, t)
	}
	// Reverse into chronological order (oldest first).
	for i, j := 0, len(turns)-1; i < j; i, j = i+1, j-1 {
		turns[i], turns[j] = turns[j], turns[i]
	}
	return turns, nil
}

// AppendTurn records a completed exchange. row_count is stored NULL-safe.
func (s *ConversationService) AppendTurn(id, question, sqlText string, rowCount int) error {
	if !ValidConversationID(id) {
		return fmt.Errorf("invalid conversation id")
	}
	_, err := s.db.Exec(`
		INSERT INTO conversation_turns (conversation_id, question, sql, row_count)
		VALUES ($1, $2, $3, $4)
	`, id, question, sqlText, rowCount)
	if err != nil {
		return err
	}
	// Keep the parent's activity timestamp fresh for future listing/cleanup.
	_, _ = s.db.Exec(`UPDATE conversations SET last_active_at = now() WHERE id = $1`, id)
	return nil
}
