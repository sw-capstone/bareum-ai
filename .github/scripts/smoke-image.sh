#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
: "${SMOKE_IMAGE:?Set the locally built ARM64 image.}"
export SMOKE_IMAGE
[[ "$(docker image inspect "$SMOKE_IMAGE" --format '{{.Os}}/{{.Architecture}}')" == linux/arm64 ]]
project="bareum-ai-smoke-${GITHUB_RUN_ID:-local}-$$"
compose=(docker compose -p "$project" -f .github/compose.smoke.yml)
cleanup() {
  status=$?
  if (( status != 0 )); then "${compose[@]}" logs --no-color --tail 100 >&2 || true; fi
  "${compose[@]}" down --volumes --remove-orphans || true
  exit "$status"
}
trap cleanup EXIT
"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait --wait-timeout 120 --no-build
address=$("${compose[@]}" port ai 8000)
curl --fail --silent --show-error --max-time 10 "http://$address/health" | jq -e '.status == "ok"'
printf 'PASS: ARM64 AI startup and health response\n'
