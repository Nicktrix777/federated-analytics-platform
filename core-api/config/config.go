package config

import (
	"fmt"
	"os"
)

// Config holds all runtime configuration for the Core API.
type Config struct {
	Port            string
	AIEngineURL     string
	QueryServiceURL string
	AIEnabled       bool
	APIAuthToken    string
	DatabaseDSN     string // postgres-meta (metadata + audit)
	SourceDSN       string // postgres-source (user data + uploads)
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

	// postgres-source DSN (for CSV upload table creation)
	srcHost := getEnv("POSTGRES_SOURCE_HOST", "postgres-source")
	srcPort := getEnv("POSTGRES_SOURCE_PORT", "5432")
	srcDB := getEnv("POSTGRES_SOURCE_DB", "source_db")
	srcUser := getEnv("POSTGRES_SOURCE_USER", "source_user")
	srcPass := getEnv("POSTGRES_SOURCE_PASSWORD", "source_pass_2024")

	sourceDSN := fmt.Sprintf(
		"host=%s port=%s dbname=%s user=%s password=%s sslmode=disable",
		srcHost, srcPort, srcDB, srcUser, srcPass,
	)

	return &Config{
		Port:            getEnv("CORE_API_PORT", "8081"),
		AIEngineURL:     getEnv("AI_ENGINE_URL", "http://localhost:8082"),
		QueryServiceURL: getEnv("QUERY_SERVICE_URL", "http://localhost:8083"),
		AIEnabled:       aiEnabled,
		APIAuthToken:    getEnv("API_AUTH_TOKEN", "poc-demo-token-2024"),
		DatabaseDSN:     dsn,
		SourceDSN:       sourceDSN,
	}
}


func getEnv(key, defaultValue string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultValue
}
