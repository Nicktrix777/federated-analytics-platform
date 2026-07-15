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

// PlanRequest is the payload sent to the AI Engine
type PlanRequest struct {
	Question string               `json:"question"`
	Datasets []models.DatasetMeta `json:"datasets"`
	// ConversationContext is a pre-rendered block of prior turns in the same
	// conversation (empty when there's no multi-turn context). The AI Engine
	// injects it into the planner prompt so follow-ups resolve against history.
	ConversationContext string `json:"conversation_context,omitempty"`
}

// DashboardPlanRequest is the payload for AI dashboard generation/refinement.
type DashboardPlanRequest struct {
	Prompt           string                 `json:"prompt"`
	Datasets         []models.DatasetMeta   `json:"datasets"`
	CurrentDashboard map[string]interface{} `json:"current_dashboard,omitempty"`
}

// GeneratePlan calls the AI Engine to convert a natural language question
// into a structured QueryPlan.
func (c *AIClient) GeneratePlan(requestID, question string, datasets []models.DatasetMeta, conversationContext string) (*models.QueryPlan, error) {
	reqBody := PlanRequest{
		Question:            question,
		Datasets:            datasets,
		ConversationContext: conversationContext,
	}

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/plan", requestID, reqBody)
	if err != nil {
		return nil, fmt.Errorf("AI engine request failed: %w", err)
	}

	var plan models.QueryPlan
	if err := decodeJSON(resp, "AI engine", &plan); err != nil {
		return nil, err
	}
	return &plan, nil
}

// StreamPlan opens the AI Engine's streaming plan endpoint and invokes
// onEvent for every SSE frame (pipeline progress, heartbeats, and the
// terminal plan/error event). The caller decides what to forward and consumes
// the terminal event itself. Returns transport-level errors, an HTTP error
// status, or the first error returned by onEvent.
func (c *AIClient) StreamPlan(
	requestID, question string,
	datasets []models.DatasetMeta,
	conversationContext string,
	onEvent func(SSEEvent) error,
) error {
	reqBody := PlanRequest{
		Question:            question,
		Datasets:            datasets,
		ConversationContext: conversationContext,
	}
	return c.streamSSE("/api/plan/stream", requestID, reqBody, onEvent)
}

// GenerateDashboardPlan asks the AI Engine's dashboard designer for a full
// dashboard proposal. Pass current != nil to refine an existing dashboard
// with a natural-language instruction instead of creating one from scratch.
func (c *AIClient) GenerateDashboardPlan(
	requestID, prompt string,
	datasets []models.DatasetMeta,
	current *models.Dashboard,
) (*models.DashboardPlan, error) {
	reqBody := buildDashboardPlanRequest(prompt, datasets, current)

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
	datasets []models.DatasetMeta,
	current *models.Dashboard,
	onEvent func(SSEEvent) error,
) error {
	reqBody := buildDashboardPlanRequest(prompt, datasets, current)
	return c.streamSSE("/api/dashboard-plan/stream", requestID, reqBody, onEvent)
}

// buildDashboardPlanRequest assembles the designer payload shared by the
// blocking and streaming dashboard-plan calls.
func buildDashboardPlanRequest(
	prompt string,
	datasets []models.DatasetMeta,
	current *models.Dashboard,
) DashboardPlanRequest {
	reqBody := DashboardPlanRequest{
		Prompt:   prompt,
		Datasets: datasets,
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

// RepairWidgetRequest is the payload for fixing a single widget query that
// failed to execute against Trino.
type RepairWidgetRequest struct {
	SQL       string               `json:"sql"`
	Error     string               `json:"error"`
	ChartType string               `json:"chart_type"`
	Title     string               `json:"title"`
	Datasets  []models.DatasetMeta `json:"datasets"`
}

type repairWidgetResponse struct {
	SQL string `json:"sql"`
}

// RepairWidgetSQL asks the AI Engine to correct a widget query that failed to
// execute, given the exact engine error and the schema context. It returns the
// repaired SQL — which the caller must still re-verify against the Query
// Service, since the AI Engine never executes anything itself.
func (c *AIClient) RepairWidgetSQL(
	requestID, sql, execErr, chartType, title string,
	datasets []models.DatasetMeta,
) (string, error) {
	reqBody := RepairWidgetRequest{
		SQL:       sql,
		Error:     execErr,
		ChartType: chartType,
		Title:     title,
		Datasets:  datasets,
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

// InvalidateCache tells the AI Engine to drop its schema metadata cache so
// newly uploaded tables and refreshed schemas appear in the next NL→SQL
// prompt. Best-effort: failures are ignored — the cache expires naturally.
func (c *AIClient) InvalidateCache() {
	if c.baseURL == "" {
		return
	}
	// Short dedicated timeout — this is fire-and-forget housekeeping and must
	// not hold callers (upload, schema refresh) for the full pipeline budget.
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(c.baseURL+"/api/invalidate-cache", "application/json", strings.NewReader("{}"))
	if err != nil {
		return
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
}
