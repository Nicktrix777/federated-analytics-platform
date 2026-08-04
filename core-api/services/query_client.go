package services

import (
	"fmt"
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
// requestID is propagated as X-Request-ID for cross-service correlation.
func (c *QueryClient) Execute(requestID, sql string) (*models.ExecuteResponse, error) {
	reqBody := models.ExecuteRequest{SQL: sql}

	resp, err := postJSON(c.httpClient, c.baseURL+"/api/execute", requestID, reqBody)
	if err != nil {
		return nil, fmt.Errorf("query service request failed: %w", err)
	}

	var result models.ExecuteResponse
	if err := decodeJSON(resp, "query service", &result); err != nil {
		return nil, err
	}
	return &result, nil
}
