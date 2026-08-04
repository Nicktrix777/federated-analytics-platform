#!/usr/bin/env bash
# Build (if needed) and run the headless UX suite inside a self-contained Playwright
# container joined to the app's compose network, so it reaches the app by service name.
#
#   ./qa/run.sh                 # run all specs
#   ./qa/run.sh 03-query-ai     # run specs matching a grep
#
set -euo pipefail
cd "$(dirname "$0")"

NETWORK="${NETWORK:-federated-analytics-platform_federation-net}"
BASE_URL="${BASE_URL:-http://frontend}"
IMAGE="fap-qa-runner"

echo "▸ Building $IMAGE (browser+deps cached after first build)…"
docker build -q -t "$IMAGE" . >/dev/null

mkdir -p artifacts
GREP_ARG=()
if [[ $# -gt 0 ]]; then GREP_ARG=(--grep "$1"); fi

echo "▸ Running UX suite against $BASE_URL on network $NETWORK…"
docker run --rm \
  --network "$NETWORK" \
  -e BASE_URL="$BASE_URL" \
  -e CI=1 \
  -v "$(pwd)/artifacts:/qa/artifacts" \
  "$IMAGE" \
  npx playwright test "${GREP_ARG[@]}"
