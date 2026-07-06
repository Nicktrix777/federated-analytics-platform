package services

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/federated-analytics/core-api/models"
)

// DataSourceService manages registered data source connections.
// It handles CRUD operations and schema refresh (fetching live schema
// from each source via Trino's information_schema and caching it).

type DataSourceService struct {
	db          *sql.DB
	trinoHost   string
	trinoPort   string
	aiEngineURL string
}

func NewDataSourceService(db *sql.DB, trinoHost, trinoPort, aiEngineURL string) *DataSourceService {
	return &DataSourceService{
		db:          db,
		trinoHost:   trinoHost,
		trinoPort:   trinoPort,
		aiEngineURL: aiEngineURL,
	}
}

// List returns all active data sources.
func (s *DataSourceService) List() ([]models.DataSource, error) {
	rows, err := s.db.Query(`
		SELECT id, name, source_type, host, port,
		       COALESCE(database_name, '') as database_name,
		       COALESCE(username, '') as username,
		       COALESCE(extra_config::text, '{}') as extra_config,
		       trino_catalog, is_active,
		       COALESCE(schema_cache::text, '{}') as schema_cache,
		       last_schema_refresh, created_at, updated_at
		FROM data_sources
		WHERE is_active = true
		ORDER BY source_type, name
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to query data_sources: %w", err)
	}
	defer rows.Close()

	var sources []models.DataSource
	for rows.Next() {
		var ds models.DataSource
		var lastRefresh sql.NullTime
		err := rows.Scan(
			&ds.ID, &ds.Name, &ds.SourceType, &ds.Host, &ds.Port,
			&ds.DatabaseName, &ds.Username, &ds.ExtraConfig,
			&ds.TrinoCatalog, &ds.IsActive, &ds.SchemaCache,
			&lastRefresh, &ds.CreatedAt, &ds.UpdatedAt,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan data_source: %w", err)
		}
		if lastRefresh.Valid {
			ds.LastSchemaRefresh = &lastRefresh.Time
		}
		sources = append(sources, ds)
	}
	return sources, nil
}

// GetByID returns a specific data source.
func (s *DataSourceService) GetByID(id int) (*models.DataSource, error) {
	var ds models.DataSource
	var lastRefresh sql.NullTime
	err := s.db.QueryRow(`
		SELECT id, name, source_type, host, port,
		       COALESCE(database_name, '') as database_name,
		       COALESCE(username, '') as username,
		       COALESCE(extra_config::text, '{}') as extra_config,
		       trino_catalog, is_active,
		       COALESCE(schema_cache::text, '{}') as schema_cache,
		       last_schema_refresh, created_at, updated_at
		FROM data_sources WHERE id = $1
	`, id).Scan(
		&ds.ID, &ds.Name, &ds.SourceType, &ds.Host, &ds.Port,
		&ds.DatabaseName, &ds.Username, &ds.ExtraConfig,
		&ds.TrinoCatalog, &ds.IsActive, &ds.SchemaCache,
		&lastRefresh, &ds.CreatedAt, &ds.UpdatedAt,
	)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get data_source: %w", err)
	}
	if lastRefresh.Valid {
		ds.LastSchemaRefresh = &lastRefresh.Time
	}
	return &ds, nil
}

// Create registers a new data source.
func (s *DataSourceService) Create(req models.CreateDataSourceRequest) (*models.DataSource, error) {
	extraConfig := req.ExtraConfig
	if extraConfig == "" {
		extraConfig = "{}"
	}

	var id int
	err := s.db.QueryRow(`
		INSERT INTO data_sources
		    (name, source_type, host, port, database_name, username, password_encrypted, 
		     trino_catalog, extra_config, is_active)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, true)
		RETURNING id
	`,
		req.Name, req.SourceType, req.Host, req.Port,
		req.DatabaseName, req.Username, req.Password,
		req.TrinoCatalog, extraConfig,
	).Scan(&id)
	if err != nil {
		return nil, fmt.Errorf("failed to create data_source: %w", err)
	}
	return s.GetByID(id)
}

// Update modifies an existing data source.
func (s *DataSourceService) Update(id int, req models.UpdateDataSourceRequest) (*models.DataSource, error) {
	setParts := []string{"updated_at = NOW()"}
	args := []interface{}{}
	argIdx := 1

	if req.Name != "" {
		setParts = append(setParts, fmt.Sprintf("name = $%d", argIdx))
		args = append(args, req.Name)
		argIdx++
	}
	if req.Host != "" {
		setParts = append(setParts, fmt.Sprintf("host = $%d", argIdx))
		args = append(args, req.Host)
		argIdx++
	}
	if req.Port != 0 {
		setParts = append(setParts, fmt.Sprintf("port = $%d", argIdx))
		args = append(args, req.Port)
		argIdx++
	}
	if req.DatabaseName != "" {
		setParts = append(setParts, fmt.Sprintf("database_name = $%d", argIdx))
		args = append(args, req.DatabaseName)
		argIdx++
	}
	if req.Username != "" {
		setParts = append(setParts, fmt.Sprintf("username = $%d", argIdx))
		args = append(args, req.Username)
		argIdx++
	}
	if req.Password != "" {
		setParts = append(setParts, fmt.Sprintf("password_encrypted = $%d", argIdx))
		args = append(args, req.Password)
		argIdx++
	}
	if req.IsActive != nil {
		setParts = append(setParts, fmt.Sprintf("is_active = $%d", argIdx))
		args = append(args, *req.IsActive)
		argIdx++
	}
	if req.ExtraConfig != "" {
		setParts = append(setParts, fmt.Sprintf("extra_config = $%d::jsonb", argIdx))
		args = append(args, req.ExtraConfig)
		argIdx++
	}

	args = append(args, id)
	_, err := s.db.Exec(
		fmt.Sprintf("UPDATE data_sources SET %s WHERE id = $%d",
			strings.Join(setParts, ", "), argIdx),
		args...,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to update data_source: %w", err)
	}
	return s.GetByID(id)
}

// Delete soft-deletes a data source.
func (s *DataSourceService) Delete(id int) error {
	_, err := s.db.Exec("UPDATE data_sources SET is_active = false WHERE id = $1", id)
	return err
}

// RefreshSchema fetches the live schema from Trino for a data source
// and caches it in postgres-meta. Also invalidates the AI Engine's schema cache.
func (s *DataSourceService) RefreshSchema(id int) (*models.SchemaRefreshResult, error) {
	ds, err := s.GetByID(id)
	if err != nil || ds == nil {
		return nil, fmt.Errorf("data source not found: %d", id)
	}

	// Fetch schema from Trino
	schema, tablesFound, err := s.fetchSchemaFromTrino(ds.TrinoCatalog)
	if err != nil {
		return nil, fmt.Errorf("schema fetch failed for %s: %w", ds.TrinoCatalog, err)
	}

	// Cache in postgres-meta
	schemaJSON, _ := json.Marshal(schema)
	now := time.Now()
	_, err = s.db.Exec(`
		UPDATE data_sources
		SET schema_cache = $1::jsonb, last_schema_refresh = $2
		WHERE id = $3
	`, string(schemaJSON), now, id)
	if err != nil {
		return nil, fmt.Errorf("failed to cache schema: %w", err)
	}

	// Invalidate AI Engine cache
	s.invalidateAICache()

	return &models.SchemaRefreshResult{
		DataSourceID: id,
		TrinoCatalog: ds.TrinoCatalog,
		TablesFound:  tablesFound,
		Message:      fmt.Sprintf("Schema refreshed: %d tables found in %s", tablesFound, ds.TrinoCatalog),
	}, nil
}

// fetchSchemaFromTrino queries Trino's information_schema for a catalog.
func (s *DataSourceService) fetchSchemaFromTrino(catalog string) (map[string]interface{}, int, error) {
	skipSchemas := []string{"information_schema", "pg_catalog", "pg_toast", "_schema"}
	skipList := make([]string, len(skipSchemas))
	for i, s := range skipSchemas {
		skipList[i] = fmt.Sprintf("'%s'", s)
	}

	sql := fmt.Sprintf(`
		SELECT table_schema, table_name, column_name, data_type
		FROM %s.information_schema.columns
		WHERE table_schema NOT IN (%s)
		  AND table_name NOT LIKE '\\_%%'
		ORDER BY table_schema, table_name, ordinal_position
	`, catalog, strings.Join(skipList, ", "))

	rows, err := s.runTrinoQuery(sql)
	if err != nil {
		return nil, 0, err
	}

	tables := map[string]map[string]interface{}{}
	for _, row := range rows {
		if len(row) < 4 {
			continue
		}
		schema := fmt.Sprintf("%v", row[0])
		table := fmt.Sprintf("%v", row[1])
		col := fmt.Sprintf("%v", row[2])
		dtype := fmt.Sprintf("%v", row[3])

		key := fmt.Sprintf("%s.%s.%s", catalog, schema, table)
		if _, ok := tables[key]; !ok {
			tables[key] = map[string]interface{}{
				"trino_path": key,
				"columns":    []map[string]string{},
			}
		}
		cols := tables[key]["columns"].([]map[string]string)
		tables[key]["columns"] = append(cols, map[string]string{
			"name": col,
			"type": strings.ToUpper(dtype),
		})
	}

	result := map[string]interface{}{
		"catalog":      catalog,
		"tables":       tables,
		"refreshed_at": time.Now().Format(time.RFC3339),
	}
	return result, len(tables), nil
}

// runTrinoQuery executes a SQL query against Trino's REST API.
func (s *DataSourceService) runTrinoQuery(query string) ([][]interface{}, error) {
	url := fmt.Sprintf("http://%s:%s/v1/statement", s.trinoHost, s.trinoPort)
	headers := map[string]string{
		"X-Trino-User":   "core-api",
		"X-Trino-Source": "fap-datasource-service",
		"Content-Type":   "application/json",
	}

	client := &http.Client{Timeout: 30 * time.Second}

	// Submit query
	req, _ := http.NewRequest("POST", url, bytes.NewBufferString(query))
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("trino request failed: %w", err)
	}
	defer resp.Body.Close()

	var allRows [][]interface{}
	maxPolls := 60
	polls := 0

	for polls < maxPolls {
		body, _ := io.ReadAll(resp.Body)
		var data map[string]interface{}
		if err := json.Unmarshal(body, &data); err != nil {
			return nil, fmt.Errorf("trino response parse failed: %w", err)
		}

		if rows, ok := data["data"].([]interface{}); ok {
			for _, row := range rows {
				if rowArr, ok := row.([]interface{}); ok {
					allRows = append(allRows, rowArr)
				}
			}
		}

		nextURI, _ := data["nextUri"].(string)
		if nextURI == "" {
			break
		}

		stats, _ := data["stats"].(map[string]interface{})
		state, _ := stats["state"].(string)
		if state == "FAILED" || state == "CANCELED" {
			errMap, _ := data["error"].(map[string]interface{})
			msg, _ := errMap["message"].(string)
			return nil, fmt.Errorf("trino query failed: %s", msg)
		}

		req2, _ := http.NewRequest("GET", nextURI, nil)
		for k, v := range headers {
			req2.Header.Set(k, v)
		}
		resp, err = client.Do(req2)
		if err != nil {
			return nil, fmt.Errorf("trino poll failed: %w", err)
		}
		defer resp.Body.Close()
		polls++
	}

	return allRows, nil
}

// invalidateAICache tells the AI Engine to drop its schema cache.
func (s *DataSourceService) invalidateAICache() {
	url := s.aiEngineURL + "/api/invalidate-cache"
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(url, "application/json", nil)
	if err != nil {
		return
	}
	defer resp.Body.Close()
}
