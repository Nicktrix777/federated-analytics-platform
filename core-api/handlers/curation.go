package handlers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// CurationHandler exposes the needs_curation queue for operator review.
//
// v1: GET /api/curation only — no resolve endpoint (flip status via SQL).
// TODO(security): same static bearer token as all other endpoints; tighten
// when real auth lands.

type CurationHandler struct {
	svc *services.CurationService
}

func NewCurationHandler(svc *services.CurationService) *CurationHandler {
	return &CurationHandler{svc: svc}
}

// HandleList serves GET /api/curation?status=new&limit=50
//
// Returns a JSON array of CurationItem. Defaults: status=new, limit=50.
// status is validated server-side against an allowlist (the service layer);
// an invalid status returns an empty list rather than an error so callers
// can safely probe the endpoint.
func (h *CurationHandler) HandleList(c *gin.Context) {
	status := c.DefaultQuery("status", "new")
	limitStr := c.DefaultQuery("limit", "50")

	limit, err := strconv.Atoi(limitStr)
	if err != nil || limit <= 0 {
		limit = 50
	}
	// Cap at 500 — avoid accidental full-table scans.
	if limit > 500 {
		limit = 500
	}

	items, err := h.svc.List(status, limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error:   "Failed to list curation items",
			Details: err.Error(),
		})
		return
	}

	// Return an empty array (not null) when there are no items.
	if items == nil {
		items = []models.CurationItem{}
	}
	c.JSON(http.StatusOK, items)
}
