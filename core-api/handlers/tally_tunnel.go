package handlers

import (
	"crypto/subtle"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"

	"github.com/federated-analytics/core-api/services"
)

const tallyForwardTimeout = 30 * time.Second

var tallyUpgrader = websocket.Upgrader{
	// Bridges dial in from arbitrary customer networks, not browser-origin
	// requests — this endpoint authenticates with a bearer-style bridge
	// token, not cookies, so there's no Origin check to make here.
	CheckOrigin: func(r *http.Request) bool { return true },
}

// TallyTunnelHandler exposes the two endpoints tally-bridge depends on:
// bridges connect via HandleConnect (authenticated by their per-datasource
// bridge token), and the tally Trino connector plugin calls HandleForward
// (authenticated by a shared internal service token — only Trino itself
// should ever call this) to relay one Tally XML request/response through
// whichever bridge is currently connected for that datasource.
type TallyTunnelHandler struct {
	dataSourceSvc        *services.DataSourceService
	registry             *services.TallyTunnelRegistry
	internalServiceToken string
}

func NewTallyTunnelHandler(dataSourceSvc *services.DataSourceService, registry *services.TallyTunnelRegistry, internalServiceToken string) *TallyTunnelHandler {
	return &TallyTunnelHandler{
		dataSourceSvc:        dataSourceSvc,
		registry:             registry,
		internalServiceToken: internalServiceToken,
	}
}

// GET /internal/tally-tunnel/connect?token=<bridge-token>
func (h *TallyTunnelHandler) HandleConnect(c *gin.Context) {
	token := c.Query("token")
	if token == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "token query parameter is required"})
		return
	}
	datasourceID, err := h.dataSourceSvc.FindTallyDatasourceByBridgeToken(token)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid bridge token"})
		return
	}

	conn, err := tallyUpgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		return // Upgrade already wrote its own error response
	}
	h.registry.Register(datasourceID, conn) // blocks until the bridge disconnects
}

// POST /internal/tally-tunnel/:id/forward — called only by the tally
// Trino connector plugin, never by a customer or the public frontend.
func (h *TallyTunnelHandler) HandleForward(c *gin.Context) {
	presented := c.GetHeader("X-Internal-Service-Token")
	if presented != "" && subtle.ConstantTimeCompare([]byte(presented), []byte(h.internalServiceToken)) == 1 {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid datasource id"})
			return
		}
		body, err := io.ReadAll(c.Request.Body)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "failed to read request body"})
			return
		}

		responseBody, err := h.registry.Forward(id, string(body), tallyForwardTimeout)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"error": err.Error()})
			return
		}
		c.Data(http.StatusOK, "application/xml", []byte(responseBody))
		return
	}
	c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid internal service token"})
}
