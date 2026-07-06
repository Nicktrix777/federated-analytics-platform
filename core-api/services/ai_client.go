package services

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
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
