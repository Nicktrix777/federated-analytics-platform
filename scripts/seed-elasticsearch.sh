#!/usr/bin/env bash
# ============================================================
# Federated Analytics Platform — Elasticsearch Synthetic Data Seeder
#
# Creates a sample employee_profiles index and seeds it with
# synthetic data that can be joined with the PostgreSQL employees table.
#
# Usage:
#   ./scripts/seed-elasticsearch.sh
#
# Requirements:
#   - Elasticsearch running at localhost:9200
#   - curl installed
# ============================================================

set -e

ES_HOST="${ELASTICSEARCH_HOST:-localhost}"
ES_PORT="${ELASTICSEARCH_PORT:-9200}"
ES_BASE="http://${ES_HOST}:${ES_PORT}"

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

# ── Delete existing index (for clean reseed) ─────────────────
curl -sf -X DELETE "${ES_BASE}/employee_profiles" > /dev/null 2>&1 || true

# ── Create employee_profiles index with explicit mapping ──────
echo "📐 Creating employee_profiles index..."
curl -sf -X PUT "${ES_BASE}/employee_profiles" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "employee_id":       { "type": "keyword" },
        "remote_preference": { "type": "keyword" },
        "skills":            { "type": "keyword" },
        "certifications":    { "type": "keyword" },
        "years_experience":  { "type": "integer" },
        "education_level":   { "type": "keyword" },
        "linkedin_url":      { "type": "keyword" },
        "location_city":     { "type": "keyword" },
        "timezone":          { "type": "keyword" },
        "availability":      { "type": "keyword" },
        "profile_score":     { "type": "float" },
        "last_updated":      { "type": "date" }
      }
    }
  }' > /dev/null
echo "✅ Index created"

# ── Seed employee profiles ────────────────────────────────────
echo "📦 Seeding employee profiles..."

REMOTE_PREFS=("fully_remote" "hybrid_2_days" "hybrid_3_days" "office_first" "flexible")
EDUCATION=("bachelor" "master" "phd" "bootcamp" "self_taught")
AVAILABILITY=("immediately" "2_weeks" "1_month" "not_looking")
CITIES=("Bangalore" "Mumbai" "Hyderabad" "Chennai" "Pune" "Delhi" "Kolkata")
TIMEZONES=("IST" "IST" "IST" "UTC+5:30")

SKILLS_POOL=(
  "Python" "Java" "Go" "TypeScript" "React" "Node.js" "PostgreSQL"
  "MongoDB" "Elasticsearch" "Redis" "Kafka" "Docker" "Kubernetes"
  "AWS" "GCP" "Azure" "TensorFlow" "PyTorch" "Spark" "Airflow"
  "FastAPI" "Spring Boot" "GraphQL" "REST" "gRPC" "Terraform"
  "Prometheus" "Grafana" "ELK Stack" "Pandas" "NumPy" "Scikit-learn"
)

CERTS_POOL=(
  "AWS Solutions Architect" "Google Cloud Professional" "CKA" "CKAD"
  "Azure Administrator" "Terraform Associate" "MongoDB Professional"
  "Elasticsearch Engineer" "PMP" "Scrum Master"
)

# Bulk indexing
BULK_BODY=""
for i in $(seq 1 100); do
  REMOTE_PREF=${REMOTE_PREFS[$((RANDOM % 5))]}
  EDU=${EDUCATION[$((RANDOM % 5))]}
  AVAIL=${AVAILABILITY[$((RANDOM % 4))]}
  CITY=${CITIES[$((RANDOM % 7))]}
  TZ=${TIMEZONES[$((RANDOM % 4))]}
  YEARS=$((RANDOM % 15 + 1))
  SCORE=$(echo "scale=1; ($RANDOM % 50 + 50) / 10" | bc)
  YEAR=$((RANDOM % 3 + 2022))
  MONTH=$((RANDOM % 12 + 1))

  # Pick 3-6 random skills
  N_SKILLS=$((RANDOM % 4 + 3))
  SKILLS_JSON="["
  for j in $(seq 1 $N_SKILLS); do
    SKILL=${SKILLS_POOL[$((RANDOM % ${#SKILLS_POOL[@]}))]}
    SKILLS_JSON+="\"${SKILL}\""
    [ $j -lt $N_SKILLS ] && SKILLS_JSON+=","
  done
  SKILLS_JSON+="]"

  # Maybe add 0-2 certs
  N_CERTS=$((RANDOM % 3))
  CERTS_JSON="["
  for k in $(seq 1 $N_CERTS); do
    CERT=${CERTS_POOL[$((RANDOM % ${#CERTS_POOL[@]}))]}
    CERTS_JSON+="\"${CERT}\""
    [ $k -lt $N_CERTS ] && CERTS_JSON+=","
  done
  CERTS_JSON+="]"

  BULK_BODY+='{"index":{"_id":"'$i'"}}'$'\n'
  BULK_BODY+='{"employee_id":"'$i'","remote_preference":"'$REMOTE_PREF'","skills":'$SKILLS_JSON',"certifications":'$CERTS_JSON',"years_experience":'$YEARS',"education_level":"'$EDU'","linkedin_url":"https://linkedin.com/in/employee-'$i'","location_city":"'$CITY'","timezone":"'$TZ'","availability":"'$AVAIL'","profile_score":'$SCORE',"last_updated":"'$YEAR'-'$(printf "%02d" $MONTH)'-01"}'$'\n'
done

echo "$BULK_BODY" | curl -sf -X POST "${ES_BASE}/employee_profiles/_bulk" \
  -H "Content-Type: application/x-ndjson" \
  --data-binary @- > /dev/null

# ── Verify ───────────────────────────────────────────────────
COUNT=$(curl -sf "${ES_BASE}/employee_profiles/_count" | grep -o '"count":[0-9]*' | cut -d: -f2)
echo "✅ Seeded ${COUNT} employee profiles into Elasticsearch"

echo ""
echo "🎉 Elasticsearch seeding complete!"
echo ""
echo "Sample Trino query to verify:"
echo "  SELECT * FROM elasticsearch.default.employee_profiles LIMIT 5"
echo ""
echo "Cross-source join example:"
echo "  SELECT e.first_name, e.department, ep.remote_preference, ep.skills"
echo "  FROM postgres_source.public.employees e"
echo "  JOIN elasticsearch.default.employee_profiles ep"
echo "    ON CAST(ep.employee_id AS INTEGER) = e.employee_id"
echo "  WHERE contains(ep.skills, 'Elasticsearch')"
echo "  LIMIT 20"
