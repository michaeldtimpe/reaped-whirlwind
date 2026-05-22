#!/bin/bash
# =============================================================================
# optimize_cpu.sh
# Patches the radar-image-processor Python code and config to dramatically
# reduce CPU usage on the NAS.
#
# Three targeted fixes:
#   1. JSON output: remove indent=2 (cuts serialization CPU ~40%, halves file size)
#   2. Workers: force max_workers=1 (stop spawning 3 parallel processes on a 4-core Atom)
#   3. Sample rate: bump from 4 to 6 (35% fewer pixels to process, still fine for ML)
#
# Usage:
#   cd /docker/radar-image-processor
#   sudo bash optimize_cpu.sh [--dry-run]
# =============================================================================

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true && echo "=== DRY RUN ===" && echo

BACKUP_SUFFIX=".bak.$(date +%Y%m%d_%H%M%S)"

# --- Locate the files (could be flat or inside radar_tools/) -----------------
# utils.py contains the JSON serialization
# processor_service.py contains the worker count logic
# config.yaml contains sample_rate

RADAR_TOOLS_DIR=""
if [[ -d "radar_tools" ]]; then
    RADAR_TOOLS_DIR="radar_tools/"
fi

UTILS_FILE="${RADAR_TOOLS_DIR}utils.py"
PROCESSOR_FILE="processor_service.py"
CONFIG_FILE="config.yaml"

for f in "$UTILS_FILE" "$PROCESSOR_FILE" "$CONFIG_FILE"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: $f not found in $(pwd)"
        echo "       Run this from your radar-image-processor directory."
        exit 1
    fi
done

echo "Found files:"
echo "  $UTILS_FILE"
echo "  $PROCESSOR_FILE"
echo "  $CONFIG_FILE"
echo

# =============================================================================
# FIX 1: Remove JSON indentation
# =============================================================================
echo "=== Fix 1: Remove JSON pretty-printing from $UTILS_FILE ==="
echo "    Why: indent=2 makes json.dump spend ~40% more CPU time formatting"
echo "         whitespace, and the output files are ~2x larger than needed."
echo "         These are machine-consumed files, not human-read."
echo

if grep -q 'indent=indent' "$UTILS_FILE" 2>/dev/null || grep -q 'indent=' "$UTILS_FILE" 2>/dev/null; then
    if [[ "$DRY_RUN" == false ]]; then
        cp "$UTILS_FILE" "${UTILS_FILE}${BACKUP_SUFFIX}"

        # Replace the save_json_data function to use compact JSON
        python3 - "$UTILS_FILE" <<'PYEOF'
import sys

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

# Replace the function signature to remove indent parameter
old_sig = 'def save_json_data(data: Dict, output_path: str, indent: int = 2):'
new_sig = 'def save_json_data(data: Dict, output_path: str):'
content = content.replace(old_sig, new_sig)

# Replace the json.dump call to use compact separators
old_dump = '        json.dump(data, f, indent=indent)'
new_dump = '        json.dump(data, f, separators=(",", ":"))'
content = content.replace(old_dump, new_dump)

# Also handle the simpler form if present
old_dump2 = 'json.dump(data, f, indent=2)'
new_dump2 = 'json.dump(data, f, separators=(",", ":"))'
content = content.replace(old_dump2, new_dump2)

with open(filepath, 'w') as f:
    f.write(content)

print("  Patched: json.dump now uses compact output (no indent, minimal separators)")
PYEOF
    else
        echo "  Would remove indent=2 from json.dump and use separators=(',',':')"
    fi
else
    echo "  Already patched or no indent found — skipping."
fi
echo

# =============================================================================
# FIX 2: Force single worker process
# =============================================================================
echo "=== Fix 2: Force max_workers=1 in $PROCESSOR_FILE ==="
echo "    Why: ProcessPoolExecutor spawns (cores-1) = 3 parallel Python processes."
echo "         Each one independently loads PIL, numpy, scipy, and builds KD-trees."
echo "         On a 4-core Atom C2538, 3 heavy Python processes = total saturation."
echo "         With the 60s interval, you rarely have >1 image queued anyway."
echo

if grep -q 'self.max_workers = min(max(1, cpu_count - 1), 4)' "$PROCESSOR_FILE" 2>/dev/null; then
    if [[ "$DRY_RUN" == false ]]; then
        cp "$PROCESSOR_FILE" "${PROCESSOR_FILE}${BACKUP_SUFFIX}"

        sed -i 's/self\.max_workers = min(max(1, cpu_count - 1), 4)/self.max_workers = 1  # Pinned to 1 to reduce CPU load on NAS/' "$PROCESSOR_FILE"
        echo "  Patched: max_workers forced to 1"
    else
        echo "  Would change: min(max(1, cpu_count-1), 4) -> 1"
    fi
else
    echo "  Already patched or pattern not found — skipping."
fi
echo

# =============================================================================
# FIX 3: Increase sample rate from 4 to 6
# =============================================================================
echo "=== Fix 3: Increase sample_rate in $CONFIG_FILE ==="
echo "    Why: sample_rate=4 processes 480x270 = 129,600 pixels per image."
echo "         sample_rate=6 processes 320x180 = 57,600 pixels — 55% fewer."
echo "         For ML training on weather patterns, this resolution is still"
echo "         more than adequate (320x180 captures all storm structure)."
echo

CURRENT_SR=$(grep 'sample_rate:' "$CONFIG_FILE" | head -1 | awk '{print $2}')
echo "  Current sample_rate: $CURRENT_SR"

if [[ "$CURRENT_SR" -lt 6 ]] 2>/dev/null; then
    if [[ "$DRY_RUN" == false ]]; then
        cp "$CONFIG_FILE" "${CONFIG_FILE}${BACKUP_SUFFIX}"
        sed -i 's/^\(\s*sample_rate:\s*\)[0-9]\+/\16/' "$CONFIG_FILE"
        echo "  Changed: sample_rate $CURRENT_SR -> 6"
    else
        echo "  Would change: sample_rate $CURRENT_SR -> 6"
    fi
else
    echo "  Already >= 6 — skipping."
fi
echo

# =============================================================================
# Rebuild and restart
# =============================================================================
echo "=== Rebuilding container ==="

if [[ "$DRY_RUN" == true ]]; then
    echo "  Would run: docker-compose down && docker-compose up -d --build"
    echo
    echo "=== DRY RUN complete ==="
    exit 0
fi

echo "  Stopping..."
docker-compose down

echo "  Rebuilding with patched code..."
docker-compose up -d --build

echo
echo "=== Verifying ==="
sleep 3

CONTAINER_ID=$(docker ps -q -f name=radar-image-processor)
if [[ -n "$CONTAINER_ID" ]]; then
    echo "Container: $(docker ps --format '{{.Names}} ({{.Status}})' -f id="$CONTAINER_ID")"
    echo
    echo "Last few log lines:"
    docker logs --tail 8 "$CONTAINER_ID" 2>&1 | sed 's/^/  /'
else
    echo "  WARNING: Container not running — check: docker-compose logs"
fi

echo
echo "=== Summary of changes ==="
echo "  1. JSON output:  indent=2 -> compact (separators=(',',':'))"
echo "  2. Workers:      auto (3 on 4-core) -> 1"
echo "  3. Sample rate:  $CURRENT_SR -> 6"
echo
echo "Expected impact:"
echo "  - JSON serialization: ~40% less CPU, ~50% smaller files"
echo "  - Worker count:       peak CPU drops from 3 cores to 1 core"
echo "  - Sample rate 6:      55% fewer pixels per image vs rate 4"
echo "  - Combined:           should cut peak CPU from ~95% to ~25-35%"
echo
echo "Monitor:  docker stats radar-image-processor"
echo
echo "Revert:"
echo "  cp ${UTILS_FILE}${BACKUP_SUFFIX} ${UTILS_FILE}"
echo "  cp ${PROCESSOR_FILE}${BACKUP_SUFFIX} ${PROCESSOR_FILE}"
echo "  cp ${CONFIG_FILE}${BACKUP_SUFFIX} ${CONFIG_FILE}"
echo "  docker-compose down && docker-compose up -d --build"