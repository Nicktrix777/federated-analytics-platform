package services

import (
	"regexp"
	"strings"
)

// simpleTrinoIdent matches identifiers Trino accepts unquoted.
var simpleTrinoIdent = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)

// quoteTrinoIdent double-quotes an identifier segment if it contains
// anything other than letters, digits, or underscores (e.g. the hyphens
// and dots common in Elasticsearch index names like "contracts-v2.37").
// Trino parses a bare hyphen as subtraction, so leaving it unquoted breaks
// the query rather than erroring at registration time.
func quoteTrinoIdent(ident string) string {
	if simpleTrinoIdent.MatchString(ident) {
		return ident
	}
	return `"` + strings.ReplaceAll(ident, `"`, `""`) + `"`
}

// buildTrinoPath joins catalog, schema, and table into a Trino-executable
// three-part path, quoting each segment only when required.
func buildTrinoPath(catalog, schema, table string) string {
	return quoteTrinoIdent(catalog) + "." + quoteTrinoIdent(schema) + "." + quoteTrinoIdent(table)
}
