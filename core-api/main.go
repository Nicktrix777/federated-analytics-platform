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
	"github.com/federated-analytics/core-api/handlers"
	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/services"
)

func main() {
	cfg := config.Load()

	log.Printf("Starting Federated Analytics Platform — Core API")
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
	aiClient := services.NewAIClient(cfg.AIEngineURL)
	queryClient := services.NewQueryClient(cfg.QueryServiceURL)
	metadataSvc := services.NewMetadataService(db)
	uploadSvc := services.NewUploadService(cfg.SourceDSN, db)


	// ── Handlers ────────────────────────────────────
	queryHandler := handlers.NewQueryHandler(aiClient, queryClient, metadataSvc, cfg.AIEnabled)
	healthHandler := handlers.NewHealthHandler(cfg.AIEnabled)
	historyHandler := handlers.NewHistoryHandler(db)
	metadataHandler := handlers.NewMetadataHandler(metadataSvc)
	uploadHandler := handlers.NewUploadHandler(uploadSvc, cfg.AIEngineURL)


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
			"details": "Use POST for /api/query.",
		})
	})

	// Public routes (no auth)
	r.GET("/api/health", healthHandler.HandleHealth)

	// Authenticated routes
	api := r.Group("/api")
	api.Use(middleware.Auth(cfg.APIAuthToken))
	api.Use(middleware.Audit(db))
	{
		api.POST("/query", queryHandler.HandleQuery)
		api.GET("/history", historyHandler.HandleHistory)
		api.GET("/metadata/datasets", metadataHandler.HandleDatasets)
		api.POST("/upload", uploadHandler.HandleUpload)
		api.GET("/upload/status/:table", uploadHandler.HandleUploadStatus)
	}

	// ── HTTP Server with Graceful Shutdown ────────────────────
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%s", cfg.Port),
		Handler:      r,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 120 * time.Second,
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
	// Give 10 seconds for in-flight requests to complete
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("Core API forced shutdown: %v", err)
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
				db.SetMaxOpenConns(10)
				db.SetMaxIdleConns(5)
				db.SetConnMaxLifetime(5 * time.Minute)
				return db, nil
			}
		}
		log.Printf("DB connection attempt %d/10 failed, retrying in 3s...", i+1)
		time.Sleep(3 * time.Second)
	}
	return nil, fmt.Errorf("could not connect to database after 10 attempts: %v", err)
}
