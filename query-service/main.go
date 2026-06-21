package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/query-service/handlers"
	trinoclient "github.com/federated-analytics/query-service/trino"
)

func main() {
	log.Println("Starting Federated Analytics Platform — Query Service")

	// ── Trino Connection ──────────────────────────────────────
	var trino *trinoclient.Client
	var err error

	// Retry connecting to Trino (it takes time to start)
	for i := 0; i < 20; i++ {
		trino, err = trinoclient.NewClient()
		if err == nil {
			log.Println("Connected to Trino")
			break
		}
		log.Printf("Trino connection attempt %d/20 failed: %v. Retrying in 5s...", i+1, err)
		time.Sleep(5 * time.Second)
	}
	if err != nil {
		log.Fatalf("Failed to connect to Trino after 20 attempts: %v", err)
	}
	defer trino.Close()

	// ── Router Setup ──────────────────────────────────────────
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Logger())
	r.Use(gin.Recovery())

	executeHandler := handlers.NewExecuteHandler(trino)

	r.GET("/health", func(c *gin.Context) {
		// Check Trino is still reachable
		if err := trino.Ping(); err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"status": "error", "trino": err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "ok", "service": "query-service"})
	})

	r.POST("/api/execute", executeHandler.HandleExecute)

	// ── Server ────────────────────────────────────────────────
	port := getEnv("QUERY_SERVICE_PORT", "8083")
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%s", port),
		Handler:      r,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 130 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	go func() {
		log.Printf("Query Service listening on :%s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Query Service failed: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down Query Service...")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
