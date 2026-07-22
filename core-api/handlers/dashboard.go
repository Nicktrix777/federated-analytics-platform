package handlers

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// maxWidgetRepairAttempts bounds how many times we ask the AI Engine to fix a
// widget query that fails to execute before giving up and dropping the widget.
const maxWidgetRepairAttempts = 2

// maxParallelVerify bounds how many widgets/sheets are verified against Trino
// at once — verification wall time collapses from sum(latency) to max(latency)
// up to this width, without opening so many concurrent connections that we
// just move the bottleneck onto the Query Service/Trino.
const maxParallelVerify = 6

// DashboardHandler provides CRUD for dashboards and their widgets, plus the
// AI flow: generate a dashboard from a natural-language brief and refine an
// existing one with natural-language instructions.

type DashboardHandler struct {
	svc         *services.DashboardService
	aiClient    *services.AIClient
	queryClient *services.QueryClient
	aiEnabled   bool
}

func NewDashboardHandler(
	svc *services.DashboardService,
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	aiEnabled bool,
) *DashboardHandler {
	return &DashboardHandler{
		svc:         svc,
		aiClient:    aiClient,
		queryClient: queryClient,
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

	resp, err := h.persistGeneratedDashboard(plan)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to persist dashboard", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, resp)
}

// POST /api/dashboards/generate/stream
// SSE variant of HandleGenerate (see docs/sse-events.md): proxies the AI
// Engine's pipeline events, emits per-widget verification progress, and ends
// with a terminal `dashboard` event carrying the same JSON as HandleGenerate.
func (h *DashboardHandler) HandleGenerateStream(c *gin.Context) {
	var req models.GenerateDashboardRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	h.streamDashboardFlow(c, req.Prompt, nil, h.persistGeneratedDashboard)
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

	resp, err := h.persistRefinedDashboard(id, plan, current)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to persist dashboard", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, resp)
}

// POST /api/dashboards/:id/refine/stream
// SSE variant of HandleRefine — same events as HandleGenerateStream, ending
// with a terminal `dashboard` event carrying the same JSON as HandleRefine.
func (h *DashboardHandler) HandleRefineStream(c *gin.Context) {
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

	h.streamDashboardFlow(c, req.Instruction, current, func(plan *models.DashboardPlan) (gin.H, error) {
		return h.persistRefinedDashboard(id, plan, current)
	})
}

// planDashboard runs the shared AI-plan step: call the AI Engine (which
// self-loads the catalog) and enforce the SQL validation boundary on every
// proposed widget. On failure it writes the HTTP error response and returns
// ok=false.
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

	requestID := middleware.RequestIDFromContext(c)
	plan, err := h.aiClient.GenerateDashboardPlan(requestID, prompt, current)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error: "AI Engine failed to generate dashboard plan", Details: err.Error(),
		})
		return nil, false
	}

	if err := h.validatePlanWidgets(requestID, plan, nil); err != nil {
		c.JSON(http.StatusUnprocessableEntity, models.ErrorResponse{Error: err.Error()})
		return nil, false
	}
	return plan, true
}

// streamDashboardFlow is the shared SSE pipeline behind the generate/refine
// streaming endpoints: proxy the AI Engine's dashboard-plan stream, consume
// the terminal dashboard_plan event, verify/repair every widget (emitting
// `widget` progress events), persist via persist, and emit the terminal
// `dashboard` event with the same JSON the non-streaming endpoint returns.
//
// Failures before the stream opens (AI disabled) return plain JSON errors;
// afterwards every failure is a terminal `error` event.
func (h *DashboardHandler) streamDashboardFlow(
	c *gin.Context,
	prompt string,
	current *models.Dashboard,
	persist func(*models.DashboardPlan) (gin.H, error),
) {
	if !h.aiEnabled {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "AI mode is disabled",
			Details: "Set AI_ENABLED=true to use AI dashboard generation",
		})
		return
	}

	requestID := middleware.RequestIDFromContext(c)
	sse := newSSEStream(c, requestID)

	_, terminal, ok := proxyAIStream(sse, []string{"dashboard_plan"}, func(onEvent func(services.SSEEvent) error) error {
		return h.aiClient.StreamDashboardPlan(requestID, prompt, current, onEvent)
	})
	if !ok {
		return
	}

	var planEvent struct {
		Plan *models.DashboardPlan `json:"plan"`
	}
	if err := json.Unmarshal(terminal, &planEvent); err != nil || planEvent.Plan == nil {
		sse.emitError("AI Engine sent a malformed dashboard_plan event", http.StatusBadGateway)
		return
	}
	plan := planEvent.Plan

	progress := func(title, status string, attempt int, detail string) {
		payload := gin.H{"title": title, "status": status}
		if attempt > 0 {
			payload["attempt"] = attempt
		}
		if detail != "" {
			payload["detail"] = detail
		}
		sse.emit("widget", payload)
	}
	if err := h.validatePlanWidgets(requestID, plan, progress); err != nil {
		sse.emitError(err.Error(), http.StatusUnprocessableEntity)
		return
	}

	resp, err := persist(plan)
	if err != nil {
		sse.emitError("Failed to persist dashboard: "+err.Error(), http.StatusInternalServerError)
		return
	}
	sse.emit("dashboard", resp)
}

// widgetProgress reports per-widget verification progress for the streaming
// endpoints. status is one of "verifying", "repairing", "ok", "dropped";
// attempt is set only while repairing, detail only when repairing/dropped.
type widgetProgress func(title, status string, attempt int, detail string)

// validatePlanWidgets enforces the SQL validation boundary on every widget
// the AI proposed, mutating plan in place (surviving widgets keep their —
// possibly repaired — SQL; the rest move to plan.DroppedWidgets). A non-nil
// error means no widget survived and the plan must not be persisted.
//
// ── VALIDATION BOUNDARY ──────────────────────────────────
// Two gates, in order:
//  1. Static safety — same gate as /api/query: no AI-generated SQL is
//     persisted unchecked (SELECT-only, no destructive keywords).
//  2. Executable check — actually run each widget's SQL against Trino, the
//     same call the dashboard UI makes. AI-generated SQL that references a
//     hallucinated column/table passes gate 1 but fails here; rather than
//     persist it and let the widget error in the UI, we send it back to the
//     AI Engine to repair, re-verify, and drop it only if it still cannot
//     run. This is what guarantees no created dashboard shows a
//     broken-query widget.
//
// ── VERIFY/REPAIR SHAPE (batched-repair driver) ──────────
// Two phases instead of one inline per-widget loop:
//
//  1. Probe every candidate ONCE, concurrently (bounded to
//     maxParallelVerify). No repair yet.
//  2. Barrier: every Phase-1 failure becomes one item in a single
//     RepairWidgetsBatch call — one LLM round trip fixes every broken
//     widget instead of N independent /api/repair-widget calls.
//  3. Phase 2 (also bounded, concurrent): re-probe whatever the batch
//     changed. A widget the batch didn't fix (batch call errored, no
//     result came back for its title, or the fix still doesn't run) falls
//     back to the existing single-item verifyAndRepairWidget retry loop —
//     that's the "keep the single-item /api/repair-widget for Core API's
//     residual per-item path" carve-out.
//
// Every surviving widget's outcome also carries its final successful probe's
// (columns, rows) — resolveChartType below uses that shape to validate the
// AI's proposed chart_type.
func (h *DashboardHandler) validatePlanWidgets(
	requestID string,
	plan *models.DashboardPlan,
	progress widgetProgress,
) error {
	if progress == nil {
		progress = func(string, string, int, string) {}
	}

	// Static safety gate first — cheap, no I/O, stays sequential so dropped-
	// widget ordering for safety failures is unaffected by the parallel probe
	// step below.
	candidates := make([]models.WidgetPlan, 0, len(plan.Widgets))
	for _, w := range plan.Widgets {
		if err := validateRawSQL(w.SQL); err != nil {
			log.Printf("dashboard: dropping widget %q — failed safety check: %v", w.Title, err)
			reason := "The generated query failed a safety check: " + err.Error()
			plan.DroppedWidgets = append(plan.DroppedWidgets, models.DroppedWidget{
				Title: w.Title, Reason: reason,
			})
			progress(w.Title, "dropped", 0, reason)
			continue
		}
		candidates = append(candidates, w)
	}

	// ── Phase 1: probe every candidate once, concurrently (bounded) ────────
	probes := make([]probeOutcome, len(candidates))
	{
		sem := make(chan struct{}, maxParallelVerify)
		var wg sync.WaitGroup
		for i, w := range candidates {
			wg.Add(1)
			go func(i int, w models.WidgetPlan) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				progress(w.Title, "verifying", 0, "")
				resp, err := h.verifyWidgetSQL(requestID, w.SQL)
				probes[i] = probeOutcome{resp: resp, err: err}
			}(i, w)
		}
		wg.Wait()
	}

	// ── Barrier: one batched repair call for every Phase-1 failure ──────────
	var failingIdx []int
	items := make([]services.RepairBatchItem, 0)
	for i, p := range probes {
		if p.err == nil {
			continue
		}
		failingIdx = append(failingIdx, i)
		items = append(items, services.RepairBatchItem{
			Title:     candidates[i].Title,
			ChartType: candidates[i].ChartType,
			SQL:       candidates[i].SQL,
			Error:     p.err.Error(),
			Mode:      "error",
		})
		progress(candidates[i].Title, "repairing", 1, p.err.Error())
	}

	var batchResults []services.RepairBatchResultItem
	if len(items) > 0 {
		var batchErr error
		batchResults, batchErr = h.aiClient.RepairWidgetsBatch(requestID, items)
		if batchErr != nil {
			log.Printf("dashboard: batched repair call failed, falling back to per-widget repair for %d widget(s): %v",
				len(items), batchErr)
			batchResults = nil
		}
	}

	// ── Phase 2: apply the batch fix; fall back to the single-item retry
	// loop for anything the batch didn't fix. Bounded, concurrent — each
	// goroutine only ever writes its own index of outcomes, so no lock is
	// needed for that slice; progress() is safe to call concurrently
	// (sseStream serializes writer access).
	outcomes := make([]widgetOutcome, len(candidates))
	for i, p := range probes {
		if p.err == nil {
			w := candidates[i]
			progress(w.Title, "ok", 0, "")
			outcomes[i] = widgetOutcome{widget: &w, resp: p.resp}
		}
	}

	if len(failingIdx) > 0 {
		sem := make(chan struct{}, maxParallelVerify)
		var wg sync.WaitGroup
		for k, i := range failingIdx {
			wg.Add(1)
			go func(k, i int) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				w := candidates[i]
				if result, ok := matchBatchRepairResult(items, batchResults, k); ok && result.Changed {
					if err := validateRawSQL(result.SQL); err != nil {
						log.Printf("dashboard: batch-repaired SQL for widget %q failed safety check: %v", w.Title, err)
					} else if resp, verr := h.verifyWidgetSQL(requestID, result.SQL); verr == nil {
						w.SQL = result.SQL
						progress(w.Title, "ok", 0, "")
						outcomes[i] = widgetOutcome{widget: &w, resp: resp}
						return
					}
				}

				// Batch repair didn't produce a runnable fix — fall back to
				// the existing single-item retry loop for just this straggler.
				fixedSQL, resp, err := h.verifyAndRepairWidget(requestID, w, progress)
				if err != nil {
					log.Printf("dashboard: dropping widget %q — SQL could not be executed after repair: %v", w.Title, err)
					reason := "The query didn't run successfully against the data source: " + err.Error()
					progress(w.Title, "dropped", 0, reason)
					outcomes[i] = widgetOutcome{reason: reason}
					return
				}
				w.SQL = fixedSQL
				progress(w.Title, "ok", 0, "")
				outcomes[i] = widgetOutcome{widget: &w, resp: resp}
			}(k, i)
		}
		wg.Wait()
	}

	valid := make([]models.WidgetPlan, 0, len(candidates))
	for i, o := range outcomes {
		if o.widget != nil {
			w := *o.widget
			var cols []string
			var rows [][]interface{}
			if o.resp != nil {
				cols, rows = o.resp.Columns, o.resp.Rows
			}
			w.ChartType = resolveChartType(w.ChartType, cols, rows)
			valid = append(valid, w)
			continue
		}
		plan.DroppedWidgets = append(plan.DroppedWidgets, models.DroppedWidget{
			Title: candidates[i].Title, Reason: o.reason,
		})
	}
	plan.Widgets = valid
	if len(plan.Widgets) == 0 {
		return errorf("AI Engine produced no widgets whose SQL could be executed")
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
	return nil
}

// widgetOutcome is one candidate widget's result from the verify/repair
// pass — widget is nil when the widget was dropped, in which case reason
// explains why. resp carries the widget's final successful probe (columns +
// sample rows), used by resolveChartType; nil exactly when widget is nil.
type widgetOutcome struct {
	widget *models.WidgetPlan
	resp   *models.ExecuteResponse
	reason string
}

// verifyAndRepairWidget runs a widget's SQL against the Query Service exactly
// as the dashboard UI will. If it executes, the (possibly unchanged) SQL and
// its probe response are returned with a nil error. If it fails, the SQL and
// the engine error are sent to the AI Engine for a focused repair (up to
// maxWidgetRepairAttempts), each attempt re-verified against Trino. Returns a
// non-nil error only when no runnable SQL could be produced — the caller
// then drops the widget and surfaces this error as the reason.
//
// This is the residual single-item repair path: the batched-repair driver in
// validatePlanWidgets calls this only as a fallback for a widget the one
// batched /api/repair-widgets-batch call didn't fix.
func (h *DashboardHandler) verifyAndRepairWidget(
	requestID string,
	w models.WidgetPlan,
	progress widgetProgress,
) (string, *models.ExecuteResponse, error) {
	sql := w.SQL
	resp, execErr := h.verifyWidgetSQL(requestID, sql)
	if execErr == nil {
		return sql, resp, nil
	}

	for attempt := 1; attempt <= maxWidgetRepairAttempts; attempt++ {
		log.Printf("dashboard: widget %q failed to execute (attempt %d/%d), repairing: %v",
			w.Title, attempt, maxWidgetRepairAttempts, execErr)
		progress(w.Title, "repairing", attempt, execErr.Error())

		repaired, err := h.aiClient.RepairWidgetSQL(requestID, sql, execErr.Error(), w.ChartType, w.Title, "error")
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
		resp, execErr = h.verifyWidgetSQL(requestID, sql)
		if execErr == nil {
			log.Printf("dashboard: widget %q repaired successfully on attempt %d", w.Title, attempt)
			return sql, resp, nil
		}
	}

	return "", nil, execErr
}

// verifyWidgetSQL probes a widget's SQL against the Query Service, returning
// the probe response (columns + up-to-25-row sample) and the execution error
// (nil on success). Runs a cheap LIMIT-25 probe (see probeSQL) rather than
// the full query — a syntax/column/table error surfaces identically either
// way, so there's no need to pay for the full result set just to prove the
// query runs. The dashboard UI re-executes the full query itself once the
// widget is persisted; the probe's shape is used here only to validate the
// widget's chart_type (resolveChartType).
func (h *DashboardHandler) verifyWidgetSQL(requestID, sql string) (*models.ExecuteResponse, error) {
	return h.queryClient.Execute(requestID, probeSQL(sql))
}

// persistGeneratedDashboard creates a new dashboard plus its validated
// widgets and returns the response payload shared by the streaming and
// non-streaming generate endpoints.
func (h *DashboardHandler) persistGeneratedDashboard(plan *models.DashboardPlan) (gin.H, error) {
	dashboard, err := h.svc.CreateDashboard(models.CreateDashboardRequest{
		Name:        plan.Name,
		Description: plan.Description,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to create dashboard: %w", err)
	}

	for _, w := range planToWidgetRequests(plan, nil) {
		if _, err := h.svc.CreateWidget(dashboard.ID, w); err != nil {
			return nil, fmt.Errorf("failed to create widget %q: %w", w.Title, err)
		}
	}

	final, err := h.svc.GetDashboard(dashboard.ID)
	if err != nil {
		return nil, err
	}
	return dashboardResponse(final, plan), nil
}

// persistRefinedDashboard applies a validated refinement plan to an existing
// dashboard and returns the response payload shared by the streaming and
// non-streaming refine endpoints. current is the dashboard as it existed
// before the refine — planToWidgetRequests uses it to keep surviving
// widgets' grid_position untouched (see computeGridLayout).
func (h *DashboardHandler) persistRefinedDashboard(id int, plan *models.DashboardPlan, current *models.Dashboard) (gin.H, error) {
	if err := h.svc.ReplaceWidgets(id, planToWidgetRequests(plan, current)); err != nil {
		return nil, fmt.Errorf("failed to apply refined widgets: %w", err)
	}
	if _, err := h.svc.UpdateDashboard(id, models.UpdateDashboardRequest{
		Name:        plan.Name,
		Description: plan.Description,
	}); err != nil {
		return nil, fmt.Errorf("failed to update dashboard: %w", err)
	}

	final, err := h.svc.GetDashboard(id)
	if err != nil {
		return nil, err
	}
	return dashboardResponse(final, plan), nil
}

// dashboardResponse is the JSON payload of both dashboard AI endpoints — the
// non-streaming response body and the terminal `dashboard` SSE event.
func dashboardResponse(final *models.Dashboard, plan *models.DashboardPlan) gin.H {
	return gin.H{
		"dashboard":       final,
		"explanation":     plan.Explanation,
		"confidence":      plan.Confidence,
		"dropped_widgets": plan.DroppedWidgets,
	}
}

// planToWidgetRequests converts validated AI widget plans into create
// requests, assigning deterministic grid positions via computeGridLayout —
// the LLM's own grid_position (whatever prose convention the designer used)
// is never used.
//
// current is nil for the create flow, in which case every widget gets a
// freshly computed position starting at y=0. For refine, current is the
// dashboard as it existed before the refine: a widget whose title matches an
// existing one keeps that widget's exact grid_position (byte-identical —
// this is what makes a refine "stable" from the user's point of view). Only
// genuinely new widgets (title not previously present) get placed by the
// algorithm, appended below the existing layout's current bottom edge.
func planToWidgetRequests(plan *models.DashboardPlan, current *models.Dashboard) []models.CreateWidgetRequest {
	existingPositions, startY := existingWidgetLayout(current)

	gridJSON := make([]string, len(plan.Widgets))
	var newIdx []int
	for i, w := range plan.Widgets {
		if pos, ok := existingPositions[w.Title]; ok {
			gridJSON[i] = pos
		} else {
			newIdx = append(newIdx, i)
		}
	}

	if len(newIdx) > 0 {
		newWidgets := make([]models.WidgetPlan, len(newIdx))
		for k, i := range newIdx {
			newWidgets[k] = plan.Widgets[i]
		}
		computed := computeGridLayout(newWidgets, startY)
		for k, i := range newIdx {
			if b, err := json.Marshal(computed[k]); err == nil {
				gridJSON[i] = string(b)
			}
		}
	}

	reqs := make([]models.CreateWidgetRequest, 0, len(plan.Widgets))
	for i, w := range plan.Widgets {
		reqs = append(reqs, models.CreateWidgetRequest{
			Title:        w.Title,
			QuerySQL:     w.SQL,
			ChartType:    w.ChartType,
			GridPosition: gridJSON[i],
		})
	}
	return reqs
}

// existingWidgetLayout parses current's widgets into a title→grid_position
// map (used to keep survivors' positions byte-identical across a refine)
// plus the current bottom edge (max y+h across every existing widget) —
// used as the start row for any newly-added widgets, so they land below the
// existing layout instead of overlapping it. current == nil (create flow)
// yields an empty map and startY 0.
func existingWidgetLayout(current *models.Dashboard) (positions map[string]string, startY int) {
	positions = make(map[string]string)
	if current == nil {
		return positions, 0
	}
	for _, w := range current.Widgets {
		if w.GridPosition == "" {
			continue
		}
		positions[w.Title] = w.GridPosition

		var gp struct {
			Y int `json:"y"`
			H int `json:"h"`
		}
		if err := json.Unmarshal([]byte(w.GridPosition), &gp); err == nil {
			if bottom := gp.Y + gp.H; bottom > startY {
				startY = bottom
			}
		}
	}
	return positions, startY
}

// gridPosition is the shape persisted into CreateWidgetRequest.GridPosition
// (a JSON blob like {"x":0,"y":0,"w":6,"h":4}).
type gridPosition struct {
	X int `json:"x"`
	Y int `json:"y"`
	W int `json:"w"`
	H int `json:"h"`
}

// gridBand classifies a chart_type into one of the three layout bands
// computeGridLayout packs — order matters: it's also the vertical stacking
// order (KPIs on top, then charts, then tables at the bottom).
func gridBand(chartType string) int {
	switch chartType {
	case "number", "gauge":
		return 0
	case "table":
		return 2
	default: // line, area, bar, pie, scatter, and anything unrecognized
		return 1
	}
}

// computeGridLayout deterministically assigns grid coordinates to widgets,
// replacing whatever the LLM proposed (PR-B2). Widgets are sorted into three
// bands — KPI, chart, table — preserving the designer's ordering within each
// band, then packed row by row:
//
//	KPI band:   w=3 h=2, 4 per row
//	chart band: w=6 h=4, 2 per row (a lone trailing chart widens to w=12)
//	table band: w=12 h=4, 1 per row, stacked at the very bottom
//
// y advances monotonically band by band, so overlap is impossible by
// construction: packing only ever depends on how many widgets survived per
// band, never on any original index, so a dropped widget just leaves fewer
// rows — never a hole.
//
// startY offsets the whole layout downward — refine mode uses this to append
// newly-added widgets below an existing dashboard's current widgets instead
// of restarting at y=0 and overlapping them.
func computeGridLayout(widgets []models.WidgetPlan, startY int) []gridPosition {
	var bands [3][]int // indices into widgets, grouped by band, designer order preserved
	for i, w := range widgets {
		b := gridBand(w.ChartType)
		bands[b] = append(bands[b], i)
	}

	positions := make([]gridPosition, len(widgets))
	y := startY

	// KPI band: w=3 h=2, 4 per row.
	chunk := bands[0]
	for len(chunk) > 0 {
		n := len(chunk)
		if n > 4 {
			n = 4
		}
		for col := 0; col < n; col++ {
			positions[chunk[col]] = gridPosition{X: col * 3, Y: y, W: 3, H: 2}
		}
		y += 2
		chunk = chunk[n:]
	}

	// Chart band: w=6 h=4, 2 per row; a lone trailing chart widens to w=12.
	chunk = bands[1]
	for len(chunk) > 0 {
		if len(chunk) == 1 {
			positions[chunk[0]] = gridPosition{X: 0, Y: y, W: 12, H: 4}
			y += 4
			break
		}
		positions[chunk[0]] = gridPosition{X: 0, Y: y, W: 6, H: 4}
		positions[chunk[1]] = gridPosition{X: 6, Y: y, W: 6, H: 4}
		y += 4
		chunk = chunk[2:]
	}

	// Table band: w=12 h=4, one per row, stacked at the very bottom.
	for _, idx := range bands[2] {
		positions[idx] = gridPosition{X: 0, Y: y, W: 12, H: 4}
		y += 4
	}

	return positions
}

// timeColumnPattern mirrors ResultsChart.tsx's isTimeColumn: a column name
// suggesting a time axis.
var timeColumnPattern = regexp.MustCompile(`(?i)date|year|month|week|time|day|quarter|period`)

// toNumber mirrors ResultsChart.tsx's toNumber coercion: real numbers pass
// through, booleans become 0/1, and strings have thousands separators
// stripped before parsing (Trino NUMERIC/DECIMAL columns often arrive as
// strings over the wire).
func toNumber(v interface{}) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case string:
		cleaned := strings.ReplaceAll(strings.TrimSpace(t), ",", "")
		if cleaned == "" {
			return 0, false
		}
		f, err := strconv.ParseFloat(cleaned, 64)
		return f, err == nil
	default:
		return 0, false
	}
}

// isBlank reports whether a probe cell counts as "no value" for classification.
func isBlank(v interface{}) bool {
	if v == nil {
		return true
	}
	s, ok := v.(string)
	return ok && s == ""
}

// isBooleanColumn mirrors ResultsChart.tsx's isBooleanColumn: every non-null
// value is a bool or the literal strings "true"/"false". Boolean columns are
// excluded from both the numeric and categorical counts entirely, matching
// the frontend classifier.
func isBooleanColumn(rows [][]interface{}, colIdx int) bool {
	seen := false
	for _, r := range rows {
		if colIdx >= len(r) || isBlank(r[colIdx]) {
			continue
		}
		seen = true
		switch t := r[colIdx].(type) {
		case bool:
			continue
		case string:
			if t == "true" || t == "false" {
				continue
			}
			return false
		default:
			return false
		}
	}
	return seen
}

// isNumericColumn mirrors ResultsChart.tsx's isNumericColumn: ≥80% of a
// column's non-null values parse as a number.
func isNumericColumn(rows [][]interface{}, colIdx int) bool {
	nonNull, numeric := 0, 0
	for _, r := range rows {
		if colIdx >= len(r) || isBlank(r[colIdx]) {
			continue
		}
		nonNull++
		if _, ok := toNumber(r[colIdx]); ok {
			numeric++
		}
	}
	if nonNull == 0 {
		return false
	}
	return float64(numeric)/float64(nonNull) >= 0.8
}

// allNonNegative reports whether every non-null value in colIdx is >= 0 —
// part of the pie-shape check (rule 3 in resolveChartType).
func allNonNegative(rows [][]interface{}, colIdx int) bool {
	for _, r := range rows {
		if colIdx >= len(r) || isBlank(r[colIdx]) {
			continue
		}
		if n, ok := toNumber(r[colIdx]); ok && n < 0 {
			return false
		}
	}
	return true
}

// resolveChartType validates the AI-proposed chart_type against the shape of
// the widget's actual query result — columns plus the ≤25-row sample probeSQL
// captured — mirroring frontend/src/components/ResultsChart.tsx's classifier
// (numeric/time-column detection heuristics). Only ever returns one of the 8
// values the chart_type CHECK constraint allows
// (ai-engine/alembic/versions/0001_baseline_schema.py:161): table, bar, line,
// pie, area, scatter, number, gauge — there is no horizontal-bar or
// multi-series enum value, so both collapse to plain "bar" rather than
// inventing one.
//
// Rules, in order — 1/2 are strong shape signals that override any proposed
// type; 3 only ever validates (never forces) a pie proposal; 7 exempts
// area/scatter/gauge (types the frontend classifier has no equivalent for)
// from being re-litigated by the generic bar/table fallback in 4/5/6:
//  1. exactly 1 numeric column and exactly 1 row            → "number"
//  2. a time-flavored column present + ≥1 numeric column     → "line"
//  3. proposed "pie" + 1 categorical + 1 numeric column,
//     2-10 rows, every value in that column ≥ 0              → keep "pie"
//  4. proposed area/scatter/gauge, nothing above contradicted it → unchanged
//  5. any other chartable numeric shape (≤12 rows, >12 rows,
//     or ≥2 numeric columns/"multi-series")                  → "bar"
//  6. nothing chartable                                       → "table"
func resolveChartType(proposed string, columns []string, rows [][]interface{}) string {
	if len(columns) == 0 {
		return proposed
	}

	var numericIdx []int
	hasTimeCol := false
	categoricalCount := 0
	for i, name := range columns {
		if isBooleanColumn(rows, i) {
			continue // excluded from both counts, matching ResultsChart.tsx
		}
		if isNumericColumn(rows, i) {
			numericIdx = append(numericIdx, i)
			continue
		}
		if timeColumnPattern.MatchString(name) {
			hasTimeCol = true
		}
		categoricalCount++
	}

	switch {
	case len(numericIdx) == 1 && len(rows) == 1:
		// Rule 1: unambiguous KPI shape.
		return "number"
	case hasTimeCol && len(numericIdx) >= 1:
		// Rule 2: time series.
		return "line"
	case proposed == "pie" &&
		categoricalCount == 1 && len(numericIdx) == 1 &&
		len(rows) >= 2 && len(rows) <= 10 &&
		allNonNegative(rows, numericIdx[0]):
		// Rule 3: designer proposed pie and the shape backs it up.
		return "pie"
	case proposed == "area" || proposed == "scatter" || proposed == "gauge":
		// Rule 4: nothing above explicitly contradicted these — trust the
		// designer (this is "rule 7" in the PR-B2 spec's original ordering,
		// renumbered here since it's evaluated right after rule 3).
		return proposed
	case len(numericIdx) >= 1:
		// Rule 5: every other chartable numeric shape collapses to bar.
		return "bar"
	default:
		// Rule 6: not chartable at all.
		return "table"
	}
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
