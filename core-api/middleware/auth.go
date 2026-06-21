package middleware

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

// Auth is a stub OIDC/LDAP middleware for the POC.
// In production, this would validate a JWT against an OIDC IdP
// or verify LDAP credentials. For now, it checks a static bearer token.
//
// Architecture note: This boundary is intentional — even in the POC
// we enforce that all requests must be authenticated before reaching
// any business logic handler.
func Auth(token string) gin.HandlerFunc {
	return func(c *gin.Context) {
		// Skip auth for health check
		if c.Request.URL.Path == "/api/health" {
			c.Next()
			return
		}

		authHeader := c.GetHeader("Authorization")
		if authHeader == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Authorization header is required",
			})
			return
		}

		parts := strings.SplitN(authHeader, " ", 2)
		if len(parts) != 2 || !strings.EqualFold(parts[0], "bearer") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Authorization header must be 'Bearer <token>'",
			})
			return
		}

		if parts[1] != token {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Invalid token",
			})
			return
		}

		// Store a sanitized user identifier for audit logging
		// In production, this would be extracted from the JWT claims
		c.Set("user_token", truncateToken(parts[1]))
		c.Next()
	}
}

// truncateToken returns a safe, loggable identifier for the token.
// We never log the full token.
func truncateToken(token string) string {
	if len(token) <= 8 {
		return "***"
	}
	return token[:4] + "***" + token[len(token)-4:]
}
