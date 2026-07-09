package config

import (
	"fmt"
	"os"
	"strconv"
)

// Config holds all runtime configuration for the Core API.
type Config struct {
	Port                       string
	AIEngineURL                string
	QueryServiceURL            string
	AIEnabled                  bool
	APIAuthToken               string
	DatabaseDSN                string // postgres-meta (metadata + audit)
	SourceDSN                  string // postgres-source (user data + uploads)
	TrinoHost                  string // for schema refresh via Trino REST API
	TrinoPort                  string
	CatalogSyncIntervalSeconds int // how often to auto-discover new Trino catalogs/tables
}

// Load reads configuration from environment variables.
func Load() *Config {
	aiEnabled := true
	if val := os.Getenv("AI_ENABLED"); val == "false" {
		aiEnabled = false
	}

	pgHost := getEnv("POSTGRES_META_HOST", "localhost")
	pgPort := getEnv("POSTGRES_META_PORT", "5432")
	pgDB := getEnv("POSTGRES_META_DB", "analytics_meta")
	pgUser := getEnv("POSTGRES_META_USER", "meta_user")
	pgPass := getEnv("POSTGRES_META_PASSWORD", "meta_pass_2024")

	dsn := fmt.Sprintf(
		"host=%s port=%s dbname=%s user=%s password=%s sslmode=disable",
		pgHost, pgPort, pgDB, pgUser, pgPass,
	)

	srcHost := getEnv("POSTGRES_SOURCE_HOST", "postgres-source")
	srcPort := getEnv("POSTGRES_SOURCE_PORT", "5432")
	srcDB := getEnv("POSTGRES_SOURCE_DB", "source_db")
	srcUser := getEnv("POSTGRES_SOURCE_USER", "source_user")
	srcPass := getEnv("POSTGRES_SOURCE_PASSWORD", "source_pass_2024")

	sourceDSN := fmt.Sprintf(
		"host=%s port=%s dbname=%s user=%s password=%s sslmode=disable",
		srcHost, srcPort, srcDB, srcUser, srcPass,
	)

	catalogSyncInterval, err := strconv.Atoi(getEnv("CATALOG_SYNC_INTERVAL_SECONDS", "300"))
	if err != nil || catalogSyncInterval <= 0 {
		catalogSyncInterval = 300
	}

	return &Config{
		Port:                       getEnv("CORE_API_PORT", "8081"),
		AIEngineURL:                getEnv("AI_ENGINE_URL", "http://localhost:8082"),
		QueryServiceURL:            getEnv("QUERY_SERVICE_URL", "http://localhost:8083"),
		AIEnabled:                  aiEnabled,
		APIAuthToken:               getEnv("API_AUTH_TOKEN", "poc-demo-token-2024"),
		DatabaseDSN:                dsn,
		SourceDSN:                  sourceDSN,
		TrinoHost:                  getEnv("TRINO_HOST", "trino"),
		TrinoPort:                  getEnv("TRINO_PORT", "8080"),
		CatalogSyncIntervalSeconds: catalogSyncInterval,
	}
}

func getEnv(key, defaultValue string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultValue
}
