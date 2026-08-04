package services

import (
	"database/sql"
	"fmt"

	"github.com/federated-analytics/core-api/models"
)

// ReportService manages reports and their sheets in postgres-meta.
//
// Reports mirror dashboards: a persisted definition (name + sheet queries)
// whose data is never stored — downloading a report executes every sheet's
// SQL live and streams a formatted workbook.

type ReportService struct {
	db *sql.DB
}

func NewReportService(db *sql.DB) *ReportService {
	return &ReportService{db: db}
}

// ListReports returns all active reports (without sheets).
func (s *ReportService) ListReports() ([]models.Report, error) {
	rows, err := s.db.Query(`
		SELECT id, name, COALESCE(description, ''), is_active, created_at, updated_at
		FROM reports
		WHERE is_active = true
		ORDER BY updated_at DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to list reports: %w", err)
	}
	defer rows.Close()

	var reports []models.Report
	for rows.Next() {
		var r models.Report
		if err := rows.Scan(
			&r.ID, &r.Name, &r.Description, &r.IsActive, &r.CreatedAt, &r.UpdatedAt,
		); err != nil {
			return nil, err
		}
		reports = append(reports, r)
	}
	return reports, nil
}

// GetReport returns a report with all its sheets.
func (s *ReportService) GetReport(id int) (*models.Report, error) {
	var r models.Report
	err := s.db.QueryRow(`
		SELECT id, name, COALESCE(description, ''), is_active, created_at, updated_at
		FROM reports WHERE id = $1
	`, id).Scan(&r.ID, &r.Name, &r.Description, &r.IsActive, &r.CreatedAt, &r.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	sheets, err := s.GetSheets(id)
	if err != nil {
		return nil, err
	}
	r.Sheets = sheets
	return &r, nil
}

// CreateReport inserts a new report.
func (s *ReportService) CreateReport(req models.CreateReportRequest) (*models.Report, error) {
	var id int
	err := s.db.QueryRow(`
		INSERT INTO reports (name, description)
		VALUES ($1, $2)
		RETURNING id
	`, req.Name, req.Description).Scan(&id)
	if err != nil {
		return nil, fmt.Errorf("failed to create report: %w", err)
	}
	return s.GetReport(id)
}

// UpdateReport modifies a report.
func (s *ReportService) UpdateReport(id int, req models.UpdateReportRequest) (*models.Report, error) {
	_, err := s.db.Exec(`
		UPDATE reports
		SET name = COALESCE(NULLIF($1, ''), name),
		    description = COALESCE(NULLIF($2, ''), description),
		    updated_at = NOW()
		WHERE id = $3
	`, req.Name, req.Description, id)
	if err != nil {
		return nil, fmt.Errorf("failed to update report: %w", err)
	}
	return s.GetReport(id)
}

// DeleteReport soft-deletes a report (sheets stay behind the FK for a hard purge).
func (s *ReportService) DeleteReport(id int) error {
	_, err := s.db.Exec("UPDATE reports SET is_active = false WHERE id = $1", id)
	return err
}

// GetSheets returns all sheets for a report in workbook order.
func (s *ReportService) GetSheets(reportID int) ([]models.ReportSheet, error) {
	rows, err := s.db.Query(`
		SELECT id, report_id, title, COALESCE(description, ''), query_sql,
		       COALESCE(column_formats::text, '{}'),
		       position, max_rows, created_at, updated_at
		FROM report_sheets
		WHERE report_id = $1
		ORDER BY position, id
	`, reportID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sheets []models.ReportSheet
	for rows.Next() {
		var sh models.ReportSheet
		if err := rows.Scan(
			&sh.ID, &sh.ReportID, &sh.Title, &sh.Description, &sh.QuerySQL,
			&sh.ColumnFormats, &sh.Position, &sh.MaxRows,
			&sh.CreatedAt, &sh.UpdatedAt,
		); err != nil {
			return nil, err
		}
		sheets = append(sheets, sh)
	}
	return sheets, nil
}

// CreateSheet adds a sheet to a report.
func (s *ReportService) CreateSheet(reportID int, req models.CreateSheetRequest) (*models.ReportSheet, error) {
	columnFormats := req.ColumnFormats
	if columnFormats == "" {
		columnFormats = "{}"
	}
	maxRows := req.MaxRows
	if maxRows <= 0 {
		maxRows = 5000
	}

	var id int
	err := s.db.QueryRow(`
		INSERT INTO report_sheets
		    (report_id, title, description, query_sql, column_formats, position, max_rows)
		VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
		RETURNING id
	`,
		reportID, req.Title, req.Description, req.QuerySQL,
		columnFormats, req.Position, maxRows,
	).Scan(&id)
	if err != nil {
		return nil, fmt.Errorf("failed to create sheet: %w", err)
	}
	return s.getSheet(id)
}

// UpdateSheet modifies a sheet.
func (s *ReportService) UpdateSheet(reportID, sheetID int, req models.UpdateSheetRequest) (*models.ReportSheet, error) {
	_, err := s.db.Exec(`
		UPDATE report_sheets
		SET title          = CASE WHEN $1 = '' THEN title ELSE $1 END,
		    query_sql      = CASE WHEN $2 = '' THEN query_sql ELSE $2 END,
		    description    = CASE WHEN $3 = '' THEN description ELSE $3 END,
		    column_formats = CASE WHEN $4 = '' THEN column_formats ELSE $4::jsonb END,
		    position       = COALESCE($5, position),
		    max_rows       = COALESCE($6, max_rows),
		    updated_at     = NOW()
		WHERE id = $7 AND report_id = $8
	`,
		req.Title, req.QuerySQL, req.Description, req.ColumnFormats,
		req.Position, req.MaxRows,
		sheetID, reportID,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to update sheet: %w", err)
	}
	return s.getSheet(sheetID)
}

// getSheet loads a single sheet by id.
func (s *ReportService) getSheet(id int) (*models.ReportSheet, error) {
	var sh models.ReportSheet
	err := s.db.QueryRow(`
		SELECT id, report_id, title, COALESCE(description, ''), query_sql,
		       COALESCE(column_formats::text, '{}'),
		       position, max_rows, created_at, updated_at
		FROM report_sheets WHERE id = $1
	`, id).Scan(
		&sh.ID, &sh.ReportID, &sh.Title, &sh.Description, &sh.QuerySQL,
		&sh.ColumnFormats, &sh.Position, &sh.MaxRows,
		&sh.CreatedAt, &sh.UpdatedAt,
	)
	if err != nil {
		return nil, err
	}
	return &sh, nil
}

// ReplaceSheets atomically swaps a report's sheets for a new set.
// Used by AI refinement, where the designer returns the full updated report.
func (s *ReportService) ReplaceSheets(reportID int, sheets []models.CreateSheetRequest) error {
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.Exec("DELETE FROM report_sheets WHERE report_id = $1", reportID); err != nil {
		return fmt.Errorf("failed to clear sheets: %w", err)
	}

	for _, req := range sheets {
		columnFormats := req.ColumnFormats
		if columnFormats == "" {
			columnFormats = "{}"
		}
		maxRows := req.MaxRows
		if maxRows <= 0 {
			maxRows = 5000
		}
		if _, err := tx.Exec(`
			INSERT INTO report_sheets
			    (report_id, title, description, query_sql, column_formats, position, max_rows)
			VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
		`, reportID, req.Title, req.Description, req.QuerySQL, columnFormats, req.Position, maxRows); err != nil {
			return fmt.Errorf("failed to insert sheet %q: %w", req.Title, err)
		}
	}

	return tx.Commit()
}

// DeleteSheet removes a sheet.
func (s *ReportService) DeleteSheet(reportID, sheetID int) error {
	_, err := s.db.Exec(
		"DELETE FROM report_sheets WHERE id = $1 AND report_id = $2",
		sheetID, reportID,
	)
	return err
}
