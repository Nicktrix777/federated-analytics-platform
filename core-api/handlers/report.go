package handlers

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// maxSheetRepairAttempts bounds how many times we ask the AI Engine to fix a
// sheet query that fails to execute before giving up and dropping the sheet.
const maxSheetRepairAttempts = 2

// reportDownloadDeadline bounds the whole download: every sheet query must
// run inside this window (mirrors queryDeadlineSeconds; the 430s server
// WriteTimeout is the hard ceiling above it). Sheets that miss the window
// land in the workbook as errors rather than failing the download.
const reportDownloadDeadline = 360 * time.Second

const xlsxContentType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

// ReportHandler provides CRUD for reports and their sheets, the AI flow
// (generate a report from a natural-language brief, refine with
// instructions), and the on-demand Excel download.

type ReportHandler struct {
	svc         *services.ReportService
	aiClient    *services.AIClient
	queryClient *services.QueryClient
	aiEnabled   bool
}

func NewReportHandler(
	svc *services.ReportService,
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	aiEnabled bool,
) *ReportHandler {
	return &ReportHandler{
		svc:         svc,
		aiClient:    aiClient,
		queryClient: queryClient,
		aiEnabled:   aiEnabled,
	}
}

// GET /api/reports
func (h *ReportHandler) HandleList(c *gin.Context) {
	reports, err := h.svc.ListReports()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to list reports", Details: err.Error(),
		})
		return
	}
	if reports == nil {
		reports = []models.Report{}
	}
	c.JSON(http.StatusOK, gin.H{"reports": reports, "count": len(reports)})
}

// POST /api/reports
func (h *ReportHandler) HandleCreate(c *gin.Context) {
	var req models.CreateReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	report, err := h.svc.CreateReport(req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create report", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, report)
}

// GET /api/reports/:id
func (h *ReportHandler) HandleGet(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	report, err := h.svc.GetReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if report == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Report not found"})
		return
	}
	c.JSON(http.StatusOK, report)
}

// PUT /api/reports/:id
func (h *ReportHandler) HandleUpdate(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.UpdateReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	report, err := h.svc.UpdateReport(id, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update report", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, report)
}

// DELETE /api/reports/:id
func (h *ReportHandler) HandleDelete(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	if err := h.svc.DeleteReport(id); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to delete report", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Report deleted"})
}

// POST /api/reports/generate
// AI flow: analyze registered data sources and build a report from a brief.
func (h *ReportHandler) HandleGenerate(c *gin.Context) {
	var req models.GenerateReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	plan, ok := h.planReport(c, req.Prompt, nil)
	if !ok {
		return
	}

	resp, err := h.persistGeneratedReport(plan)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to persist report", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, resp)
}

// POST /api/reports/generate/stream
// SSE variant of HandleGenerate (see docs/sse-events.md): proxies the AI
// Engine's pipeline events, emits per-sheet verification progress, and ends
// with a terminal `report` event carrying the same JSON as HandleGenerate.
func (h *ReportHandler) HandleGenerateStream(c *gin.Context) {
	var req models.GenerateReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	h.streamReportFlow(c, req.Prompt, nil, h.persistGeneratedReport)
}

// POST /api/reports/:id/refine
// AI flow: apply a natural-language instruction to an existing report.
func (h *ReportHandler) HandleRefine(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.RefineReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	current, err := h.svc.GetReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if current == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Report not found"})
		return
	}

	plan, ok := h.planReport(c, req.Instruction, current)
	if !ok {
		return
	}

	resp, err := h.persistRefinedReport(id, plan)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to persist report", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, resp)
}

// POST /api/reports/:id/refine/stream
// SSE variant of HandleRefine — same events as HandleGenerateStream, ending
// with a terminal `report` event carrying the same JSON as HandleRefine.
func (h *ReportHandler) HandleRefineStream(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	var req models.RefineReportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	current, err := h.svc.GetReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if current == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Report not found"})
		return
	}

	h.streamReportFlow(c, req.Instruction, current, func(plan *models.ReportPlan) (gin.H, error) {
		return h.persistRefinedReport(id, plan)
	})
}

// GET /api/reports/:id/download
// Executes every sheet's SQL live (nothing is cached or persisted) and
// streams the formatted workbook. A failing sheet becomes an error worksheet
// instead of failing the download — a partial report beats no report.
func (h *ReportHandler) HandleDownload(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid ID"})
		return
	}

	report, err := h.svc.GetReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{Error: err.Error()})
		return
	}
	if report == nil {
		c.JSON(http.StatusNotFound, models.ErrorResponse{Error: "Report not found"})
		return
	}
	if len(report.Sheets) == 0 {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error: "Report has no sheets", Details: "Add at least one sheet before downloading",
		})
		return
	}

	requestID := middleware.RequestIDFromContext(c)
	deadline := time.Now().Add(reportDownloadDeadline)

	results := make([]services.SheetResult, 0, len(report.Sheets))
	for _, sheet := range report.Sheets {
		res := services.SheetResult{Sheet: sheet}
		switch {
		case time.Now().After(deadline):
			res.Err = "skipped: report download deadline exceeded"
		default:
			exec, execErr := h.queryClient.Execute(requestID, sheet.QuerySQL)
			if execErr != nil {
				log.Printf("report: sheet %q failed during download: %v", sheet.Title, execErr)
				res.Err = execErr.Error()
			} else {
				res.Columns = exec.Columns
				res.Rows = exec.Rows
				res.TotalRows = exec.RowCount
				if sheet.MaxRows > 0 && len(res.Rows) > sheet.MaxRows {
					res.Rows = res.Rows[:sheet.MaxRows]
				}
			}
		}
		results = append(results, res)
	}

	buf, err := services.BuildReportWorkbook(report, results)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to build workbook", Details: err.Error(),
		})
		return
	}

	filename := fmt.Sprintf("%s-%s.xlsx", slugifyFilename(report.Name), time.Now().UTC().Format("20060102-150405"))
	c.Header("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filename))
	c.Data(http.StatusOK, xlsxContentType, buf.Bytes())
}

// planReport runs the shared AI-plan step: call the AI Engine (which
// self-loads the catalog) and enforce the SQL validation boundary on every
// proposed sheet. On failure it writes the HTTP error response and returns
// ok=false.
func (h *ReportHandler) planReport(
	c *gin.Context,
	prompt string,
	current *models.Report,
) (*models.ReportPlan, bool) {
	if !h.aiEnabled {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "AI mode is disabled",
			Details: "Set AI_ENABLED=true to use AI report generation",
		})
		return nil, false
	}

	requestID := middleware.RequestIDFromContext(c)
	plan, err := h.aiClient.GenerateReportPlan(requestID, prompt, current)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error: "AI Engine failed to generate report plan", Details: err.Error(),
		})
		return nil, false
	}

	if err := h.validatePlanSheets(requestID, plan, nil); err != nil {
		c.JSON(http.StatusUnprocessableEntity, models.ErrorResponse{Error: err.Error()})
		return nil, false
	}
	return plan, true
}

// streamReportFlow is the shared SSE pipeline behind the generate/refine
// streaming endpoints: proxy the AI Engine's report-plan stream, consume the
// terminal report_plan event, verify/repair every sheet (emitting `sheet`
// progress events), persist via persist, and emit the terminal `report` event
// with the same JSON the non-streaming endpoint returns.
//
// Failures before the stream opens (AI disabled) return plain JSON errors;
// afterwards every failure is a terminal `error` event.
func (h *ReportHandler) streamReportFlow(
	c *gin.Context,
	prompt string,
	current *models.Report,
	persist func(*models.ReportPlan) (gin.H, error),
) {
	if !h.aiEnabled {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "AI mode is disabled",
			Details: "Set AI_ENABLED=true to use AI report generation",
		})
		return
	}

	requestID := middleware.RequestIDFromContext(c)
	sse := newSSEStream(c, requestID)

	_, terminal, ok := proxyAIStream(sse, []string{"report_plan"}, func(onEvent func(services.SSEEvent) error) error {
		return h.aiClient.StreamReportPlan(requestID, prompt, current, onEvent)
	})
	if !ok {
		return
	}

	var planEvent struct {
		Plan *models.ReportPlan `json:"plan"`
	}
	if err := json.Unmarshal(terminal, &planEvent); err != nil || planEvent.Plan == nil {
		sse.emitError("AI Engine sent a malformed report_plan event", http.StatusBadGateway)
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
		sse.emit("sheet", payload)
	}
	if err := h.validatePlanSheets(requestID, plan, progress); err != nil {
		sse.emitError(err.Error(), http.StatusUnprocessableEntity)
		return
	}

	resp, err := persist(plan)
	if err != nil {
		sse.emitError("Failed to persist report: "+err.Error(), http.StatusInternalServerError)
		return
	}
	sse.emit("report", resp)
}

// sheetProgress reports per-sheet verification progress for the streaming
// endpoints. status is one of "verifying", "repairing", "ok", "dropped";
// attempt is set only while repairing, detail only when repairing/dropped.
type sheetProgress func(title, status string, attempt int, detail string)

// validatePlanSheets enforces the SQL validation boundary on every sheet the
// AI proposed, mutating plan in place (surviving sheets keep their — possibly
// repaired — SQL; the rest move to plan.DroppedSheets). A non-nil error means
// no sheet survived and the plan must not be persisted.
//
// Same two gates as dashboard widgets: the static safety check shared with
// /api/query, then an executable check against Trino. This is what
// guarantees a downloaded report never carries a sheet whose query cannot
// run.
//
// ── VERIFY/REPAIR SHAPE (batched-repair driver) ──────────
// Mirrors DashboardHandler.validatePlanWidgets exactly (see its doc comment
// for the full rationale): Phase 1 probes every candidate once, concurrently;
// every failure becomes one item in a single RepairWidgetsBatch call; Phase 2
// re-probes whatever the batch changed and falls back to the existing
// single-item verifyAndRepairSheet retry loop for anything the batch didn't
// fix. report.go has no chart-type step afterward, but it does have one more
// pass dashboards don't: a batched zero-rows sanity round (repairZeroRowSheets)
// over whatever survives Phase 2 with a genuinely empty result.
func (h *ReportHandler) validatePlanSheets(
	requestID string,
	plan *models.ReportPlan,
	progress sheetProgress,
) error {
	if progress == nil {
		progress = func(string, string, int, string) {}
	}

	// Static safety gate first — cheap, no I/O, stays sequential so dropped-
	// sheet ordering for safety failures is unaffected by the parallel probe
	// step below.
	candidates := make([]models.SheetPlan, 0, len(plan.Sheets))
	for _, sh := range plan.Sheets {
		if err := validateRawSQL(sh.SQL); err != nil {
			log.Printf("report: dropping sheet %q — failed safety check: %v", sh.Title, err)
			reason := "The generated query failed a safety check: " + err.Error()
			plan.DroppedSheets = append(plan.DroppedSheets, models.DroppedSheet{
				Title: sh.Title, Reason: reason,
			})
			progress(sh.Title, "dropped", 0, reason)
			continue
		}
		candidates = append(candidates, sh)
	}

	// ── Phase 1: probe every candidate once, concurrently (bounded) ────────
	probes := make([]probeOutcome, len(candidates))
	{
		sem := make(chan struct{}, maxParallelVerify)
		var wg sync.WaitGroup
		for i, sh := range candidates {
			wg.Add(1)
			go func(i int, sh models.SheetPlan) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				progress(sh.Title, "verifying", 0, "")
				resp, err := h.verifySheetSQL(requestID, sh.SQL)
				probes[i] = probeOutcome{resp: resp, err: err}
			}(i, sh)
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
			Title: candidates[i].Title,
			// Reports have no chart type — "table" keeps the repair prompt's
			// shape-preservation rule aligned with a tabular result.
			ChartType: "table",
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
			log.Printf("report: batched repair call failed, falling back to per-sheet repair for %d sheet(s): %v",
				len(items), batchErr)
			batchResults = nil
		}
	}

	// ── Phase 2: apply the batch fix; fall back to the single-item retry
	// loop for anything the batch didn't fix. Bounded, concurrent — same
	// no-lock-needed rationale as the dashboard widget pass.
	outcomes := make([]sheetOutcome, len(candidates))
	for i, p := range probes {
		if p.err == nil {
			sh := candidates[i]
			progress(sh.Title, "ok", 0, "")
			outcomes[i] = sheetOutcome{sheet: &sh, resp: p.resp}
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

				sh := candidates[i]
				if result, ok := matchBatchRepairResult(items, batchResults, k); ok && result.Changed {
					if err := validateRawSQL(result.SQL); err != nil {
						log.Printf("report: batch-repaired SQL for sheet %q failed safety check: %v", sh.Title, err)
					} else if resp, verr := h.verifySheetSQL(requestID, result.SQL); verr == nil {
						sh.SQL = result.SQL
						progress(sh.Title, "ok", 0, "")
						outcomes[i] = sheetOutcome{sheet: &sh, resp: resp}
						return
					}
				}

				// Batch repair didn't produce a runnable fix — fall back to
				// the existing single-item retry loop for just this straggler.
				fixedSQL, resp, err := h.verifyAndRepairSheet(requestID, sh, progress)
				if err != nil {
					log.Printf("report: dropping sheet %q — SQL could not be executed after repair: %v", sh.Title, err)
					reason := "The query didn't run successfully against the data source: " + err.Error()
					progress(sh.Title, "dropped", 0, reason)
					outcomes[i] = sheetOutcome{reason: reason}
					return
				}
				sh.SQL = fixedSQL
				progress(sh.Title, "ok", 0, "")
				outcomes[i] = sheetOutcome{sheet: &sh, resp: resp}
			}(k, i)
		}
		wg.Wait()
	}

	// ── Phase 3: batched zero-rows sanity pass ──────────────────────────────
	// Report-only — dashboards don't get this per the plan.
	h.repairZeroRowSheets(requestID, outcomes, progress)

	valid := make([]models.SheetPlan, 0, len(candidates))
	for i, o := range outcomes {
		if o.sheet != nil {
			valid = append(valid, *o.sheet)
			continue
		}
		plan.DroppedSheets = append(plan.DroppedSheets, models.DroppedSheet{
			Title: candidates[i].Title, Reason: o.reason,
		})
	}
	plan.Sheets = valid
	if len(plan.Sheets) == 0 {
		return errorf("AI Engine produced no sheets whose SQL could be executed")
	}
	if len(plan.DroppedSheets) > 0 {
		titles := make([]string, len(plan.DroppedSheets))
		for i, d := range plan.DroppedSheets {
			titles[i] = fmt.Sprintf("%q", d.Title)
		}
		plan.Explanation = strings.TrimSpace(fmt.Sprintf(
			"%s I wasn't able to add %s — the query kept failing against the data source even after retrying. See the details below.",
			plan.Explanation, strings.Join(titles, ", "),
		))
	}
	return nil
}

// repairZeroRowSheets runs one batched mode="zero_rows" repair round for
// every surviving sheet (outcomes[i].sheet != nil) whose final Phase-2 probe
// succeeded with RowCount == 0 — mirroring query.go's handleZeroRow
// semantics, batched across sheets instead of the single-item loop the
// chat/query path uses.
//
// Unlike the error-mode passes above, this never drops a sheet: a report
// sheet can legitimately have zero matching rows. The batch response's
// Changed flag does the job query.go's handleZeroRow does with a trimmed
// string compare — Changed == false means the AI Engine is confirming zero
// rows is correct, so the sheet is left exactly as it was. When Changed is
// true, the correction is re-probed and kept only if it now returns rows;
// otherwise the original zero-row result stands.
func (h *ReportHandler) repairZeroRowSheets(
	requestID string,
	outcomes []sheetOutcome,
	progress sheetProgress,
) {
	var zeroIdx []int
	items := make([]services.RepairBatchItem, 0)
	for i, o := range outcomes {
		if o.sheet == nil || o.resp == nil || o.resp.RowCount != 0 {
			continue
		}
		zeroIdx = append(zeroIdx, i)
		items = append(items, services.RepairBatchItem{
			Title:     o.sheet.Title,
			ChartType: "table",
			SQL:       o.sheet.SQL,
			Mode:      "zero_rows",
		})
	}
	if len(items) == 0 {
		return
	}

	results, err := h.aiClient.RepairWidgetsBatch(requestID, items)
	if err != nil {
		log.Printf("report: zero-row batched repair call failed, leaving %d sheet(s) as-is: %v", len(items), err)
		return
	}

	sem := make(chan struct{}, maxParallelVerify)
	var wg sync.WaitGroup
	for k, i := range zeroIdx {
		wg.Add(1)
		go func(k, i int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			sh := *outcomes[i].sheet
			result, ok := matchBatchRepairResult(items, results, k)
			if !ok || !result.Changed {
				// AI confirms zero rows is correct (or the batch had nothing
				// usable for this sheet) — leave the original result in place.
				return
			}
			if err := validateRawSQL(result.SQL); err != nil {
				log.Printf("report: zero-row corrected SQL for sheet %q failed safety check: %v", sh.Title, err)
				return
			}
			resp, execErr := h.verifySheetSQL(requestID, result.SQL)
			if execErr != nil || resp.RowCount == 0 {
				log.Printf("report: zero-row correction for sheet %q did not improve the result (err=%v)", sh.Title, execErr)
				return
			}
			log.Printf("report: zero-row correction for sheet %q produced %d rows", sh.Title, resp.RowCount)
			sh.SQL = result.SQL
			outcomes[i] = sheetOutcome{sheet: &sh, resp: resp}
			progress(sh.Title, "ok", 0, "")
		}(k, i)
	}
	wg.Wait()
}

// sheetOutcome is one candidate sheet's result from the verify/repair pass —
// sheet is nil when the sheet was dropped, in which case reason explains why.
// resp carries the sheet's final successful probe, used by
// repairZeroRowSheets; nil exactly when sheet is nil.
type sheetOutcome struct {
	sheet  *models.SheetPlan
	resp   *models.ExecuteResponse
	reason string
}

// verifyAndRepairSheet runs a sheet's SQL against the Query Service exactly
// as the download will. If it executes, the (possibly unchanged) SQL and its
// probe response are returned with a nil error. If it fails, the SQL and the
// engine error are sent to the AI Engine for a focused repair (up to
// maxSheetRepairAttempts), each attempt re-verified against Trino. Returns a
// non-nil error only when no runnable SQL could be produced — the caller
// then drops the sheet and surfaces this error as the reason.
//
// This is the residual single-item repair path: the batched-repair driver in
// validatePlanSheets calls this only as a fallback for a sheet the one
// batched /api/repair-widgets-batch call didn't fix.
func (h *ReportHandler) verifyAndRepairSheet(
	requestID string,
	sh models.SheetPlan,
	progress sheetProgress,
) (string, *models.ExecuteResponse, error) {
	sql := sh.SQL
	resp, execErr := h.verifySheetSQL(requestID, sql)
	if execErr == nil {
		return sql, resp, nil
	}

	for attempt := 1; attempt <= maxSheetRepairAttempts; attempt++ {
		log.Printf("report: sheet %q failed to execute (attempt %d/%d), repairing: %v",
			sh.Title, attempt, maxSheetRepairAttempts, execErr)
		progress(sh.Title, "repairing", attempt, execErr.Error())

		// Reports have no chart type — "table" keeps the repair prompt's
		// shape-preservation rule aligned with a tabular result.
		repaired, err := h.aiClient.RepairWidgetSQL(requestID, sql, execErr.Error(), "table", sh.Title, "error")
		if err != nil {
			log.Printf("report: repair call failed for sheet %q: %v", sh.Title, err)
			break
		}
		// A repaired query must still clear the static safety gate before we run it.
		if err := validateRawSQL(repaired); err != nil {
			log.Printf("report: repaired SQL for sheet %q failed safety check: %v", sh.Title, err)
			execErr = err
			break
		}

		sql = repaired
		resp, execErr = h.verifySheetSQL(requestID, sql)
		if execErr == nil {
			log.Printf("report: sheet %q repaired successfully on attempt %d", sh.Title, attempt)
			return sql, resp, nil
		}
	}

	return "", nil, execErr
}

// verifySheetSQL probes a sheet's SQL against the Query Service, returning
// the probe response (columns + up-to-25-row sample) and the execution error
// (nil on success). Runs a cheap LIMIT-25 probe (see probeSQL) rather than
// the full query — a syntax/column/table error surfaces identically either
// way. HandleDownload runs the real, unprobed query when the workbook is
// actually built, so a sheet that verifies here is guaranteed to fill its
// worksheet; it just doesn't pay for the full result set twice. The probe's
// RowCount also drives the zero-rows sanity pass (repairZeroRowSheets).
func (h *ReportHandler) verifySheetSQL(requestID, sql string) (*models.ExecuteResponse, error) {
	return h.queryClient.Execute(requestID, probeSQL(sql))
}

// persistGeneratedReport creates a new report plus its validated sheets and
// returns the response payload shared by the streaming and non-streaming
// generate endpoints.
func (h *ReportHandler) persistGeneratedReport(plan *models.ReportPlan) (gin.H, error) {
	report, err := h.svc.CreateReport(models.CreateReportRequest{
		Name:        plan.Name,
		Description: plan.Description,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to create report: %w", err)
	}

	for _, sh := range planToSheetRequests(plan) {
		if _, err := h.svc.CreateSheet(report.ID, sh); err != nil {
			return nil, fmt.Errorf("failed to create sheet %q: %w", sh.Title, err)
		}
	}

	final, err := h.svc.GetReport(report.ID)
	if err != nil {
		return nil, err
	}
	return reportResponse(final, plan), nil
}

// persistRefinedReport applies a validated refinement plan to an existing
// report and returns the response payload shared by the streaming and
// non-streaming refine endpoints.
func (h *ReportHandler) persistRefinedReport(id int, plan *models.ReportPlan) (gin.H, error) {
	if err := h.svc.ReplaceSheets(id, planToSheetRequests(plan)); err != nil {
		return nil, fmt.Errorf("failed to apply refined sheets: %w", err)
	}
	if _, err := h.svc.UpdateReport(id, models.UpdateReportRequest{
		Name:        plan.Name,
		Description: plan.Description,
	}); err != nil {
		return nil, fmt.Errorf("failed to update report: %w", err)
	}

	final, err := h.svc.GetReport(id)
	if err != nil {
		return nil, err
	}
	return reportResponse(final, plan), nil
}

// reportResponse is the JSON payload of both report AI endpoints — the
// non-streaming response body and the terminal `report` SSE event.
func reportResponse(final *models.Report, plan *models.ReportPlan) gin.H {
	return gin.H{
		"report":         final,
		"explanation":    plan.Explanation,
		"confidence":     plan.Confidence,
		"dropped_sheets": plan.DroppedSheets,
	}
}

// planToSheetRequests converts validated AI sheet plans into create requests.
func planToSheetRequests(plan *models.ReportPlan) []models.CreateSheetRequest {
	reqs := make([]models.CreateSheetRequest, 0, len(plan.Sheets))
	for i, sh := range plan.Sheets {
		columnFormats := "{}"
		if len(sh.ColumnFormats) > 0 {
			if b, err := json.Marshal(sh.ColumnFormats); err == nil {
				columnFormats = string(b)
			}
		}
		position := sh.Position
		if position == 0 {
			position = i
		}
		reqs = append(reqs, models.CreateSheetRequest{
			Title:         sh.Title,
			QuerySQL:      sh.SQL,
			Description:   sh.Description,
			ColumnFormats: columnFormats,
			Position:      position,
		})
	}
	return reqs
}

// slugifyFilename reduces a report name to a safe download-filename stem.
func slugifyFilename(name string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(name) {
		switch {
		case r >= 'a' && r <= 'z' || r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == ' ' || r == '-' || r == '_':
			b.WriteRune('-')
		}
	}
	slug := strings.Trim(b.String(), "-")
	if slug == "" {
		slug = "report"
	}
	return slug
}

// POST /api/reports/:id/sheets
func (h *ReportHandler) HandleCreateSheet(c *gin.Context) {
	reportID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid report ID"})
		return
	}

	var req models.CreateSheetRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	sheet, err := h.svc.CreateSheet(reportID, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to create sheet", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusCreated, sheet)
}

// PUT /api/reports/:id/sheets/:sid
func (h *ReportHandler) HandleUpdateSheet(c *gin.Context) {
	reportID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid report ID"})
		return
	}
	sheetID, err := strconv.Atoi(c.Param("sid"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid sheet ID"})
		return
	}

	var req models.UpdateSheetRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	sheet, err := h.svc.UpdateSheet(reportID, sheetID, req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to update sheet", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, sheet)
}

// DELETE /api/reports/:id/sheets/:sid
func (h *ReportHandler) HandleDeleteSheet(c *gin.Context) {
	reportID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid report ID"})
		return
	}
	sheetID, err := strconv.Atoi(c.Param("sid"))
	if err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: "Invalid sheet ID"})
		return
	}

	if err := h.svc.DeleteSheet(reportID, sheetID); err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error: "Failed to delete sheet", Details: err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Sheet deleted"})
}
