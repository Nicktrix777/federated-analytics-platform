package services

import (
	"database/sql"
	"fmt"

	"github.com/federated-analytics/core-api/models"
)

// DashboardService manages dashboards and widgets in postgres-meta.

type DashboardService struct {
	db *sql.DB
}

func NewDashboardService(db *sql.DB) *DashboardService {
	return &DashboardService{db: db}
}

// ListDashboards returns all active dashboards (without widgets).
func (s *DashboardService) ListDashboards() ([]models.Dashboard, error) {
	rows, err := s.db.Query(`
		SELECT id, name, COALESCE(description, ''), COALESCE(layout::text, '{}'),
		       is_active, created_at, updated_at
		FROM dashboards
		WHERE is_active = true
		ORDER BY updated_at DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to list dashboards: %w", err)
	}
	defer rows.Close()

	var dashboards []models.Dashboard
	for rows.Next() {
		var d models.Dashboard
		if err := rows.Scan(
			&d.ID, &d.Name, &d.Description, &d.Layout,
			&d.IsActive, &d.CreatedAt, &d.UpdatedAt,
		); err != nil {
			return nil, err
		}
		dashboards = append(dashboards, d)
	}
	return dashboards, nil
}

// GetDashboard returns a dashboard with all its widgets.
func (s *DashboardService) GetDashboard(id int) (*models.Dashboard, error) {
	var d models.Dashboard
	err := s.db.QueryRow(`
		SELECT id, name, COALESCE(description, ''), COALESCE(layout::text, '{}'),
		       is_active, created_at, updated_at
		FROM dashboards WHERE id = $1
	`, id).Scan(&d.ID, &d.Name, &d.Description, &d.Layout, &d.IsActive, &d.CreatedAt, &d.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	widgets, err := s.GetWidgets(id)
	if err != nil {
		return nil, err
	}
	d.Widgets = widgets
	return &d, nil
}

// CreateDashboard inserts a new dashboard.
func (s *DashboardService) CreateDashboard(req models.CreateDashboardRequest) (*models.Dashboard, error) {
	layout := req.Layout
	if layout == "" {
		layout = "{}"
	}

	var id int
	err := s.db.QueryRow(`
		INSERT INTO dashboards (name, description, layout)
		VALUES ($1, $2, $3::jsonb)
		RETURNING id
	`, req.Name, req.Description, layout).Scan(&id)
	if err != nil {
		return nil, fmt.Errorf("failed to create dashboard: %w", err)
	}
	return s.GetDashboard(id)
}

// UpdateDashboard modifies a dashboard.
func (s *DashboardService) UpdateDashboard(id int, req models.UpdateDashboardRequest) (*models.Dashboard, error) {
	_, err := s.db.Exec(`
		UPDATE dashboards
		SET name = COALESCE(NULLIF($1, ''), name),
		    description = COALESCE(NULLIF($2, ''), description),
		    layout = CASE WHEN $3 = '' THEN layout ELSE $3::jsonb END,
		    updated_at = NOW()
		WHERE id = $4
	`, req.Name, req.Description, req.Layout, id)
	if err != nil {
		return nil, fmt.Errorf("failed to update dashboard: %w", err)
	}
	return s.GetDashboard(id)
}

// DeleteDashboard soft-deletes a dashboard (and cascades to widgets via DB constraint).
func (s *DashboardService) DeleteDashboard(id int) error {
	_, err := s.db.Exec("UPDATE dashboards SET is_active = false WHERE id = $1", id)
	return err
}

// GetWidgets returns all widgets for a dashboard.
func (s *DashboardService) GetWidgets(dashboardID int) ([]models.DashboardWidget, error) {
	rows, err := s.db.Query(`
		SELECT id, dashboard_id, title, query_sql, chart_type,
		       COALESCE(chart_config::text, '{}'),
		       COALESCE(grid_position::text, '{"x":0,"y":0,"w":6,"h":4}'),
		       refresh_rate_ms, created_at, updated_at
		FROM dashboard_widgets
		WHERE dashboard_id = $1
		ORDER BY id
	`, dashboardID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var widgets []models.DashboardWidget
	for rows.Next() {
		var w models.DashboardWidget
		if err := rows.Scan(
			&w.ID, &w.DashboardID, &w.Title, &w.QuerySQL, &w.ChartType,
			&w.ChartConfig, &w.GridPosition, &w.RefreshRateMs,
			&w.CreatedAt, &w.UpdatedAt,
		); err != nil {
			return nil, err
		}
		widgets = append(widgets, w)
	}
	return widgets, nil
}

// CreateWidget adds a widget to a dashboard.
func (s *DashboardService) CreateWidget(dashboardID int, req models.CreateWidgetRequest) (*models.DashboardWidget, error) {
	chartType := req.ChartType
	if chartType == "" {
		chartType = "table"
	}
	chartConfig := req.ChartConfig
	if chartConfig == "" {
		chartConfig = "{}"
	}
	gridPos := req.GridPosition
	if gridPos == "" {
		gridPos = `{"x":0,"y":0,"w":6,"h":4}`
	}

	var id int
	err := s.db.QueryRow(`
		INSERT INTO dashboard_widgets
		    (dashboard_id, title, query_sql, chart_type, chart_config, grid_position, refresh_rate_ms)
		VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
		RETURNING id
	`,
		dashboardID, req.Title, req.QuerySQL, chartType,
		chartConfig, gridPos, req.RefreshRateMs,
	).Scan(&id)
	if err != nil {
		return nil, fmt.Errorf("failed to create widget: %w", err)
	}

	var w models.DashboardWidget
	err = s.db.QueryRow(`
		SELECT id, dashboard_id, title, query_sql, chart_type,
		       COALESCE(chart_config::text, '{}'),
		       COALESCE(grid_position::text, '{}'),
		       refresh_rate_ms, created_at, updated_at
		FROM dashboard_widgets WHERE id = $1
	`, id).Scan(
		&w.ID, &w.DashboardID, &w.Title, &w.QuerySQL, &w.ChartType,
		&w.ChartConfig, &w.GridPosition, &w.RefreshRateMs,
		&w.CreatedAt, &w.UpdatedAt,
	)
	if err != nil {
		return nil, err
	}
	return &w, nil
}

// UpdateWidget modifies a widget.
func (s *DashboardService) UpdateWidget(dashboardID, widgetID int, req models.UpdateWidgetRequest) (*models.DashboardWidget, error) {
	_, err := s.db.Exec(`
		UPDATE dashboard_widgets
		SET title       = CASE WHEN $1 = '' THEN title ELSE $1 END,
		    query_sql   = CASE WHEN $2 = '' THEN query_sql ELSE $2 END,
		    chart_type  = CASE WHEN $3 = '' THEN chart_type ELSE $3 END,
		    chart_config = CASE WHEN $4 = '' THEN chart_config ELSE $4::jsonb END,
		    grid_position = CASE WHEN $5 = '' THEN grid_position ELSE $5::jsonb END,
		    updated_at  = NOW()
		WHERE id = $6 AND dashboard_id = $7
	`,
		req.Title, req.QuerySQL, req.ChartType,
		req.ChartConfig, req.GridPosition,
		widgetID, dashboardID,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to update widget: %w", err)
	}

	var w models.DashboardWidget
	err = s.db.QueryRow(`
		SELECT id, dashboard_id, title, query_sql, chart_type,
		       COALESCE(chart_config::text, '{}'),
		       COALESCE(grid_position::text, '{}'),
		       refresh_rate_ms, created_at, updated_at
		FROM dashboard_widgets WHERE id = $1
	`, widgetID).Scan(
		&w.ID, &w.DashboardID, &w.Title, &w.QuerySQL, &w.ChartType,
		&w.ChartConfig, &w.GridPosition, &w.RefreshRateMs,
		&w.CreatedAt, &w.UpdatedAt,
	)
	return &w, err
}

// ReplaceWidgets atomically swaps a dashboard's widgets for a new set.
// Used by AI refinement, where the designer returns the full updated dashboard.
func (s *DashboardService) ReplaceWidgets(dashboardID int, widgets []models.CreateWidgetRequest) error {
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.Exec("DELETE FROM dashboard_widgets WHERE dashboard_id = $1", dashboardID); err != nil {
		return fmt.Errorf("failed to clear widgets: %w", err)
	}

	for _, req := range widgets {
		chartType := req.ChartType
		if chartType == "" {
			chartType = "table"
		}
		chartConfig := req.ChartConfig
		if chartConfig == "" {
			chartConfig = "{}"
		}
		gridPos := req.GridPosition
		if gridPos == "" {
			gridPos = `{"x":0,"y":0,"w":6,"h":4}`
		}
		if _, err := tx.Exec(`
			INSERT INTO dashboard_widgets
			    (dashboard_id, title, query_sql, chart_type, chart_config, grid_position, refresh_rate_ms)
			VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
		`, dashboardID, req.Title, req.QuerySQL, chartType, chartConfig, gridPos, req.RefreshRateMs); err != nil {
			return fmt.Errorf("failed to insert widget %q: %w", req.Title, err)
		}
	}

	return tx.Commit()
}

// DeleteWidget removes a widget.
func (s *DashboardService) DeleteWidget(dashboardID, widgetID int) error {
	_, err := s.db.Exec(
		"DELETE FROM dashboard_widgets WHERE id = $1 AND dashboard_id = $2",
		widgetID, dashboardID,
	)
	return err
}
