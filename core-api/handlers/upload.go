package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/federated-analytics/core-api/services"
)

const (
	maxUploadSize   = 20 << 20 // 20 MB
	aiEngineBaseURL = ""       // set via dependency injection
)

// UploadHandler handles CSV/Excel file uploads.
// Flow: receive file → parse → create PG table → register metadata → invalidate AI cache
type UploadHandler struct {
	uploadSvc    *services.UploadService
	aiEngineURL  string
}

func NewUploadHandler(uploadSvc *services.UploadService, aiEngineURL string) *UploadHandler {
	return &UploadHandler{
		uploadSvc:   uploadSvc,
		aiEngineURL: aiEngineURL,
	}
}

// HandleUpload processes a multipart file upload.
// POST /api/upload
// Content-Type: multipart/form-data
// Form field: "file"  (CSV or Excel file, max 20MB)
func (h *UploadHandler) HandleUpload(c *gin.Context) {
	// Limit request body size
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxUploadSize)

	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"error":   "Failed to read uploaded file",
			"details": err.Error(),
		})
		return
	}
	defer file.Close()

	filename := header.Filename
	ext := strings.ToLower(filepath.Ext(filename))

	// Validate file type
	if ext != ".csv" && ext != ".xlsx" && ext != ".xls" {
		c.JSON(http.StatusBadRequest, gin.H{
			"error":   "Unsupported file type",
			"details": fmt.Sprintf("Got '%s'. Only .csv and .xlsx/.xls files are supported.", ext),
		})
		return
	}

	// For Excel files, return a helpful message asking to save as CSV
	// (full Excel parsing requires a heavy dependency — CSV is sufficient for demo)
	if ext == ".xlsx" || ext == ".xls" {
		// Try to process as CSV anyway in case it was misnamed
		// This handles the common case where someone renames a CSV to .xlsx
		result, err := h.uploadSvc.ProcessCSV(filename, file)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{
				"error":   "Excel upload: please save your file as CSV (.csv) and re-upload",
				"details": "Tip: In Excel, use File → Save As → CSV (Comma delimited). All data and column headers will be preserved.",
				"hint":    "We support .csv files directly, or any Excel file saved as CSV.",
			})
			return
		}
		// Invalidate AI engine cache so new table appears immediately
		h.invalidateAICache()
		c.JSON(http.StatusOK, gin.H{
			"success": true,
			"message": fmt.Sprintf("Successfully uploaded '%s': %d rows, %d columns", filename, result.RowCount, len(result.Columns)),
			"data":    result,
		})
		return
	}

	// Process CSV
	result, err := h.uploadSvc.ProcessCSV(filename, file)
	if err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{
			"error":   "Failed to process CSV file",
			"details": err.Error(),
		})
		return
	}

	// Invalidate AI engine cache so new table appears in the next NL query
	h.invalidateAICache()

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": fmt.Sprintf(
			"Successfully uploaded '%s': %d rows, %d columns. Now queryable via AI!",
			filename, result.RowCount, len(result.Columns),
		),
		"data": result,
	})
}

// invalidateAICache tells the AI Engine to refresh its metadata cache
// so the newly uploaded table appears in the next NL→SQL prompt.
func (h *UploadHandler) invalidateAICache() {
	if h.aiEngineURL == "" {
		return
	}
	url := h.aiEngineURL + "/api/invalidate-cache"
	client := &http.Client{Timeout: 5 * time.Second}

	resp, err := client.Post(url, "application/json", bytes.NewBuffer([]byte("{}")))
	if err != nil {
		// Non-critical — cache will expire naturally
		return
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
}

// HandleUploadStatus returns metadata about a previously uploaded table.
// GET /api/upload/status/:table
func (h *UploadHandler) HandleUploadStatus(c *gin.Context) {
	tableName := c.Param("table")
	if tableName == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "table name required"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"table":      tableName,
		"trino_path": fmt.Sprintf("postgres_source.public.%s", tableName),
		"sample_sql": fmt.Sprintf("SELECT * FROM postgres_source.public.%s LIMIT 10", tableName),
	})
}

// jsonBody helper for internal requests
func jsonBody(v interface{}) io.Reader {
	b, _ := json.Marshal(v)
	return bytes.NewReader(b)
}
