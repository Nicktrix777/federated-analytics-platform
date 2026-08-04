package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	_ "github.com/lib/pq"

	"github.com/federated-analytics/core-api/config"
	"github.com/federated-analytics/core-api/crypto"
	"github.com/federated-analytics/core-api/handlers"
	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/services"
)

func main() {
	cfg := config.Load()

	log.Printf("Starting Federated Analytics Platform — Core API v2")
	log.Printf("AI Enabled: %v", cfg.AIEnabled)
	log.Printf("AI Engine URL: %s", cfg.AIEngineURL)
	log.Printf("Query Service URL: %s", cfg.QueryServiceURL)

	// ── Database Connection ──────────────────────────────────
	db, err := connectDB(cfg.DatabaseDSN)
	if err != nil {
		log.Fatalf("Failed to connect to metadata database: %v", err)
	}
	defer db.Close()
	log.Println("Connected to metadata database")

	// ── Services ────────────────────────────────────
	encryptor, err := crypto.NewEncryptor(cfg.DatasourceEncryptionKey)
	if err != nil {
		log.Fatalf("Failed to initialize datasource encryptor: %v", err)
	}
	aiClient := services.NewAIClient(cfg.AIEngineURL)
	queryClient := services.NewQueryClient(cfg.QueryServiceURL)
	metadataSvc := services.NewMetadataService(db)
	uploadSvc := services.NewUploadService(cfg.SourceDSN, db)
	dataSourceSvc := services.NewDataSourceService(
		db, cfg.TrinoHost, cfg.TrinoPort, encryptor,
	)
	dashboardSvc := services.NewDashboardService(db)
	reportSvc := services.NewReportService(db)
	conversationSvc := services.NewConversationService(db)
	curationSvc := services.NewCurationService(db)

	// ── Catalog Auto-Sync ────────────────────────────
	// Discovers new Trino catalogs/tables (e.g. a freshly added Elasticsearch
	// index) and registers them without any manual SQL insert. Runs once at
	// startup (retrying briefly since Trino may still be starting) and then
	// on a recurring interval.
	go runCatalogSyncLoop(dataSourceSvc, cfg.CatalogSyncIntervalSeconds)

	// ── Handlers ────────────────────────────────────
	queryHandler := handlers.NewQueryHandler(aiClient, queryClient, metadataSvc, conversationSvc, curationSvc, cfg.AIEnabled)
	healthHandler := handlers.NewHealthHandler(cfg.AIEnabled)
	historyHandler := handlers.NewHistoryHandler(db)
	metadataHandler := handlers.NewMetadataHandler(metadataSvc)
	uploadHandler := handlers.NewUploadHandler(uploadSvc)
	dataSourceHandler := handlers.NewDataSourceHandler(dataSourceSvc)
	dashboardHandler := handlers.NewDashboardHandler(dashboardSvc, aiClient, queryClient, cfg.AIEnabled)
	reportHandler := handlers.NewReportHandler(reportSvc, aiClient, queryClient, cfg.AIEnabled)
	curationHandler := handlers.NewCurationHandler(curationSvc)
	conversationsHandler := handlers.NewConversationsHandler(conversationSvc)
	llmSettingsHandler := handlers.NewLLMSettingsHandler(aiClient, cfg.AIEnabled)

	// ── Router Setup ──────────────────────────────────────────
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.HandleMethodNotAllowed = true
	r.Use(gin.Logger())
	r.Use(gin.Recovery())
	r.Use(middleware.CORS())
	r.NoMethod(func(c *gin.Context) {
		c.JSON(http.StatusMethodNotAllowed, gin.H{
			"error":   "method not allowed",
			"details": "Check the allowed HTTP methods for this endpoint.",
		})
	})

	// Public routes (no auth)
	r.GET("/api/health", healthHandler.HandleHealth)

	// Authenticated routes
	api := r.Group("/api")
	api.Use(middleware.Auth(cfg.APIAuthToken))
	api.Use(middleware.Audit(db))
	{
		// ── Query & History ─────────────────────────────────
		api.POST("/query", queryHandler.HandleQuery)
		api.POST("/query/stream", queryHandler.HandleQueryStream) // SSE (docs/sse-events.md)
		api.GET("/history", historyHandler.HandleHistory)

		// ── Conversations (PR7) — the "Chats" sidebar tab ────
		// Distinct from /history (audit_logs): these are multi-turn NL threads
		// with structured turns, used to hydrate the transcript on reopen.
		api.GET("/conversations", conversationsHandler.HandleList)
		api.GET("/conversations/:id/turns", conversationsHandler.HandleTurns)

		// ── Metadata & Upload ───────────────────────────────
		api.GET("/metadata/datasets", metadataHandler.HandleDatasets)
		api.POST("/upload", uploadHandler.HandleUpload)
		api.GET("/upload/status/:table", uploadHandler.HandleUploadStatus)

		// ── Data Sources (NEW) ──────────────────────────────
		// Register external connections (Postgres, ES, Mongo, etc.)
		// and auto-fetch their schemas for AI prompts
		api.GET("/datasources", dataSourceHandler.HandleList)
		api.POST("/datasources", dataSourceHandler.HandleCreate)
		api.GET("/datasources/:id", dataSourceHandler.HandleGet)
		api.PUT("/datasources/:id", dataSourceHandler.HandleUpdate)
		api.DELETE("/datasources/:id", dataSourceHandler.HandleDelete)
		api.POST("/datasources/:id/refresh", dataSourceHandler.HandleRefreshSchema)
		api.POST("/datasources/refresh-all", dataSourceHandler.HandleRefreshAll)
		api.POST("/datasources/sync", dataSourceHandler.HandleSyncCatalogs)

		// ── Dashboards (NEW) ────────────────────────────────
		api.GET("/dashboards", dashboardHandler.HandleList)
		api.POST("/dashboards", dashboardHandler.HandleCreate)
		api.POST("/dashboards/generate", dashboardHandler.HandleGenerate)
		api.POST("/dashboards/generate/stream", dashboardHandler.HandleGenerateStream) // SSE
		api.POST("/dashboards/:id/refine", dashboardHandler.HandleRefine)
		api.POST("/dashboards/:id/refine/stream", dashboardHandler.HandleRefineStream) // SSE
		api.GET("/dashboards/:id", dashboardHandler.HandleGet)
		api.PUT("/dashboards/:id", dashboardHandler.HandleUpdate)
		api.DELETE("/dashboards/:id", dashboardHandler.HandleDelete)
		api.POST("/dashboards/:id/widgets", dashboardHandler.HandleCreateWidget)
		api.PUT("/dashboards/:id/widgets/:wid", dashboardHandler.HandleUpdateWidget)
		api.DELETE("/dashboards/:id/widgets/:wid", dashboardHandler.HandleDeleteWidget)

		// ── Reports (Excel export) ──────────────────────────
		// Persisted sheet-query definitions; /download executes every sheet
		// live and streams a formatted workbook (rows are never stored).
		api.GET("/reports", reportHandler.HandleList)
		api.POST("/reports", reportHandler.HandleCreate)
		api.POST("/reports/generate", reportHandler.HandleGenerate)
		api.POST("/reports/generate/stream", reportHandler.HandleGenerateStream) // SSE
		api.POST("/reports/:id/refine", reportHandler.HandleRefine)
		api.POST("/reports/:id/refine/stream", reportHandler.HandleRefineStream) // SSE
		api.GET("/reports/:id", reportHandler.HandleGet)
		api.PUT("/reports/:id", reportHandler.HandleUpdate)
		api.DELETE("/reports/:id", reportHandler.HandleDelete)
		api.GET("/reports/:id/download", reportHandler.HandleDownload)
		api.POST("/reports/:id/sheets", reportHandler.HandleCreateSheet)
		api.PUT("/reports/:id/sheets/:sid", reportHandler.HandleUpdateSheet)
		api.DELETE("/reports/:id/sheets/:sid", reportHandler.HandleDeleteSheet)

		// ── Curation Queue (PR6) ─────────────────────────────
		// Lists AI queries that could not be auto-resolved (repair_exhausted or
		// zero_rows_unresolved). Operators resolve items by flipping status via SQL.
		// TODO(security): uses same static bearer token as all other endpoints;
		// tighten when real auth lands.
		api.GET("/curation", curationHandler.HandleList)

		// ── LLM Settings ─────────────────────────────────────
		// Runtime-editable, non-secret LLM configuration (provider-prefixed
		// model strings, base_url, fast-path, per-tier RPM). Proxied to the AI
		// Engine, which validates + hot-reloads. API keys stay in the AI
		// Engine's environment and are never read/written here.
		api.GET("/llm-settings", llmSettingsHandler.HandleGet)
		api.PUT("/llm-settings", llmSettingsHandler.HandleUpdate)
	}

	// ── HTTP Server with Graceful Shutdown ────────────────────
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%s", cfg.Port),
		Handler:      r,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 430 * time.Second, // Must cover AI client timeout (300s) + query execution (120s) so SSE streams aren't cut off mid-pipeline
		IdleTimeout:  60 * time.Second,
	}

	go func() {
		log.Printf("Core API listening on :%s", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Core API failed: %v", err)
		}
	}()

	// Wait for interrupt signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down Core API...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("Core API forced shutdown: %v", err)
	}
}

// runCatalogSyncLoop runs an initial retry loop (Trino may still be starting
// up when core-api boots) and then re-syncs on a fixed interval so newly
// added Trino catalogs/indices surface without a manual DB insert.
func runCatalogSyncLoop(svc *services.DataSourceService, intervalSeconds int) {
	for attempt := 1; attempt <= 5; attempt++ {
		if result, err := svc.SyncCatalogsFromTrino(); err != nil {
			log.Printf("catalog sync: startup attempt %d/5 failed: %v", attempt, err)
			time.Sleep(5 * time.Second)
			continue
		} else {
			log.Printf("catalog sync: %s", result.Message)
			break
		}
	}

	ticker := time.NewTicker(time.Duration(intervalSeconds) * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		result, err := svc.SyncCatalogsFromTrino()
		if err != nil {
			log.Printf("catalog sync: periodic sync failed: %v", err)
			continue
		}
		log.Printf("catalog sync: %s", result.Message)
	}
}

// connectDB establishes a connection to PostgreSQL with retry logic.
func connectDB(dsn string) (*sql.DB, error) {
	var db *sql.DB
	var err error

	for i := 0; i < 10; i++ {
		db, err = sql.Open("postgres", dsn)
		if err == nil {
			if pingErr := db.Ping(); pingErr == nil {
				db.SetMaxOpenConns(25)
				db.SetMaxIdleConns(10)
				db.SetConnMaxLifetime(5 * time.Minute)
				return db, nil
			}
		}
		log.Printf("DB connection attempt %d/10 failed, retrying in 3s...", i+1)
		time.Sleep(3 * time.Second)
	}
	return nil, fmt.Errorf("could not connect to database after 10 attempts: %v", err)
}
