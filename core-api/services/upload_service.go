package services

import (
	"database/sql"
	"encoding/csv"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// UploadService handles CSV/Excel file uploads:
//  1. Parses the CSV data
//  2. Auto-detects column types
//  3. Creates a new table in postgres-source
//  4. Inserts all rows
//  5. Registers the dataset + columns in postgres-meta
//
// After upload, the AI Engine's cache is invalidated so the new
// table immediately appears in NL→SQL prompts.

type UploadService struct {
	sourceDSN string // postgres-source connection
	metaDB    *sql.DB // postgres-meta connection (already open)
}

func NewUploadService(sourceDSN string, metaDB *sql.DB) *UploadService {
	return &UploadService{
		sourceDSN: sourceDSN,
		metaDB:    metaDB,
	}
}

// UploadResult is returned after a successful upload.
type UploadResult struct {
	TableName   string   `json:"table_name"`
	TrinoPath   string   `json:"trino_path"`
	RowCount    int      `json:"row_count"`
	Columns     []string `json:"columns"`
	DatasetID   int      `json:"dataset_id"`
	SampleQuery string   `json:"sample_query"`
}

// ProcessCSV parses the CSV, creates a table in postgres-source,
// inserts all rows, and registers metadata. Returns the upload result.
func (s *UploadService) ProcessCSV(filename string, reader io.Reader) (*UploadResult, error) {
	// Parse CSV
	csvReader := csv.NewReader(reader)
	csvReader.FieldsPerRecord = -1 // Allow variable fields
	csvReader.TrimLeadingSpace = true

	headers, err := csvReader.Read()
	if err != nil {
		return nil, fmt.Errorf("failed to read CSV headers: %w", err)
	}
	if len(headers) == 0 {
		return nil, fmt.Errorf("CSV file has no columns")
	}

	// Read all data rows
	var rows [][]string
	for {
		row, err := csvReader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("CSV parse error: %w", err)
		}
		rows = append(rows, row)
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("CSV file has no data rows")
	}
	if len(rows) > 50000 {
		return nil, fmt.Errorf("CSV exceeds 50,000 row limit (got %d rows)", len(rows))
	}

	// Sanitize headers to valid SQL column names
	sanitizedHeaders := make([]string, len(headers))
	for i, h := range headers {
		sanitizedHeaders[i] = sanitizeColName(h)
	}

	// Detect column types from data
	colTypes := detectColumnTypes(sanitizedHeaders, rows)

	// Generate table name from filename
	tableName := sanitizeTableName(filename)

	// Ensure table name is unique by appending timestamp if needed
	tableName = fmt.Sprintf("%s_%d", tableName, time.Now().Unix())

	// Open postgres-source connection
	sourceDB, err := sql.Open("postgres", s.sourceDSN)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to postgres-source: %w", err)
	}
	defer sourceDB.Close()

	if err := sourceDB.Ping(); err != nil {
		return nil, fmt.Errorf("postgres-source unreachable: %w", err)
	}

	// Create table and insert data
	if err := s.createAndPopulateTable(sourceDB, tableName, sanitizedHeaders, colTypes, rows); err != nil {
		return nil, fmt.Errorf("failed to create table: %w", err)
	}

	// Register metadata in postgres-meta
	datasetID, err := s.registerMetadata(tableName, sanitizedHeaders, colTypes, rows, filename)
	if err != nil {
		// Log but don't fail — table is already created, just won't have rich metadata
		fmt.Printf("Warning: failed to register metadata for %s: %v\n", tableName, err)
		datasetID = -1
	}

	trinoPath := fmt.Sprintf("postgres_source.public.%s", tableName)

	return &UploadResult{
		TableName:   tableName,
		TrinoPath:   trinoPath,
		RowCount:    len(rows),
		Columns:     sanitizedHeaders,
		DatasetID:   datasetID,
		SampleQuery: fmt.Sprintf("SELECT * FROM %s LIMIT 10", trinoPath),
	}, nil
}

func (s *UploadService) createAndPopulateTable(
	db *sql.DB,
	tableName string,
	headers []string,
	colTypes []string,
	rows [][]string,
) error {
	// Build CREATE TABLE statement
	colDefs := make([]string, len(headers))
	for i, h := range headers {
		colDefs[i] = fmt.Sprintf("%q %s", h, colTypes[i])
	}
	createSQL := fmt.Sprintf(
		"CREATE TABLE IF NOT EXISTS %q (upload_row_id SERIAL PRIMARY KEY, %s)",
		tableName,
		strings.Join(colDefs, ", "),
	)

	if _, err := db.Exec(createSQL); err != nil {
		return fmt.Errorf("CREATE TABLE failed: %w", err)
	}

	// Batch insert rows
	batchSize := 500
	for start := 0; start < len(rows); start += batchSize {
		end := start + batchSize
		if end > len(rows) {
			end = len(rows)
		}
		batch := rows[start:end]

		if err := insertBatch(db, tableName, headers, colTypes, batch); err != nil {
			return fmt.Errorf("insert failed at row %d: %w", start, err)
		}
	}
	return nil
}

func insertBatch(db *sql.DB, tableName string, headers []string, colTypes []string, rows [][]string) error {
	if len(rows) == 0 {
		return nil
	}

	quotedCols := make([]string, len(headers))
	for i, h := range headers {
		quotedCols[i] = fmt.Sprintf("%q", h)
	}

	// Build: INSERT INTO "table" ("col1", "col2") VALUES ($1,$2),($3,$4),...
	valuePlaceholders := make([]string, len(rows))
	args := make([]interface{}, 0, len(rows)*len(headers))

	for rowIdx, row := range rows {
		placeholders := make([]string, len(headers))
		for colIdx := range headers {
			argIdx := rowIdx*len(headers) + colIdx + 1
			placeholders[colIdx] = fmt.Sprintf("$%d", argIdx)

			var val interface{}
			if colIdx < len(row) && row[colIdx] != "" {
				val = coerceValue(row[colIdx], colTypes[colIdx])
			} else {
				val = nil // NULL for empty/missing values
			}
			args = append(args, val)
		}
		valuePlaceholders[rowIdx] = fmt.Sprintf("(%s)", strings.Join(placeholders, ","))
	}

	insertSQL := fmt.Sprintf(
		"INSERT INTO %q (%s) VALUES %s",
		tableName,
		strings.Join(quotedCols, ","),
		strings.Join(valuePlaceholders, ","),
	)

	_, err := db.Exec(insertSQL, args...)
	return err
}

func (s *UploadService) registerMetadata(
	tableName string,
	headers []string,
	colTypes []string,
	rows [][]string,
	originalFilename string,
) (int, error) {
	// Upsert dataset
	var datasetID int
	err := s.metaDB.QueryRow(`
		INSERT INTO datasets (name, description, source_type, trino_catalog, trino_schema, trino_table)
		VALUES ($1, $2, 'postgresql', 'postgres_source', 'public', $3)
		ON CONFLICT (name) DO UPDATE
			SET description = EXCLUDED.description,
			    updated_at = NOW()
		RETURNING id
	`,
		tableName,
		fmt.Sprintf("Uploaded CSV/Excel file: %s (%d rows, %d columns)", originalFilename, len(rows), len(headers)),
		tableName,
	).Scan(&datasetID)
	if err != nil {
		return -1, fmt.Errorf("failed to insert dataset: %w", err)
	}

	// Delete old columns
	if _, err := s.metaDB.Exec("DELETE FROM dataset_columns WHERE dataset_id = $1", datasetID); err != nil {
		return -1, err
	}

	// Insert column metadata with sample values
	for i, h := range headers {
		samples := collectSamples(rows, i, 3)
		_, err := s.metaDB.Exec(`
			INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_joinable, sample_values)
			VALUES ($1, $2, $3, $4, false, $5)
		`,
			datasetID, h, colTypes[i],
			fmt.Sprintf("Column from uploaded file '%s'", originalFilename),
			strings.Join(samples, ", "),
		)
		if err != nil {
			return -1, fmt.Errorf("failed to insert column %s: %w", h, err)
		}
	}

	return datasetID, nil
}

// ── Type Detection ────────────────────────────────────────────

func detectColumnTypes(headers []string, rows [][]string) []string {
	types := make([]string, len(headers))
	for colIdx := range headers {
		types[colIdx] = inferColType(rows, colIdx)
	}
	return types
}

func inferColType(rows [][]string, colIdx int) string {
	isInt := true
	isFloat := true
	isDate := true
	isBoolean := true

	nonEmptyCount := 0
	for _, row := range rows {
		if colIdx >= len(row) || strings.TrimSpace(row[colIdx]) == "" {
			continue
		}
		val := strings.TrimSpace(row[colIdx])
		nonEmptyCount++

		// Test INTEGER
		if isInt {
			if _, err := strconv.ParseInt(val, 10, 64); err != nil {
				isInt = false
			}
		}
		// Test FLOAT/DOUBLE
		if isFloat {
			if _, err := strconv.ParseFloat(val, 64); err != nil {
				isFloat = false
			}
		}
		// Test DATE (YYYY-MM-DD or DD/MM/YYYY or MM/DD/YYYY)
		if isDate {
			if !looksLikeDate(val) {
				isDate = false
			}
		}
		// Test BOOLEAN
		if isBoolean {
			low := strings.ToLower(val)
			if low != "true" && low != "false" && low != "1" && low != "0" && low != "yes" && low != "no" {
				isBoolean = false
			}
		}
	}

	if nonEmptyCount == 0 {
		return "VARCHAR"
	}

	switch {
	case isBoolean && nonEmptyCount > 0:
		return "BOOLEAN"
	case isInt:
		return "BIGINT"
	case isFloat:
		return "DOUBLE PRECISION"
	case isDate:
		return "DATE"
	default:
		return "VARCHAR"
	}
}

var datePatterns = []*regexp.Regexp{
	regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`),           // YYYY-MM-DD
	regexp.MustCompile(`^\d{2}/\d{2}/\d{4}$`),           // DD/MM/YYYY or MM/DD/YYYY
	regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}`), // ISO datetime
}

func looksLikeDate(val string) bool {
	for _, p := range datePatterns {
		if p.MatchString(val) {
			return true
		}
	}
	return false
}

func coerceValue(val string, colType string) interface{} {
	val = strings.TrimSpace(val)
	switch colType {
	case "BIGINT":
		if v, err := strconv.ParseInt(val, 10, 64); err == nil {
			return v
		}
	case "DOUBLE PRECISION":
		if v, err := strconv.ParseFloat(val, 64); err == nil {
			return v
		}
	case "BOOLEAN":
		low := strings.ToLower(val)
		return low == "true" || low == "1" || low == "yes"
	}
	return val // VARCHAR fallback
}

// ── Sanitization ─────────────────────────────────────────────

var nonAlphanumeric = regexp.MustCompile(`[^a-z0-9_]`)

func sanitizeColName(name string) string {
	name = strings.TrimSpace(name)
	// Replace spaces and special chars with underscores
	result := strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			return unicode.ToLower(r)
		}
		return '_'
	}, name)
	result = nonAlphanumeric.ReplaceAllString(strings.ToLower(result), "_")
	// Collapse multiple underscores
	for strings.Contains(result, "__") {
		result = strings.ReplaceAll(result, "__", "_")
	}
	result = strings.Trim(result, "_")
	if result == "" {
		result = "col"
	}
	// Reserved word prefix
	reserved := map[string]bool{
		"select": true, "from": true, "where": true, "table": true,
		"index": true, "column": true, "order": true, "group": true,
	}
	if reserved[result] {
		result = "col_" + result
	}
	return result
}

func sanitizeTableName(filename string) string {
	// Remove extension
	name := filename
	if idx := strings.LastIndex(name, "."); idx > 0 {
		name = name[:idx]
	}
	return sanitizeColName(name)
}

func collectSamples(rows [][]string, colIdx int, max int) []string {
	seen := map[string]bool{}
	var samples []string
	for _, row := range rows {
		if len(samples) >= max {
			break
		}
		if colIdx >= len(row) {
			continue
		}
		v := strings.TrimSpace(row[colIdx])
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		samples = append(samples, v)
	}
	return samples
}
