package handlers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// DataSourceHandler manages registered external data source connections.
// Users add Elasticsearch, PostgreSQL, MongoDB, etc. through the UI,
// and the platform auto-fetches schemas/mappings and makes them queryable.

type DataSourceHandler struct {
	svc *services.DataSourceService
}

func NewDataSourceHandler(svc *services.DataSourceService) *DataSourceHandler {
	return &DataSourceHandler{svc: svc}
}

// GET /api/datasources — list all registered sources
func (h *DataSourceHandler) HandleList(c *gin.Context) {
	sources, err := h.svc.List()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to list data sources", Details: err.Error(),
		})
		return
	}
	if sources == nil {
		sources = []models.DataSource{}
	}
	c.JSON(http.StatusOK, gin.H{"data_sources": sources, "count": len(sources)})
}

// POST /api/datasources — register a new source
func (h *DataSourceHandler) HandleCreate(c *gin.Context) {
	var req models.CreateDataSourceRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	ds, err := h.svc.Create(req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create data source", Details: err.Error(),
		})
		return
	}

	// Auto-trigger schema refresh on creation (best-effort)
	go func() {
		if _, err := h.svc.RefreshSchema(ds.ID); err != nil {
			// Non-critical — user can refresh manually
		}
	}()

	c.JSON(http.StatusCreated, ds)
}

// GET /api/datasources/:id — get a single source
func (h *DataSourceHandler) HandleGet(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	ds, err := h.svc.GetByID(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if ds == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Data source not found"})
		return
	}
	c.JSON(http.StatusOK, ds)
}

// PUT /api/datasources/:id — update a source
func (h *DataSourceHandler) HandleUpdate(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.UpdateDataSourceRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	ds, err := h.svc.Update(id, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update data source", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, ds)
}

// DELETE /api/datasources/:id — remove a source
func (h *DataSourceHandler) HandleDelete(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	if err := h.svc.Delete(id); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to delete data source", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Data source removed"})
}

// POST /api/datasources/:id/refresh — refresh schema from Trino
// This fetches the live schema/mapping from the source via Trino
// and caches it in postgres-meta for the AI Engine to use.
func (h *DataSourceHandler) HandleRefreshSchema(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	result, err := h.svc.RefreshSchema(id)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error:   "Schema refresh failed",
			Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, result)
}

// POST /api/datasources/refresh-all — refresh schemas for all active sources
func (h *DataSourceHandler) HandleRefreshAll(c *gin.Context) {
	sources, err := h.svc.List()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}

	var results []models.SchemaRefreshResult
	var errors []string
	for _, src := range sources {
		result, err := h.svc.RefreshSchema(src.ID)
		if err != nil {
			errors = append(errors, err.Error())
			continue
		}
		results = append(results, *result)
	}

	c.JSON(http.StatusOK, gin.H{
		"refreshed": results,
		"errors":    errors,
		"message":   "Schema refresh complete",
	})
}
