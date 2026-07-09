#!/usr/bin/env bash
# ============================================================
# Federated Analytics Platform — Elasticsearch Index Seeder
#
# Creates the contracts-v2.37 and contracts-v2.40 indices using
# the explicit mappings checked into scripts/schemas/, then loads
# 500 synthetic sample documents into each.
#
# Usage:
#   ./scripts/seed-elasticsearch.sh
#
# Requirements:
#   - Elasticsearch running at localhost:9200
#   - curl installed
#   - node installed (used to generate the sample documents)
# ============================================================

set -e

ES_HOST="${ELASTICSEARCH_HOST:-localhost}"
ES_PORT="${ELASTICSEARCH_PORT:-9200}"
ES_BASE="http://${ES_HOST}:${ES_PORT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/schemas"
GENERATOR="${SCRIPT_DIR}/generate-contract-sample-data.js"
SAMPLE_COUNT="${SAMPLE_COUNT:-500}"

INDICES=("contracts-v2.37" "contracts-v2.40")

if ! command -v node > /dev/null 2>&1; then
  echo "❌ node is required to generate sample data but was not found on PATH"
  exit 1
fi

echo "🔍 Connecting to Elasticsearch at ${ES_BASE}..."

# Wait for ES to be ready
MAX_WAIT=60
WAITED=0
until curl -sf "${ES_BASE}/_cluster/health?wait_for_status=yellow&timeout=5s" > /dev/null; do
  if [ $WAITED -ge $MAX_WAIT ]; then
    echo "❌ Elasticsearch did not become ready within ${MAX_WAIT}s"
    exit 1
  fi
  echo "⏳ Waiting for Elasticsearch... (${WAITED}s)"
  sleep 5
  WAITED=$((WAITED + 5))
done

echo "✅ Elasticsearch is ready"

for INDEX in "${INDICES[@]}"; do
  MAPPING_FILE="${SCHEMA_DIR}/${INDEX}.json"

  if [ ! -f "${MAPPING_FILE}" ]; then
    echo "❌ Mapping file not found: ${MAPPING_FILE}"
    exit 1
  fi

  # ── Delete existing index (for clean reseed) ─────────────────
  curl -sf -X DELETE "${ES_BASE}/${INDEX}" > /dev/null 2>&1 || true

  # ── Create index with explicit mapping ────────────────────────
  echo "📐 Creating ${INDEX} index..."
  curl -sf -X PUT "${ES_BASE}/${INDEX}" \
    -H "Content-Type: application/json" \
    --data-binary "@${MAPPING_FILE}" > /dev/null
  echo "✅ Index ${INDEX} created"

  # ── Seed sample documents ─────────────────────────────────────
  echo "📦 Generating ${SAMPLE_COUNT} sample documents for ${INDEX}..."
  node "${GENERATOR}" "${INDEX}" "${SAMPLE_COUNT}" \
    | curl -sf -X POST "${ES_BASE}/${INDEX}/_bulk?refresh=wait_for" \
        -H "Content-Type: application/x-ndjson" \
        --data-binary @- > /dev/null

  COUNT=$(curl -sf "${ES_BASE}/${INDEX}/_count" | grep -o '"count":[0-9]*' | cut -d: -f2)
  echo "✅ Seeded ${COUNT} documents into ${INDEX}"
done

echo ""
echo "🎉 Elasticsearch index creation complete!"
echo ""
echo "Sample Trino query to verify:"
echo "  SELECT * FROM elasticsearch.default.\"contracts-v2.37\" LIMIT 5"
echo "  SELECT * FROM elasticsearch.default.\"contracts-v2.40\" LIMIT 5"
