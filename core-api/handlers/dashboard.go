package handlers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// DashboardHandler provides CRUD for dashboards and their widgets.
// Dashboards are saved query layouts users can pin to a custom view.

type DashboardHandler struct {
	svc *services.DashboardService
}

func NewDashboardHandler(svc *services.DashboardService) *DashboardHandler {
	return &DashboardHandler{svc: svc}
}

// GET /api/dashboards
func (h *DashboardHandler) HandleList(c *gin.Context) {
	dashboards, err := h.svc.ListDashboards()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to list dashboards", Details: err.Error(),
		})
		return
	}
	if dashboards == nil {
		dashboards = []models.Dashboard{}
	}
	c.JSON(http.StatusOK, gin.H{"dashboards": dashboards, "count": len(dashboards)})
}

// POST /api/dashboards
func (h *DashboardHandler) HandleCreate(c *gin.Context) {
	var req models.CreateDashboardRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	dashboard, err := h.svc.CreateDashboard(req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create dashboard", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, dashboard)
}

// GET /api/dashboards/:id
func (h *DashboardHandler) HandleGet(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	dashboard, err := h.svc.GetDashboard(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if dashboard == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Dashboard not found"})
		return
	}
	c.JSON(http.StatusOK, dashboard)
}

// PUT /api/dashboards/:id
func (h *DashboardHandler) HandleUpdate(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.UpdateDashboardRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	dashboard, err := h.svc.UpdateDashboard(id, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update dashboard", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, dashboard)
}

// DELETE /api/dashboards/:id
func (h *DashboardHandler) HandleDelete(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	if err := h.svc.DeleteDashboard(id); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to delete dashboard", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Dashboard deleted"})
}

// POST /api/dashboards/:id/widgets
func (h *DashboardHandler) HandleCreateWidget(c *gin.Context) {
	dashID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid dashboard ID"})
		return
	}

	var req models.CreateWidgetRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	widget, err := h.svc.CreateWidget(dashID, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create widget", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, widget)
}

// PUT /api/dashboards/:id/widgets/:wid
func (h *DashboardHandler) HandleUpdateWidget(c *gin.Context) {
	dashID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid dashboard ID"})
		return
	}
	widgetID, err := strconv.Atoi(c.Param("wid"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid widget ID"})
		return
	}

	var req models.UpdateWidgetRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	widget, err := h.svc.UpdateWidget(dashID, widgetID, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update widget", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, widget)
}

// DELETE /api/dashboards/:id/widgets/:wid
func (h *DashboardHandler) HandleDeleteWidget(c *gin.Context) {
	dashID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid dashboard ID"})
		return
	}
	widgetID, err := strconv.Atoi(c.Param("wid"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid widget ID"})
		return
	}

	if err := h.svc.DeleteWidget(dashID, widgetID); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to delete widget", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Widget deleted"})
}
