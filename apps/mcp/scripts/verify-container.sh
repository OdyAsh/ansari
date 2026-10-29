#!/usr/bin/env bash
# One uncached root-lockfile image build and a synthetic, isolated runtime smoke check.
set -euo pipefail
app_root=$(cd "$(dirname "$0")/.." && pwd)
repo_root=$(cd "$app_root/../.." && pwd)
container=""
image="ansari-mcp-ci:$(date +%s)-$$"
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker build --no-cache -f "$app_root/Dockerfile" -t "$image" "$repo_root"
container=$(docker run -d --network none -e ANSARI_RELEASE=ci-public-smoke -e ANSARI_API_BASE_URL=http://127.0.0.1:4010 -e MCP_RESPONSE_MODE=json "$image")
[[ $(docker inspect "$container" --format '{{len .Mounts}}') == 0 ]]
docker exec -i "$container" node --input-type=module < "$app_root/scripts/docker-smoke.mjs"
docker stop --time 10 "$container" >/dev/null
[[ $(docker inspect "$container" --format '{{.State.ExitCode}}') == 0 ]]
echo 'MCP root-context container build and smoke passed.'
