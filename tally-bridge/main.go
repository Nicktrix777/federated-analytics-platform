// tally-bridge is a small standalone agent a customer runs on or near
// their Tally server. It dials outbound to core-api's tally-tunnel
// WebSocket endpoint (no inbound port ever needs to be opened on the
// customer's network) and relays each Tally XML request it receives to
// the local Tally instance, returning the response back through the same
// connection. It does no data translation or storage of its own — all
// protocol/business logic lives in the tally Trino connector plugin on
// the platform side; this is a pure network bridge.
//
// Cross-compiles to a single static binary for any OS/arch Go supports —
// e.g. for a Windows server (Tally is usually Windows-hosted):
//
//	GOOS=windows GOARCH=amd64 go build -o tally-bridge.exe .
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type forwardRequest struct {
	ID   string `json:"id"`
	Body string `json:"body"`
}

type forwardResponse struct {
	ID    string `json:"id"`
	Body  string `json:"body,omitempty"`
	Error string `json:"error,omitempty"`
}

func main() {
	serverURL := flag.String("server", "", "core-api base URL, e.g. https://your-instance.example.com")
	token := flag.String("token", "", "bridge token shown once when the Tally datasource was created")
	tallyURL := flag.String("tally", "http://localhost:9000", "local Tally XML/HTTP endpoint")
	reconnectDelay := flag.Duration("reconnect-delay", 5*time.Second, "how long to wait before reconnecting after a dropped connection")
	flag.Parse()

	if *serverURL == "" || *token == "" {
		log.Fatal("both -server and -token are required")
	}

	for {
		if err := connectAndServe(*serverURL, *token, *tallyURL); err != nil {
			log.Printf("connection lost: %v — reconnecting in %s", err, *reconnectDelay)
		}
		time.Sleep(*reconnectDelay)
	}
}

func connectAndServe(serverURL, token, tallyURL string) error {
	wsURL, err := tunnelURL(serverURL, token)
	if err != nil {
		return err
	}

	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer conn.Close()
	log.Printf("connected — forwarding requests to %s", tallyURL)

	var writeMu sync.Mutex // gorilla/websocket connections must not be written from multiple goroutines concurrently
	send := func(resp forwardResponse) {
		payload, err := json.Marshal(resp)
		if err != nil {
			log.Printf("failed to encode response for request %s: %v", resp.ID, err)
			return
		}
		writeMu.Lock()
		err = conn.WriteMessage(websocket.TextMessage, payload)
		writeMu.Unlock()
		if err != nil {
			log.Printf("failed to send response for request %s: %v", resp.ID, err)
		}
	}

	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("connection closed: %w", err)
		}
		var req forwardRequest
		if err := json.Unmarshal(data, &req); err != nil {
			log.Printf("received malformed request: %v", err)
			continue
		}
		// Each request is handled concurrently so one slow Tally report
		// doesn't block others queued behind it on the same connection.
		go func(req forwardRequest) {
			resp := forwardResponse{ID: req.ID}
			body, err := callTally(tallyURL, req.Body)
			if err != nil {
				resp.Error = err.Error()
			} else {
				resp.Body = body
			}
			send(resp)
		}(req)
	}
}

func callTally(tallyURL, requestXML string) (string, error) {
	client := &http.Client{Timeout: 20 * time.Second}
	resp, err := client.Post(tallyURL, "application/xml", strings.NewReader(requestXML))
	if err != nil {
		return "", fmt.Errorf("failed to reach local Tally at %s: %w", tallyURL, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read Tally response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Tally returned status %d: %s", resp.StatusCode, string(body))
	}
	return string(body), nil
}

func tunnelURL(serverURL, token string) (string, error) {
	u, err := url.Parse(serverURL)
	if err != nil {
		return "", fmt.Errorf("invalid -server URL %q: %w", serverURL, err)
	}
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	case "http":
		u.Scheme = "ws"
	default:
		return "", fmt.Errorf("-server URL must start with http:// or https://, got %q", serverURL)
	}
	u.Path = "/internal/tally-tunnel/connect"
	q := u.Query()
	q.Set("token", token)
	u.RawQuery = q.Encode()
	return u.String(), nil
}
