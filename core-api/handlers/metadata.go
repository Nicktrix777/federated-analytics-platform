package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/services"
)

type MetadataHandler struct {
	metadataSvc *services.MetadataService
}

func NewMetadataHandler(metadataSvc *services.MetadataService) *MetadataHandler {
	return &MetadataHandler{metadataSvc: metadataSvc}
}

// HandleDatasets returns all registered datasets and their column metadata.
// Frontend uses this to show users what data sources are available.
func (h *MetadataHandler) HandleDatasets(c *gin.Context) {
	datasets, err := h.metadataSvc.GetAllDatasets()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"datasets": datasets})
}
