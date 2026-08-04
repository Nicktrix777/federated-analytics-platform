package handlers

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// ConversationsHandler serves the sidebar "Chats" tab (PR7): the list of
// conversations and the turns of a single conversation for transcript
// hydration. Distinct from HistoryHandler, which lists audit_logs.
type ConversationsHandler struct {
	convoSvc *services.ConversationService
}

func NewConversationsHandler(convoSvc *services.ConversationService) *ConversationsHandler {
	return &ConversationsHandler{convoSvc: convoSvc}
}

// TurnResponse is one hydrated turn. Result rows are deliberately NOT persisted
// (minimum-persistence, §14) — a plan turn carries enough to re-render the plan
// card and a "run again to view" note; a clarification turn carries the
// clarification so its option buttons re-render.
type TurnResponse struct {
	ID            int64                 `json:"id"`
	Question      string                `json:"question"`
	Kind          string                `json:"kind"` // "plan" | "clarification"
	SQL           string                `json:"sql,omitempty"`
	RowCount      int                   `json:"row_count"`
	Confidence    float64               `json:"confidence,omitempty"`
	Clarification *models.Clarification `json:"clarification,omitempty"`
	CreatedAt     time.Time             `json:"created_at"`
}

// HandleList — GET /api/conversations?limit=30
func (h *ConversationsHandler) HandleList(c *gin.Context) {
	limit := 30
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 100 {
			limit = parsed
		}
	}

	convos, err := h.convoSvc.ListConversations(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to list conversations: " + err.Error()})
		return
	}
	if convos == nil {
		convos = []services.ConversationSummary{}
	}
	c.JSON(http.StatusOK, gin.H{"conversations": convos, "count": len(convos)})
}

// HandleTurns — GET /api/conversations/:id/turns
func (h *ConversationsHandler) HandleTurns(c *gin.Context) {
	id := c.Param("id")
	if !services.ValidConversationID(id) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid conversation id"})
		return
	}

	turns, err := h.convoSvc.ListTurns(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to list turns: " + err.Error()})
		return
	}

	out := make([]TurnResponse, 0, len(turns))
	for _, t := range turns {
		tr := TurnResponse{
			ID:        t.ID,
			Question:  t.Question,
			Kind:      t.Kind,
			SQL:       t.SQL,
			RowCount:  t.RowCount,
			CreatedAt: t.CreatedAt,
		}
		switch t.Kind {
		case "clarification":
			var clar models.Clarification
			if t.Payload != nil && json.Unmarshal(t.Payload, &clar) == nil {
				tr.Clarification = &clar
			}
		default: // "plan" (and legacy)
			if t.Payload != nil {
				var p struct {
					Confidence float64 `json:"confidence"`
				}
				if json.Unmarshal(t.Payload, &p) == nil {
					tr.Confidence = p.Confidence
				}
			}
		}
		out = append(out, tr)
	}

	c.JSON(http.StatusOK, gin.H{"turns": out, "count": len(out)})
}
