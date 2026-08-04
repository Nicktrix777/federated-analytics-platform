package config

import (
	"fmt"
	"log"
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
	DatasourceEncryptionKey    string // base64 AES-256 key encrypting stored datasource credentials at rest
	TrinoHost                  string // for schema refresh via Trino REST API
	TrinoPort                  string
	CatalogSyncIntervalSeconds int // how often to auto-discover new Trino catalogs/tables
}

// knownInsecureDefaults are the exact values shipped in .env.example. A deployment
// still running any of these is only as secure as the public source repo.
var knownInsecureDefaults = map[string]string{
	"API_AUTH_TOKEN":          "poc-demo-token-2024",
	"POSTGRES_META_PASSWORD":  "meta_pass_2024",
	"POSTGRES_SOURCE_PASSWORD": "source_pass_2024",
}

// Load reads configuration from environment variables.
func Load() *Config {
	aiEnabled := true
	if val := os.Getenv("AI_ENABLED"); val == "false" {
		aiEnabled = false
	}
	allowDevDefaults := os.Getenv("ALLOW_DEV_DEFAULTS") == "true"

	apiAuthToken := getEnv("API_AUTH_TOKEN", "poc-demo-token-2024")

	pgHost := getEnv("POSTGRES_META_HOST", "localhost")
	pgPort := getEnv("POSTGRES_META_PORT", "5432")
	pgDB := getEnv("POSTGRES_META_DB", "analytics_meta")
	pgUser := getEnv("POSTGRES_META_USER", "meta_user")
	pgPass := getEnv("POSTGRES_META_PASSWORD", "meta_pass_2024")

	srcHost := getEnv("POSTGRES_SOURCE_HOST", "postgres-source")
	srcPort := getEnv("POSTGRES_SOURCE_PORT", "5432")
	srcDB := getEnv("POSTGRES_SOURCE_DB", "source_db")
	srcUser := getEnv("POSTGRES_SOURCE_USER", "source_user")
	srcPass := getEnv("POSTGRES_SOURCE_PASSWORD", "source_pass_2024")

	requireChanged("API_AUTH_TOKEN", apiAuthToken, allowDevDefaults)
	requireChanged("POSTGRES_META_PASSWORD", pgPass, allowDevDefaults)
	requireChanged("POSTGRES_SOURCE_PASSWORD", srcPass, allowDevDefaults)

	encryptionKey := os.Getenv("DATASOURCE_ENCRYPTION_KEY")
	if encryptionKey == "" {
		log.Fatal("DATASOURCE_ENCRYPTION_KEY is required (base64-encoded 32-byte AES-256 key — " +
			"generate one with `openssl rand -base64 32`). Datasource credentials cannot be stored without it.")
	}

	dsn := fmt.Sprintf(
		"host=%s port=%s dbname=%s user=%s password=%s sslmode=disable",
		pgHost, pgPort, pgDB, pgUser, pgPass,
	)

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
		APIAuthToken:               apiAuthToken,
		DatabaseDSN:                dsn,
		SourceDSN:                  sourceDSN,
		DatasourceEncryptionKey:    encryptionKey,
		TrinoHost:                  getEnv("TRINO_HOST", "trino"),
		TrinoPort:                  getEnv("TRINO_PORT", "8080"),
		CatalogSyncIntervalSeconds: catalogSyncInterval,
	}
}

// requireChanged fails startup if a security-sensitive value still matches the
// placeholder shipped in .env.example, unless ALLOW_DEV_DEFAULTS=true (local
// development only) — otherwise a deployment that forgot to set a real value
// would silently run wide open with a value published in the source repo.
func requireChanged(envVar, value string, allowDevDefaults bool) {
	insecure, tracked := knownInsecureDefaults[envVar]
	if !tracked || value != insecure {
		return
	}
	if allowDevDefaults {
		log.Printf("WARNING: %s is still the public .env.example default — fine for local dev, "+
			"unsafe anywhere real credentials or customer data are involved.", envVar)
		return
	}
	log.Fatalf("%s is still set to the public .env.example default (%q). Set a real value, "+
		"or set ALLOW_DEV_DEFAULTS=true for local development only.", envVar, insecure)
}

func getEnv(key, defaultValue string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultValue
}
