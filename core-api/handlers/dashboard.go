package handlers

import (
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// maxWidgetRepairAttempts bounds how many times we ask the AI Engine to fix a
// widget query that fails to execute before giving up and dropping the widget.
const maxWidgetRepairAttempts = 2

// DashboardHandler provides CRUD for dashboards and their widgets, plus the
// AI flow: generate a dashboard from a natural-language brief and refine an
// existing one with natural-language instructions.

type DashboardHandler struct {
	svc         *services.DashboardService
	aiClient    *services.AIClient
	queryClient *services.QueryClient
	metadataSvc *services.MetadataService
	aiEnabled   bool
}

func NewDashboardHandler(
	svc *services.DashboardService,
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	metadataSvc *services.MetadataService,
	aiEnabled bool,
) *DashboardHandler {
	return &DashboardHandler{
		svc:         svc,
		aiClient:    aiClient,
		queryClient: queryClient,
		metadataSvc: metadataSvc,
		aiEnabled:   aiEnabled,
	}
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

// POST /api/dashboards/generate
// AI flow: analyze registered data sources and build a dashboard from a brief.
func (h *DashboardHandler) HandleGenerate(c *gin.Context) {
	var req models.GenerateDashboardRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	plan, ok := h.planDashboard(c, req.Prompt, nil)
	if !ok {
		return
	}

	dashboard, err := h.svc.CreateDashboard(models.CreateDashboardRequest{
		Name:        plan.Name,
		Description: plan.Description,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create dashboard", Details: err.Error(),
		})
		return
	}

	for _, w := range planToWidgetRequests(plan) {
		if _, err := h.svc.CreateWidget(dashboard.ID, w); err != nil {
			c.JSON(http.StatusInternalServerError, models.ErrorResponse{
				Error: "Failed to create widget", Details: err.Error(),
			})
			return
		}
	}

	final, err := h.svc.GetDashboard(dashboard.ID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	c.JSON(http.StatusCreated, gin.H{
		"dashboard":       final,
		"explanation":     plan.Explanation,
		"confidence":      plan.Confidence,
		"dropped_widgets": plan.DroppedWidgets,
	})
}

// POST /api/dashboards/:id/refine
// AI flow: apply a natural-language instruction to an existing dashboard.
func (h *DashboardHandler) HandleRefine(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.RefineDashboardRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	current, err := h.svc.GetDashboard(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if current == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Dashboard not found"})
		return
	}

	plan, ok := h.planDashboard(c, req.Instruction, current)
	if !ok {
		return
	}

	if err := h.svc.ReplaceWidgets(id, planToWidgetRequests(plan)); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to apply refined widgets", Details: err.Error(),
		})
		return
	}
	if _, err := h.svc.UpdateDashboard(id, models.UpdateDashboardRequest{
		Name:        plan.Name,
		Description: plan.Description,
	}); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update dashboard", Details: err.Error(),
		})
		return
	}

	final, err := h.svc.GetDashboard(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"dashboard":       final,
		"explanation":     plan.Explanation,
		"confidence":      plan.Confidence,
		"dropped_widgets": plan.DroppedWidgets,
	})
}

// planDashboard runs the shared AI-plan step: fetch metadata, call the AI
// Engine, and enforce the SQL validation boundary on every proposed widget.
// On failure it writes the HTTP error response and returns ok=false.
func (h *DashboardHandler) planDashboard(
	c *gin.Context,
	prompt string,
	current *models.Dashboard,
) (*models.DashboardPlan, bool) {
	if !h.aiEnabled {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "AI mode is disabled",
			Details: "Set AI_ENABLED=true to use AI dashboard generation",
		})
		return nil, false
	}

	datasets, err := h.metadataSvc.GetAllDatasets()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to fetch metadata", Details: err.Error(),
		})
		return nil, false
	}

	plan, err := h.aiClient.GenerateDashboardPlan(prompt, datasets, current)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error: "AI Engine failed to generate dashboard plan", Details: err.Error(),
		})
		return nil, false
	}

	// ── VALIDATION BOUNDARY ──────────────────────────────────
	// Two gates, in order:
	//   1. Static safety — same gate as /api/query: no AI-generated SQL is
	//      persisted unchecked (SELECT-only, no destructive keywords).
	//   2. Executable check — actually run each widget's SQL against Trino, the
	//      same call the dashboard UI makes. AI-generated SQL that references a
	//      hallucinated column/table passes gate 1 but fails here; rather than
	//      persist it and let the widget error in the UI, we send it back to the
	//      AI Engine to repair (bounded), re-verify, and drop it only if it
	//      still cannot run. This is what guarantees no created dashboard shows
	//      a broken-query widget.
	valid := plan.Widgets[:0]
	for _, w := range plan.Widgets {
		if err := validateRawSQL(w.SQL); err != nil {
			log.Printf("dashboard: dropping widget %q — failed safety check: %v", w.Title, err)
			plan.DroppedWidgets = append(plan.DroppedWidgets, models.DroppedWidget{
				Title: w.Title, Reason: "The generated query failed a safety check: " + err.Error(),
			})
			continue
		}
		fixedSQL, err := h.verifyAndRepairWidget(w, datasets)
		if err != nil {
			log.Printf("dashboard: dropping widget %q — SQL could not be executed after repair: %v", w.Title, err)
			plan.DroppedWidgets = append(plan.DroppedWidgets, models.DroppedWidget{
				Title: w.Title, Reason: "The query didn't run successfully against the data source: " + err.Error(),
			})
			continue
		}
		w.SQL = fixedSQL
		valid = append(valid, w)
	}
	plan.Widgets = valid
	if len(plan.Widgets) == 0 {
		c.JSON(http.StatusUnprocessableEntity, models.ErrorResponse{
			Error: "AI Engine produced no widgets whose SQL could be executed",
		})
		return nil, false
	}
	if len(plan.DroppedWidgets) > 0 {
		titles := make([]string, len(plan.DroppedWidgets))
		for i, d := range plan.DroppedWidgets {
			titles[i] = fmt.Sprintf("%q", d.Title)
		}
		plan.Explanation = strings.TrimSpace(fmt.Sprintf(
			"%s I wasn't able to add %s — the query kept failing against the data source even after retrying. See the details below.",
			plan.Explanation, strings.Join(titles, ", "),
		))
	}
	return plan, true
}

// verifyAndRepairWidget runs a widget's SQL against the Query Service exactly
// as the dashboard UI will. If it executes, the (possibly unchanged) SQL is
// returned with a nil error. If it fails, the SQL and the engine error are
// sent to the AI Engine for a focused repair (up to maxWidgetRepairAttempts),
// each attempt re-verified against Trino. Returns a non-nil error only when no
// runnable SQL could be produced — the caller then drops the widget and
// surfaces this error as the reason.
func (h *DashboardHandler) verifyAndRepairWidget(
	w models.WidgetPlan,
	datasets []models.DatasetMeta,
) (string, error) {
	sql := w.SQL
	execErr := h.verifyWidgetSQL(sql)
	if execErr == nil {
		return sql, nil
	}

	for attempt := 1; attempt <= maxWidgetRepairAttempts; attempt++ {
		log.Printf("dashboard: widget %q failed to execute (attempt %d/%d), repairing: %v",
			w.Title, attempt, maxWidgetRepairAttempts, execErr)

		repaired, err := h.aiClient.RepairWidgetSQL(sql, execErr.Error(), w.ChartType, w.Title, datasets)
		if err != nil {
			log.Printf("dashboard: repair call failed for widget %q: %v", w.Title, err)
			break
		}
		// A repaired query must still clear the static safety gate before we run it.
		if err := validateRawSQL(repaired); err != nil {
			log.Printf("dashboard: repaired SQL for widget %q failed safety check: %v", w.Title, err)
			execErr = err
			break
		}

		sql = repaired
		execErr = h.verifyWidgetSQL(sql)
		if execErr == nil {
			log.Printf("dashboard: widget %q repaired successfully on attempt %d", w.Title, attempt)
			return sql, nil
		}
	}

	return "", execErr
}

// verifyWidgetSQL executes a widget's SQL against the Query Service, returning
// the execution error (nil on success). This is the same path the UI uses, so
// a query that verifies here is guaranteed to render without a query error.
func (h *DashboardHandler) verifyWidgetSQL(sql string) error {
	_, err := h.queryClient.Execute(sql)
	return err
}

// planToWidgetRequests converts validated AI widget plans into create requests.
func planToWidgetRequests(plan *models.DashboardPlan) []models.CreateWidgetRequest {
	reqs := make([]models.CreateWidgetRequest, 0, len(plan.Widgets))
	for _, w := range plan.Widgets {
		gridPos := ""
		if len(w.GridPosition) > 0 {
			gridPos = string(w.GridPosition)
		}
		reqs = append(reqs, models.CreateWidgetRequest{
			Title:        w.Title,
			QuerySQL:     w.SQL,
			ChartType:    w.ChartType,
			GridPosition: gridPos,
		})
	}
	return reqs
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
