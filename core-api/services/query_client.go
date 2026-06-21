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

// QueryClient communicates with the Query Service.
//
// Architecture boundary:
//   The Query Service is the ONLY component that talks to Trino.
//   The Core API never queries data sources directly.
//   The Core API validates the plan before calling this service.

type QueryClient struct {
	baseURL    string
	httpClient *http.Client
}

func NewQueryClient(baseURL string) *QueryClient {
	return &QueryClient{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 120 * time.Second, // Federated queries can take time
		},
	}
}

// Execute sends a validated SQL query to the Query Service for execution.
func (c *QueryClient) Execute(sql string) (*models.ExecuteResponse, error) {
	reqBody := models.ExecuteRequest{SQL: sql}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal execute request: %w", err)
	}

	resp, err := c.httpClient.Post(
		c.baseURL+"/api/execute",
		"application/json",
		bytes.NewBuffer(bodyBytes),
	)
	if err != nil {
		return nil, fmt.Errorf("query service request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read query service response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("query service returned %d: %s", resp.StatusCode, string(respBytes))
	}

	var result models.ExecuteResponse
	if err := json.Unmarshal(respBytes, &result); err != nil {
		return nil, fmt.Errorf("failed to parse query service response: %w", err)
	}

	return &result, nil
}
