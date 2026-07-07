package trino

import (
	"database/sql"
	"database/sql/driver"
	"fmt"
	"os"
	"strings"
	"time"

	_ "github.com/trinodb/trino-go-client/trino"
)

// Client wraps the Trino database connection.
//
// Architecture boundary:
//   This is the ONLY component that talks to Trino.
//   It receives pre-validated SQL from the handler
//   and returns structured result sets.

type Client struct {
	db *sql.DB
}

// NewClient creates and validates a Trino connection.
func NewClient() (*Client, error) {
	host := getEnv("TRINO_HOST", "localhost")
	port := getEnv("TRINO_PORT", "8080")

	// Configure Trino connection
	// We connect as "trino" user with no catalog specified at connection time
	// (catalogs are specified in the SQL itself: catalog.schema.table)
	dsn := fmt.Sprintf("http://trino@%s:%s?session_properties=query_max_run_time=2m", host, port)

	db, err := sql.Open("trino", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to Trino: %w", err)
	}
	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)

	// Validate connection
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("failed to connect to Trino: %w", err)
	}

	return &Client{db: db}, nil
}

// Execute runs a SELECT query against Trino and returns structured results.
func (c *Client) Execute(sql string) (columns []string, rows [][]interface{}, err error) {
	// Trino rejects trailing semicolons ("mismatched input ';'") — strip them
	// so user-typed SQL ending in ";" doesn't fail.
	sql = strings.TrimRight(strings.TrimSpace(sql), "; \t\n")

	dbRows, err := c.db.Query(sql)
	if err != nil {
		return nil, nil, fmt.Errorf("trino query failed: %w", err)
	}
	defer dbRows.Close()

	// Get column names
	columns, err = dbRows.Columns()
	if err != nil {
		return nil, nil, fmt.Errorf("failed to get columns: %w", err)
	}

	// Scan rows
	for dbRows.Next() {
		// Create a slice of interface{} to hold each column value
		values := make([]interface{}, len(columns))
		valuePtrs := make([]interface{}, len(columns))
		for i := range values {
			valuePtrs[i] = &values[i]
		}

		if err := dbRows.Scan(valuePtrs...); err != nil {
			return nil, nil, fmt.Errorf("failed to scan row: %w", err)
		}

		// Convert values to JSON-safe types
		row := make([]interface{}, len(columns))
		for i, v := range values {
			row[i] = normalizeValue(v)
		}
		rows = append(rows, row)
	}

	if err := dbRows.Err(); err != nil {
		return nil, nil, fmt.Errorf("row iteration error: %w", err)
	}

	return columns, rows, nil
}

// normalizeValue converts raw database values to JSON-serializable types.
func normalizeValue(v interface{}) interface{} {
	if v == nil {
		return nil
	}

	switch val := v.(type) {
	case []byte:
		return string(val)
	case time.Time:
		return val.Format(time.RFC3339)
	case driver.Valuer:
		dv, err := val.Value()
		if err == nil {
			return normalizeValue(dv)
		}
		return fmt.Sprintf("%v", v)
	default:
		return v
	}
}

// Close closes the database connection pool.
func (c *Client) Close() error {
	return c.db.Close()
}

// Ping checks if Trino is reachable.
func (c *Client) Ping() error {
	return c.db.Ping()
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
