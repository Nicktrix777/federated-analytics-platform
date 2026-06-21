package models

// ExecuteRequest is received from the Core API.
// It contains a pre-validated SQL query to execute against Trino.
type ExecuteRequest struct {
	SQL string `json:"sql" binding:"required"`
}

// ExecuteResponse is returned to the Core API.
type ExecuteResponse struct {
	Columns         []string        `json:"columns"`
	Rows            [][]interface{} `json:"rows"`
	RowCount        int             `json:"row_count"`
	ExecutionTimeMs int64           `json:"execution_time_ms"`
}

// ErrorResponse is returned on failure.
type ErrorResponse struct {
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}
