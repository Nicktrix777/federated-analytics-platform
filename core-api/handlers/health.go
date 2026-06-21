package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type HealthHandler struct {
	aiEnabled bool
}

func NewHealthHandler(aiEnabled bool) *HealthHandler {
	return &HealthHandler{aiEnabled: aiEnabled}
}

func (h *HealthHandler) HandleHealth(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":     "ok",
		"service":    "core-api",
		"ai_enabled": h.aiEnabled,
		"version":    "1.0.0-poc",
	})
}
