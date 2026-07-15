package services

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/federated-analytics/core-api/models"
)

// trinoSystemCatalogs are Trino's built-in catalogs, not real registered
// data sources — SyncCatalogsFromTrino skips these.
var trinoSystemCatalogs = map[string]bool{
	"system": true, "tpch": true, "tpcds": true, "jmx": true, "memory": true,
}

// allowedDataSourceTypes mirrors the data_sources.source_type CHECK constraint.
var allowedDataSourceTypes = map[string]bool{
	"postgresql": true, "mongodb": true, "elasticsearch": true, "mysql": true, "trino": true,
}

// DataSourceService manages registered data source connections.
// It handles CRUD operations and schema refresh (fetching live schema
// from each source via Trino's information_schema and caching it).

type DataSourceService struct {
	db        *sql.DB
	trinoHost string
	trinoPort string
	aiClient  *AIClient
}

func NewDataSourceService(db *sql.DB, trinoHost, trinoPort string, aiClient *AIClient) *DataSourceService {
	return &DataSourceService{
		db:        db,
		trinoHost: trinoHost,
		trinoPort: trinoPort,
		aiClient:  aiClient,
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
	s.aiClient.InvalidateCache()

	return &models.SchemaRefreshResult{
		DataSourceID: id,
		TrinoCatalog: ds.TrinoCatalog,
		TablesFound:  tablesFound,
		Message:      fmt.Sprintf("Schema refreshed: %d tables found in %s", tablesFound, ds.TrinoCatalog),
	}, nil
}

// SyncCatalogsFromTrino discovers every catalog Trino actually has configured
// (postgres_source, mongodb, elasticsearch, and any future addition to
// trino/catalog/*.properties) and registers any that aren't yet in
// data_sources, then registers any table/index within each source that isn't
// yet in the curated `datasets` table. This is what lets a newly-added
// Elasticsearch index show up to the AI without a manual SQL insert.
func (s *DataSourceService) SyncCatalogsFromTrino() (*models.SyncCatalogsResult, error) {
	result := &models.SyncCatalogsResult{}

	catalogs, err := s.discoverTrinoCatalogs()
	if err != nil {
		return nil, fmt.Errorf("failed to discover trino catalogs: %w", err)
	}

	existing, err := s.List()
	if err != nil {
		return nil, fmt.Errorf("failed to list existing data sources: %w", err)
	}
	known := map[string]bool{}
	for _, ds := range existing {
		known[ds.TrinoCatalog] = true
	}

	trinoPort, _ := strconv.Atoi(s.trinoPort)

	for catalog, connector := range catalogs {
		if trinoSystemCatalogs[catalog] || known[catalog] {
			continue
		}
		sourceType := connector
		if !allowedDataSourceTypes[sourceType] {
			sourceType = "trino"
		}
		name := strings.ToUpper(catalog[:1]) + catalog[1:]

		res, err := s.db.Exec(`
			INSERT INTO data_sources
			    (name, source_type, host, port, trino_catalog, extra_config, is_active)
			VALUES ($1, $2, $3, $4, $5, $6::jsonb, true)
			ON CONFLICT (trino_catalog) DO NOTHING
		`, name, sourceType, s.trinoHost, trinoPort, catalog, `{"auto_discovered": true}`)
		if err != nil {
			log.Printf("catalog sync: failed to register catalog %s: %v", catalog, err)
			continue
		}
		if n, _ := res.RowsAffected(); n > 0 {
			result.NewSources = append(result.NewSources, catalog)
		}
	}

	// Re-list to include any sources inserted above.
	sources, err := s.List()
	if err != nil {
		return nil, fmt.Errorf("failed to list data sources: %w", err)
	}

	columnsRefreshed := 0
	for _, ds := range sources {
		schema, _, err := s.fetchSchemaFromTrino(ds.TrinoCatalog)
		if err != nil {
			log.Printf("catalog sync: schema fetch failed for %s: %v", ds.TrinoCatalog, err)
			continue
		}

		schemaJSON, _ := json.Marshal(schema)
		if _, err := s.db.Exec(`
			UPDATE data_sources SET schema_cache = $1::jsonb, last_schema_refresh = $2 WHERE id = $3
		`, string(schemaJSON), time.Now(), ds.ID); err != nil {
			log.Printf("catalog sync: failed to cache schema for %s: %v", ds.TrinoCatalog, err)
		}

		newDatasets, changed, err := s.syncDatasetsForCatalog(ds.TrinoCatalog, ds.SourceType, schema)
		if err != nil {
			log.Printf("catalog sync: dataset sync failed for %s: %v", ds.TrinoCatalog, err)
			continue
		}
		result.NewDatasets = append(result.NewDatasets, newDatasets...)
		columnsRefreshed += changed
	}

	// Derive cross-table join hints from the freshly-synced schema. This is
	// what replaced the AI's old hardcoded relationship map — the join map is
	// now discovered from live columns, so a newly-connected source gets join
	// hints without any manual SQL or code change.
	inferred, err := s.InferRelationships()
	if err != nil {
		log.Printf("catalog sync: relationship inference failed: %v", err)
	}
	result.InferredRelationships = inferred

	if len(result.NewSources) > 0 || len(result.NewDatasets) > 0 || inferred > 0 || columnsRefreshed > 0 {
		s.aiClient.InvalidateCache()
	}

	result.Message = fmt.Sprintf(
		"Sync complete: %d new source(s), %d new dataset(s), %d column(s) refreshed, %d inferred relationship(s)",
		len(result.NewSources), len(result.NewDatasets), columnsRefreshed, inferred,
	)
	return result, nil
}

// inferColumn is a column as seen by relationship inference.
type inferColumn struct {
	name  string
	dtype string
}

// inferTable is one registered dataset with its columns, for inference.
type inferTable struct {
	trinoPath string // directly-runnable (quoted) path
	catalog   string
	table     string // raw table/index name (lowercased for matching)
	columns   []inferColumn
}

// InferRelationships derives cross-table join hints from the registered
// datasets/columns and stores any NEW ones in table_relationships. It is:
//   - non-destructive: never deletes or overwrites existing rows, so
//     hand-curated relationships (added via the UI/SQL) always win;
//   - idempotent: an existing (from,from_col,to,to_col) tuple is skipped;
//   - high-precision: only two well-understood foreign-key patterns are
//     proposed, so the AI's join map stays trustworthy rather than noisy.
//
// Patterns:
//  1. FK → PK (same or cross source): a column `<x>_id` references a table
//     whose name matches `<x>` (singular/plural) on its `id` (or `<x>_id`) key.
//  2. Shared FK across sources: the same `<x>_id` column present in two tables
//     in DIFFERENT catalogs is a strong federation-join signal (e.g. a Postgres
//     table and a Mongo collection that both carry employee_id).
//
// When the two sides' declared types differ, a CAST cast_expression is stored
// so the AI reconciles the mismatch instead of guessing.
func (s *DataSourceService) InferRelationships() (int, error) {
	tables, err := s.loadInferTables()
	if err != nil {
		return 0, err
	}

	// Index tables by candidate base names so `<x>_id` can find table `<x>`.
	byName := map[string]*inferTable{}
	for i := range tables {
		t := &tables[i]
		tn := t.table
		byName[tn] = t
		if strings.HasSuffix(tn, "es") {
			byName[strings.TrimSuffix(tn, "es")] = t
		}
		if strings.HasSuffix(tn, "s") {
			byName[strings.TrimSuffix(tn, "s")] = t
		}
	}

	inserted := 0
	for i := range tables {
		from := &tables[i]
		for _, col := range from.columns {
			if !strings.HasSuffix(col.name, "_id") || len(col.name) <= 3 {
				continue
			}
			entity := strings.TrimSuffix(col.name, "_id")

			// Pattern 1: FK → PK on the referenced entity's table.
			if target, ok := byName[entity]; ok && target.trinoPath != from.trinoPath {
				if toCol, ok := pickKeyColumn(target, col.name); ok {
					if s.insertRelationship(from, col, target, toCol, "one "+entity+" per row (inferred FK)") {
						inserted++
					}
				}
			}

			// Pattern 2: same FK column shared across DIFFERENT sources.
			for j := range tables {
				other := &tables[j]
				if other.catalog == from.catalog || other.trinoPath == from.trinoPath {
					continue
				}
				// Emit one direction only (lexical order) to avoid duplicate mirrors.
				if from.trinoPath >= other.trinoPath {
					continue
				}
				if oc, ok := findColumn(other, col.name); ok {
					if s.insertRelationship(from, col, other, oc, "shared "+col.name+" across sources (inferred)") {
						inserted++
					}
				}
			}
		}
	}

	return inserted, nil
}

// loadInferTables loads active datasets and their columns in one query.
func (s *DataSourceService) loadInferTables() ([]inferTable, error) {
	rows, err := s.db.Query(`
		SELECT d.id, d.trino_catalog, d.trino_schema, d.trino_table,
		       dc.column_name, dc.data_type
		FROM datasets d
		LEFT JOIN dataset_columns dc ON dc.dataset_id = d.id
		WHERE d.is_active = true
		ORDER BY d.id
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to load datasets for inference: %w", err)
	}
	defer rows.Close()

	order := []int{}
	byID := map[int]*inferTable{}
	for rows.Next() {
		var id int
		var catalog, schema, table string
		var colName, colType sql.NullString
		if err := rows.Scan(&id, &catalog, &schema, &table, &colName, &colType); err != nil {
			return nil, fmt.Errorf("failed to scan inference row: %w", err)
		}
		t, ok := byID[id]
		if !ok {
			t = &inferTable{
				trinoPath: buildTrinoPath(catalog, schema, table),
				catalog:   catalog,
				table:     strings.ToLower(table),
			}
			byID[id] = t
			order = append(order, id)
		}
		if colName.Valid {
			t.columns = append(t.columns, inferColumn{name: colName.String, dtype: colType.String})
		}
	}

	out := make([]inferTable, 0, len(order))
	for _, id := range order {
		out = append(out, *byID[id])
	}
	return out, nil
}

// pickKeyColumn returns the primary-key-ish column of target to join onto:
// prefer `id`, then the FK's own name (e.g. employees keyed on employee_id).
func pickKeyColumn(target *inferTable, fkName string) (inferColumn, bool) {
	if c, ok := findColumn(target, "id"); ok {
		return c, true
	}
	if c, ok := findColumn(target, fkName); ok {
		return c, true
	}
	return inferColumn{}, false
}

func findColumn(t *inferTable, name string) (inferColumn, bool) {
	for _, c := range t.columns {
		if c.name == name {
			return c, true
		}
	}
	return inferColumn{}, false
}

// baseType strips type parameters/precision, e.g. "DECIMAL(10,2)" -> "DECIMAL".
func baseType(t string) string {
	t = strings.ToUpper(strings.TrimSpace(t))
	if i := strings.IndexByte(t, '('); i >= 0 {
		t = t[:i]
	}
	return t
}

// insertRelationship stores one inferred join hint if an identical tuple isn't
// already present. Returns true when a row was inserted.
func (s *DataSourceService) insertRelationship(
	from *inferTable, fromCol inferColumn,
	to *inferTable, toCol inferColumn,
	description string,
) bool {
	var exists bool
	if err := s.db.QueryRow(`
		SELECT EXISTS(
			SELECT 1 FROM table_relationships
			WHERE from_trino_path = $1 AND from_column = $2
			  AND to_trino_path = $3 AND to_column = $4
		)
	`, from.trinoPath, fromCol.name, to.trinoPath, toCol.name).Scan(&exists); err != nil {
		log.Printf("relationship inference: existence check failed: %v", err)
		return false
	}
	if exists {
		return false
	}

	// If declared types differ, cast the FROM side to the TO side's base type.
	cast := ""
	if baseType(fromCol.dtype) != baseType(toCol.dtype) && baseType(toCol.dtype) != "" {
		cast = fmt.Sprintf("CAST(%s AS %s)", fromCol.name, baseType(toCol.dtype))
	}

	if _, err := s.db.Exec(`
		INSERT INTO table_relationships
		    (from_trino_path, from_column, to_trino_path, to_column, join_type, cast_expression, description)
		VALUES ($1, $2, $3, $4, 'INNER', $5, $6)
	`, from.trinoPath, fromCol.name, to.trinoPath, toCol.name, cast, description); err != nil {
		log.Printf("relationship inference: insert failed for %s.%s -> %s.%s: %v",
			from.trinoPath, fromCol.name, to.trinoPath, toCol.name, err)
		return false
	}
	return true
}

// discoverTrinoCatalogs returns catalog_name -> connector_name for every
// catalog Trino is currently configured with. Falls back to SHOW CATALOGS
// (no connector info) on Trino versions without system.metadata.catalogs.
func (s *DataSourceService) discoverTrinoCatalogs() (map[string]string, error) {
	rows, err := s.runTrinoQuery("SELECT catalog_name, connector_name FROM system.metadata.catalogs")
	if err != nil {
		rows, err = s.runTrinoQuery("SHOW CATALOGS")
		if err != nil {
			return nil, err
		}
		catalogs := map[string]string{}
		for _, row := range rows {
			if len(row) < 1 {
				continue
			}
			catalogs[fmt.Sprintf("%v", row[0])] = ""
		}
		return catalogs, nil
	}

	catalogs := map[string]string{}
	for _, row := range rows {
		if len(row) < 2 {
			continue
		}
		catalogs[fmt.Sprintf("%v", row[0])] = fmt.Sprintf("%v", row[1])
	}
	return catalogs, nil
}

// syncDatasetsForCatalog registers any table/index discovered via Trino that
// isn't yet represented in the curated `datasets` table, with a minimal
// auto-generated description and its columns — so it appears in the AI's
// pre-loaded schema context next to hand-curated datasets instead of being
// invisible.
func (s *DataSourceService) syncDatasetsForCatalog(
	catalog, sourceType string, schema map[string]interface{},
) ([]string, int, error) {
	tables, ok := schema["tables"].(map[string]map[string]interface{})
	if !ok {
		return nil, 0, nil
	}

	var added []string
	columnsChanged := 0
	for trinoPath, tbl := range tables {
		parts := strings.SplitN(trinoPath, ".", 3)
		if len(parts) != 3 {
			continue
		}
		tSchema, tTable := parts[1], parts[2]

		// Look up the existing dataset for this Trino table, if any.
		var datasetID int
		err := s.db.QueryRow(`
			SELECT id FROM datasets
			WHERE trino_catalog = $1 AND trino_schema = $2 AND trino_table = $3
		`, catalog, tSchema, tTable).Scan(&datasetID)

		switch {
		case err == sql.ErrNoRows:
			// New table — register it with a placeholder description.
			name := tTable
			var nameTaken bool
			if err := s.db.QueryRow(`SELECT EXISTS(SELECT 1 FROM datasets WHERE name = $1)`, name).
				Scan(&nameTaken); err != nil {
				return added, columnsChanged, fmt.Errorf("failed to check dataset name for %s: %w", trinoPath, err)
			}
			if nameTaken {
				name = fmt.Sprintf("%s.%s", catalog, tTable)
			}
			if err := s.db.QueryRow(`
				INSERT INTO datasets (name, description, source_type, trino_catalog, trino_schema, trino_table)
				VALUES ($1, $2, $3, $4, $5, $6)
				RETURNING id
			`, name,
				"Auto-discovered from Trino — add a curated description via the Data Sources UI for richer AI context.",
				sourceType, catalog, tSchema, tTable,
			).Scan(&datasetID); err != nil {
				return added, columnsChanged, fmt.Errorf("failed to insert dataset for %s: %w", trinoPath, err)
			}
			added = append(added, name)
		case err != nil:
			return added, columnsChanged, fmt.Errorf("failed to look up dataset for %s: %w", trinoPath, err)
		}

		// Reconcile columns against Trino's live view for BOTH new and existing
		// datasets. Refreshing existing datasets is what keeps nested
		// ROW/ARRAY(ROW) types accurate when a source's shape changes — existing
		// datasets used to be skipped wholesale, which is how the metadata drifted
		// out of sync with Trino (e.g. arrays flattened to plain rows, so the AI
		// dot-accessed what was really an array). Curated description/sample_values
		// are preserved; only data_type / membership are reconciled.
		cols, _ := tbl["columns"].([]map[string]string)
		changed, err := s.reconcileDatasetColumns(datasetID, cols)
		if err != nil {
			log.Printf("catalog sync: failed to reconcile columns for %s: %v", trinoPath, err)
		}
		columnsChanged += changed
	}

	return added, columnsChanged, nil
}

// reconcileDatasetColumns brings a dataset's dataset_columns rows in line with
// the live column set Trino reports, WITHOUT clobbering curated metadata:
//   - a column whose data_type changed is UPDATEd in place (description,
//     sample_values, is_primary_key are untouched);
//   - a newly-appeared column is INSERTed;
//   - a column that no longer exists in the source is DELETEd.
//
// Schema-agnostic: it works purely off (name, type) pairs, so any source —
// nested Elasticsearch documents, Mongo, relational — reconciles the same way.
// Returns the number of columns added/updated/removed.
func (s *DataSourceService) reconcileDatasetColumns(datasetID int, cols []map[string]string) (int, error) {
	rows, err := s.db.Query(`SELECT column_name, data_type FROM dataset_columns WHERE dataset_id = $1`, datasetID)
	if err != nil {
		return 0, err
	}
	current := map[string]string{}
	for rows.Next() {
		var name, dtype string
		if err := rows.Scan(&name, &dtype); err != nil {
			rows.Close()
			return 0, err
		}
		current[name] = dtype
	}
	rows.Close()

	changed := 0
	live := map[string]bool{}
	for _, col := range cols {
		name := col["name"]
		if name == "" {
			continue
		}
		live[name] = true
		dtype := col["type"]
		existingType, ok := current[name]
		if !ok {
			isJoinable := strings.HasSuffix(name, "_id") || name == "id"
			if _, err := s.db.Exec(`
				INSERT INTO dataset_columns (dataset_id, column_name, data_type, is_joinable)
				VALUES ($1, $2, $3, $4)
			`, datasetID, name, dtype, isJoinable); err != nil {
				log.Printf("catalog sync: failed to insert column %d.%s: %v", datasetID, name, err)
				continue
			}
			changed++
		} else if existingType != dtype {
			if _, err := s.db.Exec(`
				UPDATE dataset_columns SET data_type = $1 WHERE dataset_id = $2 AND column_name = $3
			`, dtype, datasetID, name); err != nil {
				log.Printf("catalog sync: failed to refresh column %d.%s: %v", datasetID, name, err)
				continue
			}
			changed++
		}
	}

	// Prune columns Trino no longer reports (dropped/renamed fields).
	for name := range current {
		if !live[name] {
			if _, err := s.db.Exec(
				`DELETE FROM dataset_columns WHERE dataset_id = $1 AND column_name = $2`, datasetID, name,
			); err != nil {
				log.Printf("catalog sync: failed to prune column %d.%s: %v", datasetID, name, err)
				continue
			}
			changed++
		}
	}
	return changed, nil
}

// fetchSchemaFromTrino queries Trino's information_schema for a catalog.
func (s *DataSourceService) fetchSchemaFromTrino(catalog string) (map[string]interface{}, int, error) {
	skipSchemas := []string{"information_schema", "pg_catalog", "pg_toast", "_schema", "system"}
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

		// Map key stays raw/unquoted catalog.schema.table — syncDatasetsForCatalog
		// below splits it back into 3 parts. The "trino_path" value is the
		// quoted, directly-runnable form exposed to callers.
		key := fmt.Sprintf("%s.%s.%s", catalog, schema, table)
		if _, ok := tables[key]; !ok {
			tables[key] = map[string]interface{}{
				"trino_path": buildTrinoPath(catalog, schema, table),
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
