package services

import (
	"database/sql"
	"fmt"

	"github.com/federated-analytics/core-api/models"
)

// MetadataService reads dataset and column metadata from the postgres-meta database.
// This metadata is used to:
// 1. Build the AI Engine's system prompt (schema context)
// 2. Return dataset info to the frontend (for display/discovery)

type MetadataService struct {
	db *sql.DB
}

func NewMetadataService(db *sql.DB) *MetadataService {
	return &MetadataService{db: db}
}

// BumpMetadataVersion increments the single-row metadata_state version so the
// AI Engine's watcher notices metadata changed and re-runs enrichment within
// its poll interval. DB triggers on datasets/dataset_columns/table_relationships
// cover most writes automatically; this is the explicit bump for the one write
// path that isn't trigger-covered — RefreshSchema, which only touches
// data_sources.schema_cache (deliberately trigger-free, it churns every sync).
func BumpMetadataVersion(db *sql.DB) error {
	_, err := db.Exec(`UPDATE metadata_state SET version = version + 1, updated_at = NOW() WHERE id = TRUE`)
	if err != nil {
		return fmt.Errorf("failed to bump metadata version: %w", err)
	}
	return nil
}

// GetAllDatasets returns all active datasets with their column metadata.
func (s *MetadataService) GetAllDatasets() ([]models.DatasetMeta, error) {
	rows, err := s.db.Query(`
		SELECT id, name, description, source_type,
		       trino_catalog, trino_schema, trino_table
		FROM datasets
		WHERE is_active = true
		ORDER BY name
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to query datasets: %w", err)
	}
	defer rows.Close()

	var datasets []models.DatasetMeta
	for rows.Next() {
		var d models.DatasetMeta
		var catalog, schemaName, table string
		if err := rows.Scan(&d.ID, &d.Name, &d.Description, &d.SourceType, &catalog, &schemaName, &table); err != nil {
			return nil, fmt.Errorf("failed to scan dataset: %w", err)
		}
		// Quote each segment that needs it (e.g. Elasticsearch index names
		// like "contracts-v2.37") so the path is directly runnable in Trino.
		d.TrinoPath = buildTrinoPath(catalog, schemaName, table)
		datasets = append(datasets, d)
	}

	// Load columns for each dataset
	for i := range datasets {
		cols, err := s.getColumns(datasets[i].ID)
		if err != nil {
			return nil, err
		}
		datasets[i].Columns = cols
	}

	return datasets, nil
}

func (s *MetadataService) getColumns(datasetID int) ([]models.DatasetColumn, error) {
	rows, err := s.db.Query(`
		SELECT dc.column_name, dc.data_type, COALESCE(dc.description, ''),
		       dc.is_joinable, COALESCE(cp.sample_values, '')
		FROM dataset_columns dc
		LEFT JOIN column_profiles cp ON cp.dataset_column_id = dc.id
		WHERE dc.dataset_id = $1
		ORDER BY dc.id
	`, datasetID)
	if err != nil {
		return nil, fmt.Errorf("failed to query columns for dataset %d: %w", datasetID, err)
	}
	defer rows.Close()

	var cols []models.DatasetColumn
	for rows.Next() {
		var c models.DatasetColumn
		if err := rows.Scan(&c.ColumnName, &c.DataType, &c.Description, &c.IsJoinable, &c.SampleValues); err != nil {
			return nil, fmt.Errorf("failed to scan column: %w", err)
		}
		cols = append(cols, c)
	}
	return cols, nil
}
