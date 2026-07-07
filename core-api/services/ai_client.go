package services

import (
	"bytes"
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
}

// DashboardPlanRequest is the payload for AI dashboard generation/refinement.
type DashboardPlanRequest struct {
	Prompt           string                 `json:"prompt"`
	Datasets         []models.DatasetMeta   `json:"datasets"`
	CurrentDashboard map[string]interface{} `json:"current_dashboard,omitempty"`
}

// GeneratePlan calls the AI Engine to convert a natural language question
// into a structured QueryPlan.
func (c *AIClient) GeneratePlan(question string, datasets []models.DatasetMeta) (*models.QueryPlan, error) {
	reqBody := PlanRequest{
		Question: question,
		Datasets: datasets,
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal AI request: %w", err)
	}

	resp, err := c.httpClient.Post(
		c.baseURL+"/api/plan",
		"application/json",
		bytes.NewBuffer(bodyBytes),
	)
	if err != nil {
		return nil, fmt.Errorf("AI engine request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read AI engine response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("AI engine returned %d: %s", resp.StatusCode, string(respBytes))
	}

	var plan models.QueryPlan
	if err := json.Unmarshal(respBytes, &plan); err != nil {
		return nil, fmt.Errorf("failed to parse AI engine response: %w", err)
	}

	return &plan, nil
}

// GenerateDashboardPlan asks the AI Engine's dashboard designer for a full
// dashboard proposal. Pass current != nil to refine an existing dashboard
// with a natural-language instruction instead of creating one from scratch.
func (c *AIClient) GenerateDashboardPlan(
	prompt string,
	datasets []models.DatasetMeta,
	current *models.Dashboard,
) (*models.DashboardPlan, error) {
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

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal dashboard request: %w", err)
	}

	resp, err := c.httpClient.Post(
		c.baseURL+"/api/dashboard-plan",
		"application/json",
		bytes.NewBuffer(bodyBytes),
	)
	if err != nil {
		return nil, fmt.Errorf("AI engine request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read AI engine response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("AI engine returned %d: %s", resp.StatusCode, string(respBytes))
	}

	var plan models.DashboardPlan
	if err := json.Unmarshal(respBytes, &plan); err != nil {
		return nil, fmt.Errorf("failed to parse AI engine response: %w", err)
	}

	return &plan, nil
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
	SQL     string `json:"sql"`
	Changed bool   `json:"changed"`
}

// RepairWidgetSQL asks the AI Engine to correct a widget query that failed to
// execute, given the exact engine error and the schema context. It returns the
// repaired SQL — which the caller must still re-verify against the Query
// Service, since the AI Engine never executes anything itself.
func (c *AIClient) RepairWidgetSQL(
	sql, execErr, chartType, title string,
	datasets []models.DatasetMeta,
) (string, error) {
	reqBody := RepairWidgetRequest{
		SQL:       sql,
		Error:     execErr,
		ChartType: chartType,
		Title:     title,
		Datasets:  datasets,
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal repair request: %w", err)
	}

	resp, err := c.httpClient.Post(
		c.baseURL+"/api/repair-widget",
		"application/json",
		bytes.NewBuffer(bodyBytes),
	)
	if err != nil {
		return "", fmt.Errorf("AI engine repair request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read AI engine repair response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("AI engine repair returned %d: %s", resp.StatusCode, string(respBytes))
	}

	var out repairWidgetResponse
	if err := json.Unmarshal(respBytes, &out); err != nil {
		return "", fmt.Errorf("failed to parse AI engine repair response: %w", err)
	}
	if strings.TrimSpace(out.SQL) == "" {
		return "", fmt.Errorf("AI engine repair returned empty SQL")
	}
	return out.SQL, nil
}
