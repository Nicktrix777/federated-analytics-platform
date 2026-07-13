package services

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// postJSON issues a POST with a JSON-encoded payload, propagating the audit
// request ID as X-Request-ID so downstream services can correlate logs and
// events with the originating request.
func postJSON(client *http.Client, url, requestID string, payload interface{}) (*http.Response, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if requestID != "" {
		req.Header.Set("X-Request-ID", requestID)
	}
	return client.Do(req)
}

// decodeJSON drains and closes resp's body, enforces a 200 status, and
// unmarshals the body into out. service names the upstream for error messages.
func decodeJSON(resp *http.Response, service string, out interface{}) error {
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read %s response: %w", service, err)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s returned %d: %s", service, resp.StatusCode, string(respBytes))
	}
	if err := json.Unmarshal(respBytes, out); err != nil {
		return fmt.Errorf("failed to parse %s response: %w", service, err)
	}
	return nil
}

// SSEEvent is one parsed server-sent event frame from an upstream stream.
type SSEEvent struct {
	Name string // value of the "event:" field ("" if the frame had none)
	Data string // concatenated "data:" lines

	// Comment marks a comment/heartbeat frame (lines starting with ":").
	// Name and Data are empty; consumers typically relay a heartbeat of
	// their own so intermediate proxies keep the client connection open.
	Comment bool
}

// parseSSEStream reads server-sent events from r, invoking onEvent for each
// complete frame (and for each comment line, flagged with Comment=true).
// Unknown fields are ignored per the SSE spec. Returns the first error from
// onEvent or from the underlying reader.
func parseSSEStream(r io.Reader, onEvent func(SSEEvent) error) error {
	scanner := bufio.NewScanner(r)
	// Terminal plan/dashboard_plan events carry full plans; allow large lines.
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)

	var name string
	var data []string

	dispatch := func() error {
		if name == "" && len(data) == 0 {
			return nil // empty frame (e.g. consecutive blank lines)
		}
		ev := SSEEvent{Name: name, Data: strings.Join(data, "\n")}
		name, data = "", nil
		return onEvent(ev)
	}

	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case line == "":
			// Blank line terminates the current frame.
			if err := dispatch(); err != nil {
				return err
			}
		case strings.HasPrefix(line, ":"):
			if err := onEvent(SSEEvent{Comment: true}); err != nil {
				return err
			}
		case strings.HasPrefix(line, "event:"):
			name = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
		case strings.HasPrefix(line, "data:"):
			data = append(data, strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("failed to read SSE stream: %w", err)
	}
	// Flush a trailing frame not followed by a blank line.
	return dispatch()
}
