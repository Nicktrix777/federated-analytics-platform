package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/services"
)

// SSE plumbing for the streaming endpoints (docs/sse-events.md).
//
// The Core API proxies the AI Engine's pipeline events verbatim, consumes the
// AI Engine's terminal event, then appends its own execution/verification
// events. Every payload the Core API emits itself is stamped with elapsed_ms
// (ms since this handler started) and request_id, per the contract.

// sseStream wraps a gin response configured for Server-Sent Events.
type sseStream struct {
	writer    gin.ResponseWriter
	start     time.Time
	requestID string
}

// newSSEStream switches the response into SSE mode and flushes the headers.
// After this point errors must be reported as terminal `error` events, never
// as JSON status responses.
func newSSEStream(c *gin.Context, requestID string) *sseStream {
	h := c.Writer.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no") // disable nginx proxy buffering
	c.Writer.WriteHeader(http.StatusOK)
	c.Writer.Flush()

	return &sseStream{
		writer:    c.Writer,
		start:     time.Now(),
		requestID: requestID,
	}
}

// forward writes an upstream event through verbatim — same event name, same
// (already-JSON) data payload — and flushes so the client sees it immediately.
func (s *sseStream) forward(name, data string) {
	fmt.Fprintf(s.writer, "event: %s\ndata: %s\n\n", name, data)
	s.writer.Flush()
}

// heartbeat relays a comment frame so proxies keep the connection open.
// Clients ignore these per the contract.
func (s *sseStream) heartbeat() {
	fmt.Fprint(s.writer, ": heartbeat\n\n")
	s.writer.Flush()
}

// emit sends a Core API event, stamping elapsed_ms and request_id into the
// JSON payload. payload must marshal to a JSON object.
func (s *sseStream) emit(name string, payload interface{}) {
	body, err := json.Marshal(payload)
	if err != nil {
		return
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		s.forward(name, string(body))
		return
	}
	fields["elapsed_ms"], _ = json.Marshal(time.Since(s.start).Milliseconds())
	if s.requestID != "" {
		fields["request_id"], _ = json.Marshal(s.requestID)
	}
	data, err := json.Marshal(fields)
	if err != nil {
		return
	}
	s.forward(name, string(data))
}

// emitError sends the terminal error event. The caller must end the stream
// (return from the handler) immediately afterwards.
func (s *sseStream) emitError(detail string, statusCode int) {
	s.emit("error", gin.H{"detail": detail, "status_code": statusCode})
}

// upstreamError mirrors the AI Engine's terminal error payload.
type upstreamError struct {
	Detail     string `json:"detail"`
	StatusCode int    `json:"status_code"`
}

// proxyAIStream runs stream (an AIClient streaming call), forwarding every AI
// Engine event verbatim except the terminal ones: terminalEvent is consumed
// and its raw data returned; an upstream `error` event (or transport failure,
// or a stream that ends with no terminal event) is converted into this
// stream's own terminal error event, in which case ok=false and the handler
// must end the stream.
func proxyAIStream(
	sse *sseStream,
	terminalEvent string,
	stream func(onEvent func(services.SSEEvent) error) error,
) (data []byte, ok bool) {
	var terminal []byte
	var upErr *upstreamError

	err := stream(func(ev services.SSEEvent) error {
		switch {
		case ev.Comment:
			sse.heartbeat()
		case ev.Name == terminalEvent:
			terminal = []byte(ev.Data)
		case ev.Name == "error":
			var e upstreamError
			if json.Unmarshal([]byte(ev.Data), &e) != nil || e.Detail == "" {
				e.Detail = ev.Data
			}
			if e.StatusCode == 0 {
				e.StatusCode = http.StatusBadGateway
			}
			upErr = &e
		default:
			// Forward everything else verbatim — including event types this
			// version doesn't know about (forward-compatibility).
			sse.forward(ev.Name, ev.Data)
		}
		return nil
	})

	switch {
	case err != nil:
		sse.emitError("AI Engine stream failed: "+err.Error(), http.StatusBadGateway)
	case upErr != nil:
		sse.emitError(upErr.Detail, upErr.StatusCode)
	case terminal == nil:
		sse.emitError(
			fmt.Sprintf("AI Engine stream ended without a terminal %s event", terminalEvent),
			http.StatusBadGateway,
		)
	default:
		return terminal, true
	}
	return nil, false
}
