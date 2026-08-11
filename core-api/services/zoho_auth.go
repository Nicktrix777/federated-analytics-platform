package services

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// exchangeZohoGrantCode performs the one-time exchange of a Zoho "Self
// Client" grant code for a long-lived refresh token. This is the only
// OAuth step core-api ever performs for Zoho Books — everything after
// this (minting short-lived access tokens from the refresh token for
// each live query) happens inside the zoho_books Trino connector plugin,
// which reads the stored refresh token directly from postgres-meta.
func exchangeZohoGrantCode(dataCenter, clientID, clientSecret, grantCode string) (string, error) {
	tokenURL := fmt.Sprintf("https://accounts.zoho.%s/oauth/v2/token", dataCenter)

	form := url.Values{}
	form.Set("grant_type", "authorization_code")
	form.Set("client_id", clientID)
	form.Set("client_secret", clientSecret)
	form.Set("code", grantCode)

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.PostForm(tokenURL, form)
	if err != nil {
		return "", fmt.Errorf("failed to reach Zoho token endpoint: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read Zoho token response: %w", err)
	}

	var parsed struct {
		RefreshToken string `json:"refresh_token"`
		Error        string `json:"error"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return "", fmt.Errorf("failed to parse Zoho token response: %w", err)
	}
	if parsed.Error != "" {
		return "", fmt.Errorf("zoho rejected the grant code: %s", parsed.Error)
	}
	if resp.StatusCode != http.StatusOK || parsed.RefreshToken == "" {
		return "", fmt.Errorf("zoho token exchange failed (status %d): no refresh_token in response — "+
			"a Self Client grant code is single-use and short-lived (~10 minutes); generate a fresh one and retry",
			resp.StatusCode)
	}
	return parsed.RefreshToken, nil
}
