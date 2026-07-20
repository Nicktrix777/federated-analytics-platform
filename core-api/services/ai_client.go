package services

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/federated-analytics/core-api/models"
)

// AIClient communicates with the AI Engine service.
//
// Architecture boundary:
//   The Core API calls the AI Engine to get a query plan,
//   then VALIDATES that plan before forwarding to the Query Service.
//   The AI Engine never executes queries or touches infrastructure.

type AIClient struct {
	baseURL    string
	httpClient *http.Client
}

func NewAIClient(baseURL string) *AIClient {
	return &AIClient{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 300 * time.Second, // Multi-agent pipeline makes 8-10 LLM calls; allow up to 5 min
		},
	}
}

// PlanRequest is the payload sent to the AI Engine.
//
// Datasets is omitempty: the AI Engine is authoritative for reading the catalog
// from postgres-meta itself, so the query path no longer pushes it. When nil the
// field is omitted and the AI Engine self-sources the catalog.
type PlanRequest struct {
	Question string               `json:"question"`
	Datasets []models.DatasetMeta `json:"datasets,omitempty"`
	// Messages are structured prior turns of the same conversation (Core API
	// assembles these from conversation_turns). The AI Engine renders them to
	// text and injects into the planner prompt so follow-ups resolve against
	// history. Replaces the old flat ConversationContext string (PR4).
	Messages []models.ChatMessage `json:"messages,omitempty"`
}

// DashboardPlanRequest is the payload for AI dashboard generation/refinement.
//
// Datasets is omitempty and no longer populated: like the query path, the AI
// Engine self-loads the catalog from postgres-meta, so the Core API stops
// pushing it. The field remains only so the contract is explicit.
type DashboardPlanRequest struct {
	Prompt           string                 `json:"prompt"`
	Datasets         []models.DatasetMeta   `json:"datasets,omitempty"`
	CurrentDashboard map[string]interface{} `json:"current_dashboard,omitempty"`
}

// planOutcome is the envelope the AI Engine returns from /api/plan (PR5):
// exactly one of Plan or Clarification is set.
type planOutcome struct {
	Plan          *models.QueryPlan      `json:"plan"`
	Clarification *models.Clarification  `json:"clarification"`
	Path          string                 `json:"path"`
}

// GeneratePlan calls the AI Engine to convert a natural language question
// into a structured QueryPlan, or receive a Clarification when the input is
// ambiguous. Exactly one of plan/clarification is non-nil on success.
func (c *AIClient) GeneratePlan(requestID, question string, datasets []models.DatasetMeta, messages []models.ChatMessage) (*models.QueryPlan, *models.Clarification, error) {
	reqBody := PlanRequest{
		Question: question,
		Datasets: datasets,
		Messages: messages,
	}

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/plan", requestID, reqBody)
	if err != nil {
		return nil, nil, fmt.Errorf("AI engine request failed: %w", err)
	}

	var outcome planOutcome
	if err := decodeJSON(resp, "AI engine", &outcome); err != nil {
		return nil, nil, err
	}
	if outcome.Clarification != nil {
		return nil, outcome.Clarification, nil
	}
	if outcome.Plan == nil {
		return nil, nil, fmt.Errorf("AI engine returned neither a plan nor a clarification")
	}
	return outcome.Plan, nil, nil
}

// StreamPlan opens the AI Engine's streaming plan endpoint and invokes
// onEvent for every SSE frame (pipeline progress, heartbeats, and the
// terminal plan/error event). The caller decides what to forward and consumes
// the terminal event itself. Returns transport-level errors, an HTTP error
// status, or the first error returned by onEvent.
func (c *AIClient) StreamPlan(
	requestID, question string,
	datasets []models.DatasetMeta,
	messages []models.ChatMessage,
	onEvent func(SSEEvent) error,
) error {
	reqBody := PlanRequest{
		Question: question,
		Datasets: datasets,
		Messages: messages,
	}
	return c.streamSSE("/api/plan/stream", requestID, reqBody, onEvent)
}

// GenerateDashboardPlan asks the AI Engine's dashboard designer for a full
// dashboard proposal. Pass current != nil to refine an existing dashboard
// with a natural-language instruction instead of creating one from scratch.
func (c *AIClient) GenerateDashboardPlan(
	requestID, prompt string,
	current *models.Dashboard,
) (*models.DashboardPlan, error) {
	reqBody := buildDashboardPlanRequest(prompt, current)

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/dashboard-plan", requestID, reqBody)
	if err != nil {
		return nil, fmt.Errorf("AI engine request failed: %w", err)
	}

	var plan models.DashboardPlan
	if err := decodeJSON(resp, "AI engine", &plan); err != nil {
		return nil, err
	}
	return &plan, nil
}

// StreamDashboardPlan is the streaming counterpart of GenerateDashboardPlan:
// same request body, but pipeline progress arrives as SSE frames passed to
// onEvent, terminating with a dashboard_plan or error event.
func (c *AIClient) StreamDashboardPlan(
	requestID, prompt string,
	current *models.Dashboard,
	onEvent func(SSEEvent) error,
) error {
	reqBody := buildDashboardPlanRequest(prompt, current)
	return c.streamSSE("/api/dashboard-plan/stream", requestID, reqBody, onEvent)
}

// buildDashboardPlanRequest assembles the designer payload shared by the
// blocking and streaming dashboard-plan calls.
func buildDashboardPlanRequest(
	prompt string,
	current *models.Dashboard,
) DashboardPlanRequest {
	reqBody := DashboardPlanRequest{
		Prompt: prompt,
	}
	if current != nil {
		// Send only what the designer needs — no IDs or timestamps.
		widgets := make([]map[string]interface{}, 0, len(current.Widgets))
		for _, w := range current.Widgets {
			widgets = append(widgets, map[string]interface{}{
				"title":         w.Title,
				"sql":           w.QuerySQL,
				"chart_type":    w.ChartType,
				"grid_position": json.RawMessage(w.GridPosition),
			})
		}
		reqBody.CurrentDashboard = map[string]interface{}{
			"name":        current.Name,
			"description": current.Description,
			"widgets":     widgets,
		}
	}
	return reqBody
}

// ReportPlanRequest is the payload for AI report generation/refinement.
// Like DashboardPlanRequest, the AI Engine self-loads the catalog — the Core
// API only sends the brief and (when refining) the report as it exists now.
type ReportPlanRequest struct {
	Prompt        string                 `json:"prompt"`
	CurrentReport map[string]interface{} `json:"current_report,omitempty"`
}

// GenerateReportPlan asks the AI Engine's report designer for a full Excel
// report proposal. Pass current != nil to refine an existing report with a
// natural-language instruction instead of creating one from scratch.
func (c *AIClient) GenerateReportPlan(
	requestID, prompt string,
	current *models.Report,
) (*models.ReportPlan, error) {
	reqBody := buildReportPlanRequest(prompt, current)

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/report-plan", requestID, reqBody)
	if err != nil {
		return nil, fmt.Errorf("AI engine request failed: %w", err)
	}

	var plan models.ReportPlan
	if err := decodeJSON(resp, "AI engine", &plan); err != nil {
		return nil, err
	}
	return &plan, nil
}

// StreamReportPlan is the streaming counterpart of GenerateReportPlan:
// same request body, but pipeline progress arrives as SSE frames passed to
// onEvent, terminating with a report_plan or error event.
func (c *AIClient) StreamReportPlan(
	requestID, prompt string,
	current *models.Report,
	onEvent func(SSEEvent) error,
) error {
	reqBody := buildReportPlanRequest(prompt, current)
	return c.streamSSE("/api/report-plan/stream", requestID, reqBody, onEvent)
}

// buildReportPlanRequest assembles the designer payload shared by the
// blocking and streaming report-plan calls.
func buildReportPlanRequest(
	prompt string,
	current *models.Report,
) ReportPlanRequest {
	reqBody := ReportPlanRequest{
		Prompt: prompt,
	}
	if current != nil {
		// Send only what the designer needs — no IDs or timestamps.
		sheets := make([]map[string]interface{}, 0, len(current.Sheets))
		for _, sh := range current.Sheets {
			columnFormats := sh.ColumnFormats
			if columnFormats == "" {
				columnFormats = "{}"
			}
			sheets = append(sheets, map[string]interface{}{
				"title":          sh.Title,
				"description":    sh.Description,
				"sql":            sh.QuerySQL,
				"column_formats": json.RawMessage(columnFormats),
				"position":       sh.Position,
			})
		}
		reqBody.CurrentReport = map[string]interface{}{
			"name":        current.Name,
			"description": current.Description,
			"sheets":      sheets,
		}
	}
	return reqBody
}

// streamSSE POSTs payload to the AI Engine and parses the SSE response,
// passing each frame to onEvent. The 300s client timeout bounds the whole
// stream — the same budget the blocking pipeline endpoints already have.
func (c *AIClient) streamSSE(
	path, requestID string,
	payload interface{},
	onEvent func(SSEEvent) error,
) error {
	resp, err := postJSON(c.httpClient, c.baseURL+path, requestID, payload)
	if err != nil {
		return fmt.Errorf("AI engine request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBytes, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("AI engine returned %d: %s", resp.StatusCode, string(respBytes))
	}
	return parseSSEStream(resp.Body, onEvent)
}

// RepairWidgetRequest is the payload for fixing a single query or widget.
//
// mode="error" (default): SQL failed to execute — fix the broken identifiers.
// mode="zero_rows": SQL ran fine but returned no rows — correct filter literals.
type RepairWidgetRequest struct {
	SQL       string               `json:"sql"`
	Error     string               `json:"error"`
	ChartType string               `json:"chart_type"`
	Title     string               `json:"title"`
	Datasets  []models.DatasetMeta `json:"datasets,omitempty"`
	Mode      string               `json:"mode,omitempty"`
}

type repairWidgetResponse struct {
	SQL string `json:"sql"`
}

// RepairWidgetSQL asks the AI Engine to correct a query. mode selects the
// repair variant: "error" (default) fixes execution failures; "zero_rows"
// corrects filter literals when a query ran cleanly but returned no rows.
// The caller must re-verify the returned SQL — the AI Engine never executes.
func (c *AIClient) RepairWidgetSQL(
	requestID, sql, execErr, chartType, title, mode string,
) (string, error) {
	reqBody := RepairWidgetRequest{
		SQL:       sql,
		Error:     execErr,
		ChartType: chartType,
		Title:     title,
		Mode:      mode,
	}

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/repair-widget", requestID, reqBody)
	if err != nil {
		return "", fmt.Errorf("AI engine repair request failed: %w", err)
	}

	var out repairWidgetResponse
	if err := decodeJSON(resp, "AI engine repair", &out); err != nil {
		return "", err
	}
	if strings.TrimSpace(out.SQL) == "" {
		return "", fmt.Errorf("AI engine repair returned empty SQL")
	}
	return out.SQL, nil
}

// GetLLMSettings proxies the AI Engine's runtime LLM configuration (the
// non-secret, UI-editable fields plus which provider keys are set). The status
// and body are returned raw so the handler can relay them verbatim — the AI
// Engine is authoritative for the shape and for validation.
func (c *AIClient) GetLLMSettings(requestID string) (int, []byte, error) {
	return requestJSONRaw(c.httpClient, http.MethodGet, c.baseURL+"/api/llm-settings", requestID, nil)
}

// UpdateLLMSettings proxies a settings change to the AI Engine, which validates
// it, persists it, bumps its version (triggering a hot-reload of the provider/
// agent stack), and returns the applied config. API keys are never included —
// they stay in the AI Engine's environment. A validation error comes back as a
// 400 with detail, relayed unchanged.
func (c *AIClient) UpdateLLMSettings(requestID string, payload interface{}) (int, []byte, error) {
	return requestJSONRaw(c.httpClient, http.MethodPut, c.baseURL+"/api/llm-settings", requestID, payload)
}
