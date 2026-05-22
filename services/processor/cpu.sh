#!/bin/bash
# fix_cpu_limits.sh — removes the unsupported "cpus" line and restarts
set -euo pipefail

COMPOSE_FILE="docker-compose.yaml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: $COMPOSE_FILE not found in $(pwd)"
    exit 1
fi

echo "=== Removing unsupported 'cpus' directive ==="

# Remove the cpus line (and its comment if on the same line)
sed -i '/^\s*cpus:/d' "$COMPOSE_FILE"

echo "  Removed cpus line from $COMPOSE_FILE"
echo
echo "  Remaining CPU limits:"
grep -E '(cpu_shares|cpuset)' "$COMPOSE_FILE" | sed 's/^/    /'
echo

echo "=== Restarting service ==="
docker-compose up -d

echo
CONTAINER_ID=$(docker ps -q -f name=radar-image-processor)
if [[ -n "$CONTAINER_ID" ]]; then
    echo "Container: $(docker ps --format '{{.Names}} ({{.Status}})' -f id="$CONTAINER_ID")"
    docker inspect "$CONTAINER_ID" --format '  CPU Shares:     {{.HostConfig.CpuShares}}' 2>/dev/null || true
    docker inspect "$CONTAINER_ID" --format '  CPUSet (cores):  {{.HostConfig.CpusetCpus}}' 2>/dev/null || true
else
    echo "  WARNING: Container not running — check logs with: docker-compose logs"
fi

echo
echo "Monitor with:  docker stats radar-image-processor"
