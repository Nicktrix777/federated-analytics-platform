package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// LLMSettingsHandler exposes the AI Engine's runtime-editable LLM configuration
// to the UI. It is a thin proxy: the AI Engine owns validation, persistence, and
// the live hot-reload of the provider/agent stack. Secret API keys are never
// handled here — they live in the AI Engine's environment and are only reported
// as present/absent booleans.
type LLMSettingsHandler struct {
	ai        *services.AIClient
	aiEnabled bool
}

func NewLLMSettingsHandler(ai *services.AIClient, aiEnabled bool) *LLMSettingsHandler {
	return &LLMSettingsHandler{ai: ai, aiEnabled: aiEnabled}
}

// GET /api/llm-settings — effective config + which provider keys are configured.
func (h *LLMSettingsHandler) HandleGet(c *gin.Context) {
	if !h.aiEnabled {
		c.JSON(http.StatusServiceUnavailable, models.ErrorResponse{Error: "AI features are disabled"})
		return
	}
	status, body, err := h.ai.GetLLMSettings(middleware.RequestIDFromContext(c))
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error: "AI engine unreachable", Details: err.Error(),
		})
		return
	}
	c.Data(status, "application/json; charset=utf-8", body)
}

// PUT /api/llm-settings — update the non-secret config; the AI Engine validates,
// persists, and hot-reloads. Relays the AI Engine's status/body verbatim so a
// validation error (400 + detail) reaches the UI unchanged.
func (h *LLMSettingsHandler) HandleUpdate(c *gin.Context) {
	if !h.aiEnabled {
		c.JSON(http.StatusServiceUnavailable, models.ErrorResponse{Error: "AI features are disabled"})
		return
	}
	var payload map[string]interface{}
	if err := c.ShouldBindJSON(&payload); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "invalid request body", Details: err.Error()})
		return
	}
	status, body, err := h.ai.UpdateLLMSettings(middleware.RequestIDFromContext(c), payload)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error: "AI engine unreachable", Details: err.Error(),
		})
		return
	}
	c.Data(status, "application/json; charset=utf-8", body)
}
