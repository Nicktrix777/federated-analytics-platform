package services

import (
	"database/sql"
	"log"
	"time"

	"github.com/federated-analytics/core-api/models"
)

// CurationService manages the needs_curation queue — a best-effort log of
// AI queries that the platform could not auto-resolve, surfaced to operators
// for manual review.
//
// All writes are best-effort: a failure here must never fail the query itself.
// The table is insert-only for the application; operators resolve items by
// flipping status via SQL (v1: no resolve endpoint).
//
// Security note: all queries use parameterized statements; status is validated
// against an allowlist before use.
// TODO(security): access is gated by the same static bearer token as all other
// endpoints — tighten when real auth lands.

type CurationService struct {
	db *sql.DB
}

func NewCurationService(db *sql.DB) *CurationService {
	return &CurationService{db: db}
}

// allowedStatuses is the allowlist for the status filter parameter.
var allowedStatuses = map[string]bool{
	"new":      true,
	"resolved": true,
}

// Add inserts a new curation item. Errors are only logged; the caller must
// treat this as best-effort and never surface failures to the end user.
//
// kind: one of "repair_exhausted" | "zero_rows_unresolved"
// question: the user's original NL question
// detail: first line of the engine error or a short description
// requestID / conversationID: correlation ids (may be empty strings)
func (s *CurationService) Add(kind, question, detail, requestID, conversationID string) {
	_, err := s.db.Exec(`
		INSERT INTO needs_curation (kind, question, detail, request_id, conversation_id)
		VALUES ($1, $2, $3, NULLIF($4, '')::UUID, NULLIF($5, '')::UUID)
	`, kind, question, detail, requestID, conversationID)
	if err != nil {
		log.Printf("curation: failed to add item (kind=%s): %v", kind, err)
	}
}

// List returns at most limit curation items filtered by status. status must
// be one of "new" or "resolved"; an invalid value returns an empty list.
func (s *CurationService) List(status string, limit int) ([]models.CurationItem, error) {
	// Validate status against allowlist — never interpolate user input into SQL.
	if !allowedStatuses[status] {
		return nil, nil
	}
	if limit <= 0 || limit > 500 {
		limit = 50
	}

	rows, err := s.db.Query(`
		SELECT id, kind, dataset_id, COALESCE(column_name,''), COALESCE(question,''),
		       COALESCE(detail,''), status,
		       COALESCE(request_id::TEXT,''), COALESCE(conversation_id::TEXT,''),
		       created_at
		FROM needs_curation
		WHERE status = $1
		ORDER BY created_at DESC
		LIMIT $2
	`, status, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var items []models.CurationItem
	for rows.Next() {
		var item models.CurationItem
		var datasetID sql.NullInt64
		var createdAt time.Time
		if err := rows.Scan(
			&item.ID, &item.Kind, &datasetID, &item.ColumnName, &item.Question,
			&item.Detail, &item.Status, &item.RequestID, &item.ConversationID,
			&createdAt,
		); err != nil {
			return nil, err
		}
		if datasetID.Valid {
			id := int(datasetID.Int64)
			item.DatasetID = &id
		}
		item.CreatedAt = createdAt
		items = append(items, item)
	}
	return items, nil
}
