package services

import (
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// tunnelForwardRequest/Response are the only two message shapes exchanged
// over a tally-bridge's WebSocket connection - Tally's own protocol is
// always a single POST of TDL-formatted XML to the bridge's local Tally
// instance, so there's nothing else to model here.
type tunnelForwardRequest struct {
	ID   string `json:"id"`
	Body string `json:"body"`
}

type tunnelForwardResponse struct {
	ID    string `json:"id"`
	Body  string `json:"body,omitempty"`
	Error string `json:"error,omitempty"`
}

// TallyTunnelRegistry holds one live WebSocket session per connected
// tally-bridge agent, keyed by data_sources.id. It's what lets the tally
// Trino connector plugin — which cannot reach a customer's network
// directly — forward a Tally XML request through core-api and get the
// response back, without ever requiring an inbound port on the customer's
// side: the bridge is always the one that dials out.
type TallyTunnelRegistry struct {
	mu       sync.Mutex
	sessions map[int]*tallyBridgeSession
}

type tallyBridgeSession struct {
	conn      *websocket.Conn
	writeMu   sync.Mutex // gorilla/websocket connections must not be written from multiple goroutines concurrently
	pendingMu sync.Mutex
	pending   map[string]chan tunnelForwardResponse
}

func NewTallyTunnelRegistry() *TallyTunnelRegistry {
	return &TallyTunnelRegistry{sessions: map[int]*tallyBridgeSession{}}
}

// Register takes ownership of conn and reads response messages from it
// until it closes, dispatching each to whichever Forward call is waiting
// on its correlation ID. Blocks until the connection closes — the caller
// runs it in its own goroutine per connected bridge.
func (r *TallyTunnelRegistry) Register(datasourceID int, conn *websocket.Conn) {
	session := &tallyBridgeSession{conn: conn, pending: map[string]chan tunnelForwardResponse{}}

	r.mu.Lock()
	if existing, ok := r.sessions[datasourceID]; ok {
		// Superseded by a newer connection from the same bridge (e.g. a
		// reconnect after a network blip) — drop the stale one.
		existing.conn.Close()
	}
	r.sessions[datasourceID] = session
	r.mu.Unlock()

	defer func() {
		r.mu.Lock()
		if r.sessions[datasourceID] == session {
			delete(r.sessions, datasourceID)
		}
		r.mu.Unlock()
		conn.Close()
	}()

	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		var resp tunnelForwardResponse
		if err := json.Unmarshal(data, &resp); err != nil {
			continue
		}
		session.pendingMu.Lock()
		ch, ok := session.pending[resp.ID]
		if ok {
			delete(session.pending, resp.ID)
		}
		session.pendingMu.Unlock()
		if ok {
			ch <- resp
		}
	}
}

// Forward sends a Tally XML request body to the bridge registered for
// datasourceID and blocks until its response arrives or timeout elapses.
func (r *TallyTunnelRegistry) Forward(datasourceID int, requestBody string, timeout time.Duration) (string, error) {
	r.mu.Lock()
	session, ok := r.sessions[datasourceID]
	r.mu.Unlock()
	if !ok {
		return "", fmt.Errorf("no tally-bridge is currently connected for datasource %d", datasourceID)
	}

	correlationID := fmt.Sprintf("%d-%d", datasourceID, time.Now().UnixNano())
	ch := make(chan tunnelForwardResponse, 1)
	session.pendingMu.Lock()
	session.pending[correlationID] = ch
	session.pendingMu.Unlock()

	payload, err := json.Marshal(tunnelForwardRequest{ID: correlationID, Body: requestBody})
	if err != nil {
		return "", fmt.Errorf("failed to encode tunnel request: %w", err)
	}

	session.writeMu.Lock()
	err = session.conn.WriteMessage(websocket.TextMessage, payload)
	session.writeMu.Unlock()
	if err != nil {
		session.pendingMu.Lock()
		delete(session.pending, correlationID)
		session.pendingMu.Unlock()
		return "", fmt.Errorf("failed to send request over tally-bridge tunnel: %w", err)
	}

	select {
	case resp := <-ch:
		if resp.Error != "" {
			return "", fmt.Errorf("tally-bridge reported an error: %s", resp.Error)
		}
		return resp.Body, nil
	case <-time.After(timeout):
		session.pendingMu.Lock()
		delete(session.pending, correlationID)
		session.pendingMu.Unlock()
		return "", fmt.Errorf("timed out waiting for tally-bridge response after %s", timeout)
	}
}
