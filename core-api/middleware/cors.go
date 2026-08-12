package middleware

import (
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
)

// CORS returns a permissive CORS middleware for local development.
// In production, restrict AllowOrigins to your actual frontend domain.
func CORS() gin.HandlerFunc {
	return cors.New(cors.Config{
		// http://frontend:5173 covers the dev compose overlay reached by
		// compose service DNS name (e.g. qa/run.sh's Playwright runner) —
		// browsers attach Origin even on same-origin-via-proxy POSTs, so
		// without this every mutating request 403s while GETs pass fine.
		AllowOrigins: []string{"http://localhost:3000", "http://localhost:5173", "http://frontend:5173", "http://frontend"},
		AllowMethods: []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowHeaders: []string{"Origin", "Content-Type", "Authorization"},
		// Content-Disposition is exposed so the report download can read the
		// server-chosen .xlsx filename.
		ExposeHeaders:    []string{"Content-Length", "Content-Disposition"},
		AllowCredentials: true,
		MaxAge:           12 * time.Hour,
	})
}
